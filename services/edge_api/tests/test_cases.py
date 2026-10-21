"""Casos desde la web (fase 10.5): 202 + Idempotency-Key, outbox y seguimiento por SSE."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from edge_api.cases import CaseBackend
from edge_api.testing import ASK, FakeAssistant, bearer, client, memory_cases, token
from headache_core.adapters.memory import InMemoryCaseStore, InMemoryMessageBus, InMemoryOutbox
from headache_core.application.orchestration import TriageAgent
from headache_core.application.outbox import OutboxRelay
from headache_core.domain import CaseStatus, Specialist
from headache_core.ports.cases import CaseRecord
from headache_core.ports.intake import CaseSubmission
from headache_core.ports.outbox import OutboxEvent
from orchestrator.async_flow import subscribe_workers
from orchestrator.specialists import build_specialists
from orchestrator.testing import local_world

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
CASE = {"subject": "Accesibilidad", "body": "¿El recinto tiene acceso para silla de ruedas?"}


def key(n: int = 1) -> dict[str, str]:
    return {"Idempotency-Key": f"web-form-000{n}"}


def events(response: httpx.Response) -> list[tuple[str, Any]]:
    parsed = []
    for block in response.text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith((":", "retry:"))]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        parsed.append((name, json.loads("".join(line[6:] for line in lines[1:]))))
    return parsed


def outbox_of(backend: CaseBackend) -> InMemoryOutbox:
    assert isinstance(backend.cases, InMemoryCaseStore)
    return backend.cases.outbox


async def test_case_is_accepted_at_once_and_its_event_waits_in_the_outbox() -> None:
    backend = memory_cases()
    async with client(cases=backend) as http:
        response = await http.post("/cases", json=CASE, headers={**bearer(token("ana")), **key()})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert response.headers["Location"] == f"/cases/{body['case_id']}"
    assert "Idempotent-Replayed" not in response.headers
    [event] = [e for e, *_ in outbox_of(backend).events.values()]
    assert event.topic == "support.emails"
    assert event.payload["customer_id"] == "ana"
    assert event.payload["correlation_id"] == body["case_id"]


async def test_retries_with_the_same_key_return_the_same_case_once() -> None:
    backend = memory_cases()
    ana = {**bearer(token("ana")), **key()}
    async with client(cases=backend) as http:
        first = await http.post("/cases", json=CASE, headers=ana)
        retry = await http.post("/cases", json=CASE, headers=ana)
        other_body = await http.post("/cases", json={**CASE, "body": "Otra cosa."}, headers=ana)

    assert retry.status_code == 202
    assert retry.json()["case_id"] == first.json()["case_id"]
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert other_body.status_code == 409
    assert await outbox_of(backend).backlog() == 1


@pytest.mark.parametrize(
    ("authenticated", "idempotency_key", "status"),
    [
        (False, "web-form-0001", 401),  # Un caso es de alguien.
        (True, None, 400),  # Sin clave, un reintento abriría otro caso.
        (True, "corta", 422),
        (True, "web-form-0001", 202),
    ],
    ids=["anonymous", "missing-key", "invalid-key", "ok"],
)
async def test_submission_rules(
    authenticated: bool, idempotency_key: str | None, status: int
) -> None:
    headers = bearer(token("ana")) if authenticated else {}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    async with client() as http:
        response = await http.post("/cases", json=CASE, headers=headers)

    assert response.status_code == status


async def test_cases_are_only_visible_to_their_owner_and_to_staff() -> None:
    async with client() as http:
        created = await http.post("/cases", json=CASE, headers={**bearer(token("ana")), **key()})
        case_id = created.json()["case_id"]
        own = await http.get(f"/cases/{case_id}", headers=bearer(token("ana")))
        other = await http.get(f"/cases/{case_id}", headers=bearer(token("luis")))
        staff = await http.get(f"/cases/{case_id}", headers=bearer(token("ops", plan="internal")))
        missing = await http.get("/cases/case_nope", headers=bearer(token("ana")))

    assert [r.status_code for r in (own, other, staff, missing)] == [200, 404, 200, 404]


async def test_submissions_count_against_the_rate_limit() -> None:
    async with client() as http:
        statuses = [
            (
                await http.post("/cases", json=CASE, headers={**bearer(token("ana")), **key(i)})
            ).status_code
            for i in range(4)
        ]

    assert statuses == [202, 202, 202, 429]


async def test_cases_have_their_own_bulkhead_when_questions_fill_the_replica() -> None:
    # Fase 10.7: en la prueba de carga sin caché, las preguntas lentas ocupaban todos los huecos
    # y las altas (una transacción) recibían 503. Ahora cada ruta tiene su compartimento.
    assistant = FakeAssistant()
    assistant.gate = asyncio.Event()
    async with client(assistant, capacity=5) as http:
        asking = [
            asyncio.create_task(http.post("/ask", json=ASK, headers=bearer(token(f"u{i}"))))
            for i in range(4)
        ]
        async with asyncio.timeout(5):
            for _ in range(4):
                await assistant.arrivals.get()
        shed = await http.post("/ask", json=ASK, headers=bearer(token("late")))
        case = await http.post("/cases", json=CASE, headers={**bearer(token("late")), **key()})
        assistant.gate.set()
        await asyncio.gather(*asking)

    assert shed.status_code == 503
    assert case.status_code == 202


class DownIntake:
    async def submit(
        self, submission: CaseSubmission, event: OutboxEvent
    ) -> tuple[CaseSubmission, bool]:
        raise ConnectionRefusedError("PostgreSQL no responde")

    async def get_submission(self, case_id: str) -> CaseSubmission | None:
        raise ConnectionRefusedError("PostgreSQL no responde")


async def test_database_outage_is_a_503_not_a_500() -> None:
    backend = CaseBackend(DownIntake(), InMemoryCaseStore())
    async with client(cases=backend) as http:
        response = await http.post("/cases", json=CASE, headers={**bearer(token("ana")), **key()})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"


async def test_status_stream_follows_the_case_until_it_closes() -> None:
    backend = memory_cases()
    ana = bearer(token("ana"))
    async with client(cases=backend, case_poll_seconds=0.02) as http:
        created = await http.post("/cases", json=CASE, headers={**ana, **key()})
        case_id = created.json()["case_id"]
        email_id = case_id.removeprefix("case_")

        async def workers() -> None:
            record = CaseRecord(case_id, email_id, "ana", None, CaseStatus.RECEIVED, None, NOW, NOW)
            await asyncio.sleep(0.05)
            await backend.cases.create(record)
            triaged = replace(record, status=CaseStatus.TRIAGED, specialist=Specialist.KNOWLEDGE)
            await asyncio.sleep(0.05)
            await backend.cases.transition(case_id, CaseStatus.RECEIVED, triaged)
            await asyncio.sleep(0.05)
            resolved = replace(triaged, status=CaseStatus.RESOLVED)
            await backend.cases.transition(case_id, CaseStatus.TRIAGED, resolved)

        progress = asyncio.create_task(workers())
        async with asyncio.timeout(5):
            stream = await http.get(f"/cases/{case_id}/events", headers=ana)
        await progress

    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "retry: 20" in stream.text
    statuses = [data["status"] for name, data in events(stream) if name == "status"]
    assert statuses == ["accepted", "received", "triaged", "resolved"]


async def test_status_stream_ends_with_timeout_and_the_client_reconnects() -> None:
    async with client(case_poll_seconds=0.01, case_stream_seconds=0.05) as http:
        created = await http.post("/cases", json=CASE, headers={**bearer(token("ana")), **key()})
        stream = await http.get(
            f"/cases/{created.json()['case_id']}/events", headers=bearer(token("ana"))
        )

    assert [name for name, _ in events(stream)] == ["status", "timeout"]


async def test_end_to_end_web_case_through_outbox_relay_and_workers() -> None:
    # Edge → outbox → relay → cola → triaje → especialista → caso resuelto, visto por SSE.
    world = local_world(
        [
            json.dumps(
                {
                    "intent": "event_info",
                    "language": "es",
                    "order_id": None,
                    "urgency": "normal",
                    "summary": "Pregunta por accesibilidad.",
                }
            ),
            "Sí, el recinto es accesible.",
        ]
    )
    bus, outbox = InMemoryMessageBus(), InMemoryOutbox()
    backend = memory_cases(outbox)
    await subscribe_workers(
        bus,
        backend.cases,
        triage=TriageAgent(world.llm),
        specialists=build_specialists(world.deps),
    )
    relay = OutboxRelay(outbox, bus)
    ana = bearer(token("ana"))
    async with client(cases=backend, case_poll_seconds=0.02) as http:
        created = await http.post("/cases", json=CASE, headers={**ana, **key()})
        case_id = created.json()["case_id"]
        assert await relay.drain() == 3  # Alta, traspaso y resultado.
        stream = await http.get(f"/cases/{case_id}/events", headers=ana)

    statuses = [data["status"] for name, data in events(stream) if name == "status"]
    assert statuses == ["resolved"]
    record = await backend.cases.get(case_id)
    assert record is not None and record.customer_id == "ana"
