"""Sessions y Memory Bank (7.G3) con un doble del cliente de `agentplatform`.

El doble imita lo que se ha leído de la API instalada: `list`/`retrieve` devuelven un paginador
asíncrono, `state_delta` vuelve como JSON (`Struct`: los enteros, como `float`) y una sesión
que no existe da un error con `code = 404`. Se sustituye por respuestas reales capturadas en la
primera validación en la nube (`pendientes-cloud.md#fase-7-gcp`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from genaipocs_core.adapters.memory_bank import (
    MemoryBankPreferenceStore,
    SessionsCheckpointStore,
    build_agent_platform_memory,
    session_id,
)
from genaipocs_core.config import Settings
from genaipocs_core.ports import ChatMessage, Role, ToolCall
from genaipocs_core.ports.memory import CheckpointConflictError, ThreadState

ENGINE = "projects/p/locations/europe-west4/reasoningEngines/123"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
STATE = ThreadState(
    thread_id="t1",
    customer_id="cus_1",
    messages=(
        ChatMessage(Role.USER, "hola"),
        ChatMessage(
            Role.ASSISTANT, "", tool_calls=(ToolCall("c1", "get_order", "{}", "c2lnLTE="),)
        ),
        ChatMessage(Role.TOOL, '{"error": "x"}', tool_call_id="c1", is_error=True),
        ChatMessage(Role.ASSISTANT, "listo"),
    ),
    summary="resumen",
)


class ApiError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


async def _pager(items: Iterable[Any]) -> AsyncIterator[Any]:
    for item in list(items):
        yield item


def _as_struct(value: Any) -> Any:
    """Lo que devuelve la API: JSON con los enteros como `float` (protobuf `Struct`)."""
    return json.loads(json.dumps(value), parse_int=float)


class FakeEvents:
    def __init__(self, sessions: dict[str, list[Any]]) -> None:
        self._sessions = sessions
        self.fail_with: int | None = None

    async def append(
        self, *, name: str, author: str, invocation_id: str, timestamp: datetime, config: Any
    ) -> None:
        if name not in self._sessions:
            raise ApiError(404)
        actions = SimpleNamespace(state_delta=_as_struct(config["actions"]["state_delta"]))
        self._sessions[name].append(
            SimpleNamespace(author=author, invocation_id=invocation_id, actions=actions)
        )

    async def list(self, *, name: str, config: Any = None) -> AsyncIterator[Any]:
        if self.fail_with is not None:
            raise ApiError(self.fail_with)
        if name not in self._sessions:
            raise ApiError(404)
        return _pager(self._sessions[name])


class FakeSessions:
    def __init__(self) -> None:
        self.sessions: dict[str, list[Any]] = {}
        self.users: dict[str, str] = {}
        self._events = FakeEvents(self.sessions)

    @property
    def events(self) -> FakeEvents:
        return self._events

    async def create(self, *, name: str, user_id: str, config: Any = None) -> None:
        session = f"{name}/sessions/{config['session_id']}"
        self.sessions[session] = []
        self.users[session] = user_id


class FakeMemories:
    def __init__(self) -> None:
        self.memories: dict[str, Any] = {}
        self._next = 0

    async def create(
        self, *, name: str, fact: str, scope: dict[str, str], config: Any = None
    ) -> None:
        self._next += 1
        memory_name = f"{name}/memories/{self._next}"
        updated = NOW + timedelta(seconds=self._next)
        self.memories[memory_name] = SimpleNamespace(
            name=memory_name, fact=fact, scope=dict(scope), update_time=updated
        )

    async def retrieve(
        self, *, name: str, scope: dict[str, str], simple_retrieval_params: Any = None
    ) -> AsyncIterator[Any]:
        found = [m for m in self.memories.values() if m.scope == scope]
        return _pager(SimpleNamespace(memory=m, distance=None) for m in found)

    async def delete(self, *, name: str, config: Any = None) -> None:
        del self.memories[name]


async def test_threads_round_trip_with_versions_and_signatures() -> None:
    sessions = FakeSessions()
    store = SessionsCheckpointStore(sessions, ENGINE, clock=lambda: NOW)

    assert await store.load("t1") is None
    first = await store.save(STATE, expected_version=0)
    second = await store.save(STATE, expected_version=first.version)
    loaded = await store.load("t1")

    session = f"{ENGINE}/sessions/{session_id('t1')}"
    # Una sesión por hilo, de su cliente, y un evento por guardado.
    assert sessions.users == {session: "cus_1"}
    assert [e.invocation_id for e in sessions.sessions[session]] == [
        f"{session_id('t1')}:1",
        f"{session_id('t1')}:2",
    ]
    assert (first.version, second.version) == (1, 2)
    assert loaded is not None and loaded.version == 2
    assert loaded.messages == STATE.messages and loaded.summary == "resumen"
    # La firma de Gemini sobrevive al viaje por el servicio (7.G1).
    assert loaded.messages[1].tool_calls[0].signature == "c2lnLTE="


async def test_stale_versions_are_rejected() -> None:
    store = SessionsCheckpointStore(FakeSessions(), ENGINE, clock=lambda: NOW)
    await store.save(STATE, expected_version=0)

    with pytest.raises(CheckpointConflictError):
        await store.save(STATE, expected_version=0)


async def test_only_not_found_means_a_new_thread() -> None:
    sessions = FakeSessions()
    sessions.events.fail_with = 403
    store = SessionsCheckpointStore(sessions, ENGINE, clock=lambda: NOW)

    # Un 403 (permisos) no se confunde con un hilo vacío: se perdería la conversación.
    with pytest.raises(ApiError):
        await store.load("t1")


async def test_preferences_are_direct_memories_scoped_by_customer() -> None:
    memories = FakeMemories()
    store = MemoryBankPreferenceStore(memories, ENGINE)

    await store.put("cus_1", "language", "es")
    await store.put("cus_1", "home_city", "Madrid")
    await store.put("cus_1", "language", "en")
    await store.put("cus_2", "language", "pt")
    # Un hecho que generara la extracción automática no es una preferencia de la lista cerrada.
    extracted = "Viaja a Lisboa en noviembre"
    await memories.create(name=ENGINE, fact=extracted, scope={"customer_id": "cus_1"})

    assert await store.get("cus_1") == {"language": "en", "home_city": "Madrid"}
    assert await store.get("cus_2") == {"language": "pt"}
    # Cambiar una preferencia borra la anterior: no se acumulan versiones.
    facts = sorted(m.fact for m in memories.memories.values() if m.scope["customer_id"] == "cus_1")
    assert facts == ["Viaja a Lisboa en noviembre", "home_city=Madrid", "language=en"]


def test_cloud_composition_needs_the_engine() -> None:
    with pytest.raises(ValueError, match="GCP_AGENT_PLATFORM__MEMORY_ENGINE"):
        build_agent_platform_memory(Settings(_env_file=None, gcp={"project": "p"}))
