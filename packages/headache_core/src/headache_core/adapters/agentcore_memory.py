"""AgentCore Memory como `CheckpointStore` (corto plazo) y `PreferenceStore` (largo plazo).

- Hilos: cada guardado es un evento `blob` con la instantánea completa del hilo (el resumen y la
  compactación son nuestros, así que no basta con eventos conversacionales sueltos). Se lee el
  evento de mayor versión. El `actorId` es la aplicación y el `sessionId` se deriva del hilo; el
  dueño viaja en la instantánea y lo comprueba `load_thread`.
- Concurrencia optimista: el `clientToken` de `CreateEvent` es `hilo:versión`. Dos turnos que
  partan de la misma versión mandan el mismo token y el servicio solo acepta uno; además se
  relee la última versión antes de escribir.
- Preferencias: registros de memoria a largo plazo en `/preferences/{cliente}/`, escritos al
  momento con `BatchCreateMemoryRecords` (la tool `remember_preference` es explícita; las
  estrategias de extracción asíncronas de AgentCore serían la alternativa implícita).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from headache_core.ports.llm import ChatMessage, Role, ToolCall
from headache_core.ports.memory import CheckpointConflictError, ThreadState

if TYPE_CHECKING:
    from mypy_boto3_bedrock_agentcore import BedrockAgentCoreClient
    from mypy_boto3_bedrock_agentcore.type_defs import EventTypeDef

APP_ACTOR = "headache-booking-agent"
VERSION_KEY = "version"


def thread_document(state: ThreadState) -> dict[str, Any]:
    return {
        "thread_id": state.thread_id,
        "customer_id": state.customer_id,
        "summary": state.summary,
        "messages": [
            {
                "role": m.role.value,
                "content": m.content,
                "tool_calls": [asdict(c) for c in m.tool_calls],
                "tool_call_id": m.tool_call_id,
                "is_error": m.is_error,
            }
            for m in state.messages
        ],
    }


def thread_from_document(document: dict[str, Any], version: int) -> ThreadState:
    messages = tuple(
        ChatMessage(
            Role(m["role"]),
            m["content"],
            tool_calls=tuple(ToolCall(**c) for c in m["tool_calls"]),
            tool_call_id=m["tool_call_id"],
            is_error=m["is_error"],
        )
        for m in document["messages"]
    )
    return ThreadState(
        thread_id=document["thread_id"],
        customer_id=document["customer_id"],
        messages=messages,
        summary=document["summary"],
        version=version,
    )


def session_id(thread_id: str) -> str:
    """El `sessionId` de AgentCore solo admite `[a-zA-Z0-9][a-zA-Z0-9-_]*`; el hilo es libre."""
    return f"thread-{hashlib.sha256(thread_id.encode()).hexdigest()[:40]}"


def _version(event: EventTypeDef) -> int:
    value = event.get("metadata", {}).get(VERSION_KEY, {}).get("stringValue", "0")
    return int(value)


class AgentCoreCheckpointStore:
    def __init__(
        self,
        client: BedrockAgentCoreClient,
        memory_id: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._memory_id = memory_id
        self._clock = clock

    async def _latest(self, thread_id: str) -> EventTypeDef | None:
        latest: EventTypeDef | None = None
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"nextToken": token} if token else {}
            page = await asyncio.to_thread(
                self._client.list_events,
                memoryId=self._memory_id,
                sessionId=session_id(thread_id),
                actorId=APP_ACTOR,
                includePayloads=True,
                **kwargs,
            )
            for event in page["events"]:
                if latest is None or _version(event) > _version(latest):
                    latest = event
            token = page.get("nextToken")
            if not token:
                return latest

    async def load(self, thread_id: str) -> ThreadState | None:
        event = await self._latest(thread_id)
        if event is None:
            return None
        blob = next(p["blob"] for p in event["payload"] if "blob" in p)
        return thread_from_document(blob, _version(event))

    async def save(self, state: ThreadState, *, expected_version: int) -> ThreadState:
        latest = await self._latest(state.thread_id)
        if (_version(latest) if latest else 0) != expected_version:
            raise CheckpointConflictError(f"El hilo {state.thread_id} ha cambiado.")
        version = expected_version + 1
        session = session_id(state.thread_id)
        await asyncio.to_thread(
            self._client.create_event,
            memoryId=self._memory_id,
            actorId=APP_ACTOR,
            sessionId=session,
            eventTimestamp=self._clock(),
            payload=[{"blob": thread_document(state)}],
            metadata={VERSION_KEY: {"stringValue": str(version)}},
            clientToken=f"{session}:{version}",
        )
        return ThreadState(
            thread_id=state.thread_id,
            customer_id=state.customer_id,
            messages=state.messages,
            summary=state.summary,
            version=version,
        )


def preferences_namespace(customer_id: str) -> str:
    return f"/preferences/{customer_id}/"


class AgentCorePreferenceStore:
    def __init__(
        self,
        client: BedrockAgentCoreClient,
        memory_id: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._memory_id = memory_id
        self._clock = clock

    async def get(self, customer_id: str) -> dict[str, str]:
        records: list[tuple[datetime, str, str]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"nextToken": token} if token else {}
            page = await asyncio.to_thread(
                self._client.list_memory_records,
                memoryId=self._memory_id,
                namespace=preferences_namespace(customer_id),
                **kwargs,
            )
            for record in page["memoryRecordSummaries"]:
                item = json.loads(record["content"].get("text", "{}"))
                records.append((record["createdAt"], item["key"], item["value"]))
            token = page.get("nextToken")
            if not token:
                break
        # Los registros se acumulan: gana el más reciente de cada clave.
        return {key: value for _, key, value in sorted(records, key=lambda r: r[0])}

    async def put(self, customer_id: str, key: str, value: str) -> None:
        response = await asyncio.to_thread(
            self._client.batch_create_memory_records,
            memoryId=self._memory_id,
            records=[
                {
                    "requestIdentifier": uuid4().hex,
                    "namespaces": [preferences_namespace(customer_id)],
                    "content": {"text": json.dumps({"key": key, "value": value})},
                    "timestamp": self._clock(),
                }
            ],
        )
        if response["failedRecords"]:
            raise RuntimeError(f"AgentCore Memory rechazó la preferencia: {key}")
