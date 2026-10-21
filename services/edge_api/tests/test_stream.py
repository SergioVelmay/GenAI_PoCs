"""Streaming SSE en el edge (fase 10.4): trozos, errores antes y después de empezar, caché y
liberación del hueco de admisión aunque el cliente se vaya a mitad."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import MutableMapping
from typing import Any

import httpx
import pytest
from starlette.requests import ClientDisconnect

from edge_api.app import create_app
from edge_api.settings import EdgeSettings
from edge_api.testing import ASK, FakeAssistant, bearer, client, token
from headache_core.adapters.memory import (
    HashingEmbedding,
    InMemoryRateLimiter,
    InMemorySemanticCache,
)
from headache_core.application.admission import AdmissionController, plan_rules
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.ports import LLMOverloadedError, LLMTransientError


def events(response: httpx.Response) -> list[tuple[str, Any]]:
    parsed = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if not lines or all(line.startswith(":") for line in lines):
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        parsed.append((name, json.loads(data)))
    return parsed


def semantic_cache() -> SemanticCache:
    return SemanticCache(InMemorySemanticCache(), HashingEmbedding(256), threshold=0.55)


async def test_answer_arrives_in_chunks_and_ends_with_the_full_answer() -> None:
    async with client(FakeAssistant()) as http:
        response = await http.post("/ask/stream", json=ASK, headers=bearer(token("ana")))
        ready = await http.get("/ready")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["RateLimit-Remaining"] == "2"
    parsed = events(response)
    deltas = [data["text"] for name, data in parsed if name == "delta"]
    name, done = parsed[-1]
    assert len(deltas) > 3
    assert name == "done"
    assert "".join(deltas) == done["answer"]
    assert (done["cache"], done["verified"]) == ("miss", True)
    assert ready.json()["in_flight"] == 0


async def test_rejections_before_streaming_are_plain_http() -> None:
    async with client(FakeAssistant()) as http:
        statuses = [(await http.post("/ask/stream", json=ASK)).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


async def test_model_saturated_before_the_first_chunk_is_a_503() -> None:
    assistant = FakeAssistant()
    assistant.error = LLMOverloadedError("cuota", retry_after=4.2)
    async with client(assistant) as http:
        response = await http.post("/ask/stream", json=ASK)
        ready = await http.get("/ready")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert ready.json()["in_flight"] == 0


async def test_failure_mid_stream_is_an_error_event() -> None:
    assistant = FakeAssistant()
    assistant.error = LLMTransientError("conexión cortada", retry_after=2)
    assistant.fail_after = 2
    async with client(assistant) as http:
        response = await http.post("/ask/stream", json=ASK)
        ready = await http.get("/ready")

    parsed = events(response)
    assert response.status_code == 200
    assert [name for name, _ in parsed] == ["delta", "delta", "error"]
    assert parsed[-1][1] == {
        "reason": "model_unavailable",
        "detail": "El asistente está saturado.",
        "retry_after": 2,
    }
    assert ready.json()["in_flight"] == 0


async def test_stream_that_exceeds_the_budget_ends_with_a_deadline_event() -> None:
    assistant = FakeAssistant()
    assistant.gate = asyncio.Event()  # Nunca se abre: el asistente no llega a responder.
    app = create_app(
        EdgeSettings(_env_file=None, request_budget_seconds=0.2, auth="none"),
        assistant=assistant,
        controller=AdmissionController(
            InMemoryRateLimiter(), rules=plan_rules({ClientPlan.ANONYMOUS: 5}), capacity=5
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://edge") as http:
        response = await http.post("/ask/stream", json=ASK)

    assert response.status_code == 504


async def test_second_similar_question_streams_from_the_cache() -> None:
    assistant = FakeAssistant()
    ana = bearer(token("ana"))
    async with client(assistant, cache=semantic_cache()) as http:
        await http.post(
            "/ask/stream",
            json={"question": "¿A qué hora abren las puertas en Barcelona?"},
            headers=ana,
        )
        second = await http.post(
            "/ask/stream",
            json={"question": "¿Cuándo abren las puertas en Barcelona?"},
            headers=ana,
        )

    parsed = events(second)
    assert parsed[-1][1]["cache"] == "hit"
    assert sum(name == "delta" for name, _ in parsed) > 1  # También en trozos.
    assert len(assistant.callers) == 1


async def test_unverified_streamed_answers_are_not_cached() -> None:
    assistant = FakeAssistant()
    assistant.verified = False
    ana = bearer(token("ana"))
    async with client(assistant, cache=semantic_cache()) as http:
        first = await http.post("/ask/stream", json=ASK, headers=ana)
        second = await http.post("/ask/stream", json=ASK, headers=ana)

    assert events(first)[-1][1]["verified"] is False
    assert events(second)[-1][1]["cache"] == "miss"
    assert len(assistant.callers) == 2


async def test_identical_concurrent_streams_are_computed_once() -> None:
    assistant = FakeAssistant()
    assistant.gate = asyncio.Event()
    async with client(assistant, cache=semantic_cache(), capacity=50) as http:
        burst = [
            asyncio.create_task(http.post("/ask/stream", json=ASK, headers=bearer(token(f"u{i}"))))
            for i in range(5)
        ]
        async with asyncio.timeout(5):
            await assistant.arrivals.get()
        await asyncio.sleep(0.05)
        assistant.gate.set()
        responses = await asyncio.gather(*burst)

    caches = sorted(events(r)[-1][1]["cache"] for r in responses)
    assert caches == ["coalesced"] * 4 + ["miss"]
    assert len(assistant.callers) == 1


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
async def test_client_leaving_mid_stream_frees_the_admission_slot(spec_version: str) -> None:
    controller = AdmissionController(
        InMemoryRateLimiter(), rules=plan_rules({ClientPlan.ANONYMOUS: 5}), capacity=5
    )
    assistant = FakeAssistant()
    app = create_app(
        EdgeSettings(_env_file=None, auth="none"), assistant=assistant, controller=controller
    )
    disconnected = asyncio.Event()
    sent: list[bytes] = []
    body = json.dumps(ASK).encode()
    requests = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        if requests:
            return requests.pop()
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            if disconnected.is_set():
                raise OSError("conexión cerrada")
            sent.append(message["body"])
            disconnected.set()  # El usuario cierra la pestaña tras el primer trozo.

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/ask/stream",
        "raw_path": b"/ask/stream",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("10.0.0.1", 1234),
        "server": ("edge", 80),
    }
    # Con la especificación 2.4, Starlette convierte el OSError del `send` en ClientDisconnect.
    with contextlib.suppress(ClientDisconnect):
        await asyncio.wait_for(app(scope, receive, send), 5)
    for _ in range(100):  # El generador se cierra al recogerse: unas vueltas del bucle.
        if not controller.in_flight:
            break
        await asyncio.sleep(0.01)

    assert len(sent) == 1
    assert controller.in_flight == 0
    assert assistant.callers == [Caller("ip:10.0.0.1", ClientPlan.ANONYMOUS)]
