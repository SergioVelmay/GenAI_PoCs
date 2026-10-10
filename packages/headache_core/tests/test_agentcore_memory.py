from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from botocore.stub import ANY, Stubber

from headache_core.adapters.agentcore_memory import (
    APP_ACTOR,
    AgentCoreCheckpointStore,
    AgentCorePreferenceStore,
    preferences_namespace,
    session_id,
    thread_document,
)
from headache_core.ports import ChatMessage, Role, ToolCall
from headache_core.ports.memory import CheckpointConflictError, ThreadState

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
MEMORY = "headache_memory-a1b2c3d4e5"
SESSION = session_id("t1")
STATE = ThreadState(
    thread_id="t1",
    customer_id="cus_1",
    messages=(
        ChatMessage(Role.USER, "hola"),
        ChatMessage(Role.ASSISTANT, "", tool_calls=(ToolCall("c1", "get_order", "{}"),)),
        ChatMessage(Role.TOOL, '{"error": "x"}', tool_call_id="c1", is_error=True),
        ChatMessage(Role.ASSISTANT, "listo", cache_checkpoint=True),
    ),
    summary="resumen",
)


@pytest.fixture
def stubbed() -> Iterator[tuple[Any, Stubber]]:
    client = boto3.client(
        "bedrock-agentcore",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def _event(version: int, state: ThreadState = STATE) -> dict[str, Any]:
    return {
        "memoryId": MEMORY,
        "actorId": APP_ACTOR,
        "sessionId": SESSION,
        "eventId": f"{version}#abc",
        "eventTimestamp": NOW,
        "payload": [{"blob": thread_document(state)}],
        "metadata": {"version": {"stringValue": str(version)}},
    }


def _list_events(stubber: Stubber, *pages: list[dict[str, Any]]) -> None:
    for index, events in enumerate(pages):
        last = index == len(pages) - 1
        response: dict[str, Any] = {"events": events}
        params: dict[str, Any] = {
            "memoryId": MEMORY,
            "sessionId": SESSION,
            "actorId": APP_ACTOR,
            "includePayloads": True,
        }
        if not last:
            response["nextToken"] = f"p{index + 1}"
        if index:
            params["nextToken"] = f"p{index}"
        stubber.add_response("list_events", response, params)


async def test_checkpoint_round_trip_picks_the_latest_version_across_pages(
    stubbed: tuple[Any, Stubber],
) -> None:
    client, stubber = stubbed
    older = ThreadState(thread_id="t1", customer_id="cus_1", summary="viejo")
    _list_events(stubber, [_event(2)], [_event(1, older)])

    loaded = await AgentCoreCheckpointStore(client, MEMORY).load("t1")

    assert loaded is not None
    assert loaded.version == 2 and loaded.summary == "resumen"
    # El checkpoint de caché es del prompt, no del hilo: no se persiste.
    assert loaded.messages[-1] == ChatMessage(Role.ASSISTANT, "listo")
    assert loaded.messages[1:3] == STATE.messages[1:3]


async def test_save_writes_an_idempotent_versioned_snapshot(
    stubbed: tuple[Any, Stubber],
) -> None:
    client, stubber = stubbed
    _list_events(stubber, [_event(1)])
    stubber.add_response(
        "create_event",
        {"event": {**_event(2)}},
        {
            "memoryId": MEMORY,
            "actorId": APP_ACTOR,
            "sessionId": SESSION,
            "eventTimestamp": NOW,
            "payload": [{"blob": thread_document(STATE)}],
            "metadata": {"version": {"stringValue": "2"}},
            "clientToken": f"{SESSION}:2",
        },
    )

    saved = await AgentCoreCheckpointStore(client, MEMORY, clock=lambda: NOW).save(
        STATE, expected_version=1
    )

    assert saved.version == 2


async def test_save_rejects_a_stale_version(stubbed: tuple[Any, Stubber]) -> None:
    client, stubber = stubbed
    _list_events(stubber, [_event(3)])

    with pytest.raises(CheckpointConflictError):
        await AgentCoreCheckpointStore(client, MEMORY).save(STATE, expected_version=1)


async def test_preferences_keep_the_latest_value_per_key(stubbed: tuple[Any, Stubber]) -> None:
    client, stubber = stubbed

    def record(key: str, value: str, minute: int) -> dict[str, Any]:
        return {
            "memoryRecordId": f"mem-{minute:040d}",
            "content": {"text": json.dumps({"key": key, "value": value})},
            "memoryStrategyId": "explicit",
            "namespaces": [preferences_namespace("cus_1")],
            "createdAt": NOW.replace(minute=minute),
        }

    stubber.add_response(
        "list_memory_records",
        {
            "memoryRecordSummaries": [
                record("home_city", "Lisboa", 5),
                record("home_city", "Madrid", 1),
                record("language", "es", 2),
            ]
        },
        {"memoryId": MEMORY, "namespace": "/preferences/cus_1/"},
    )

    assert await AgentCorePreferenceStore(client, MEMORY).get("cus_1") == {
        "home_city": "Lisboa",
        "language": "es",
    }


async def test_put_preference_writes_a_record_and_fails_loudly(
    stubbed: tuple[Any, Stubber],
) -> None:
    client, stubber = stubbed
    expected = {
        "memoryId": MEMORY,
        "records": [
            {
                "requestIdentifier": ANY,
                "namespaces": ["/preferences/cus_1/"],
                "content": {"text": '{"key": "home_city", "value": "Lisboa"}'},
                "timestamp": NOW,
            }
        ],
    }
    stubber.add_response(
        "batch_create_memory_records", {"successfulRecords": [], "failedRecords": []}, expected
    )
    failed = {
        "memoryRecordId": f"mem-{0:040d}",
        "requestIdentifier": "x",
        "status": "FAILED",
        "errorCode": 500,
    }
    stubber.add_response(
        "batch_create_memory_records",
        {"successfulRecords": [], "failedRecords": [failed]},
        expected,
    )
    store = AgentCorePreferenceStore(client, MEMORY, clock=lambda: NOW)

    await store.put("cus_1", "home_city", "Lisboa")
    with pytest.raises(RuntimeError):
        await store.put("cus_1", "home_city", "Lisboa")
