"""Edge (fase 10.2): identidad, rate limit por usuario, load shedding y errores del modelo."""

from __future__ import annotations

import asyncio

import pytest

from edge_api.testing import ASK, FakeAssistant, bearer, client, token
from headache_core.domain.admission import ClientPlan
from headache_core.ports import LLMDeadlineExceededError, LLMOverloadedError


async def test_anonymous_question_is_answered_with_rate_limit_headers() -> None:
    assistant = FakeAssistant()
    async with client(assistant) as http:
        response = await http.post("/ask", json=ASK)

    assert response.status_code == 200
    assert response.json()["citations"] == ["doc_1#0"]
    assert response.headers["RateLimit-Limit"] == "2"
    assert response.headers["RateLimit-Remaining"] == "1"
    assert assistant.callers[0].plan is ClientPlan.ANONYMOUS


async def test_user_over_the_limit_gets_429_and_others_are_unaffected() -> None:
    async with client(FakeAssistant()) as http:
        ana = bearer(token("ana"))
        statuses = [(await http.post("/ask", json=ASK, headers=ana)).status_code for _ in range(4)]
        limited = await http.post("/ask", json=ASK, headers=ana)
        luis = await http.post("/ask", json=ASK, headers=bearer(token("luis")))

    assert statuses == [200, 200, 200, 429]
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.headers["RateLimit-Policy"] == '"user_public";q=3;w=60'
    assert limited.json()["reason"] == "rate_limited"
    assert luis.status_code == 200


async def test_plan_comes_from_the_signed_token_not_from_headers() -> None:
    assistant = FakeAssistant()
    async with client(assistant) as http:
        forged = await http.post("/ask", json=ASK, headers={"X-Headache-Plan": "internal"})
        staff = await http.post("/ask", json=ASK, headers=bearer(token("ops", plan="internal")))
        unknown = await http.post("/ask", json=ASK, headers=bearer(token("eva", plan="vip")))

    assert forged.headers["RateLimit-Limit"] == "2"  # Sigue siendo anónimo.
    assert staff.headers["RateLimit-Limit"] == "50"
    assert unknown.headers["RateLimit-Limit"] == "3"  # Plan desconocido = público.
    assert [c.plan for c in assistant.callers] == [
        ClientPlan.ANONYMOUS,
        ClientPlan.INTERNAL,
        ClientPlan.PUBLIC,
    ]


@pytest.mark.parametrize(
    "authorization",
    [f"Bearer {token('ana', audience='api://otra-api')}", "Bearer basura", "Bearer "],
    ids=["wrong-audience", "garbage", "empty"],
)
async def test_invalid_token_is_401_not_anonymous(authorization: str) -> None:
    assistant = FakeAssistant()
    async with client(assistant) as http:
        response = await http.post("/ask", json=ASK, headers={"Authorization": authorization})

    assert response.status_code == 401
    assert "invalid_token" in response.headers["WWW-Authenticate"]
    assert assistant.callers == []


async def test_full_replica_sheds_public_traffic_but_serves_staff() -> None:
    assistant = FakeAssistant()
    assistant.gate = asyncio.Event()
    async with client(assistant, capacity=5) as http:
        public = [
            asyncio.create_task(http.post("/ask", json=ASK, headers=bearer(token(f"u{i}"))))
            for i in range(4)
        ]
        async with asyncio.timeout(5):  # Si algo no llega al asistente, falla en vez de colgar.
            for _ in range(4):
                await assistant.arrivals.get()
        shed = await http.post("/ask", json=ASK, headers=bearer(token("late")))
        ready = await http.get("/ready")
        staff = asyncio.create_task(
            http.post("/ask", json=ASK, headers=bearer(token("ops", plan="internal")))
        )
        async with asyncio.timeout(5):
            await assistant.arrivals.get()
        saturated = await http.get("/ready")
        assistant.gate.set()
        results = await asyncio.gather(*public, staff)

    assert shed.status_code == 503
    assert shed.headers["Retry-After"] == "1"
    assert shed.json()["reason"] == "shed"
    assert ready.json()["in_flight"] == 4
    assert saturated.status_code == 503
    assert [r.status_code for r in results] == [200] * 5


@pytest.mark.parametrize(
    ("error", "status", "retry_after"),
    [
        (LLMOverloadedError("cuota", retry_after=7.2), 503, "8"),
        (LLMOverloadedError("circuito abierto"), 503, "1"),
        (LLMDeadlineExceededError("tarde"), 504, None),
    ],
)
async def test_model_saturation_maps_to_http(
    error: Exception, status: int, retry_after: str | None
) -> None:
    assistant = FakeAssistant()
    assistant.error = error
    async with client(assistant) as http:
        response = await http.post("/ask", json=ASK)
        ready = await http.get("/ready")

    assert response.status_code == status
    assert response.headers.get("Retry-After") == retry_after
    assert ready.json()["in_flight"] == 0


async def test_question_is_validated_before_spending_quota() -> None:
    async with client(FakeAssistant()) as http:
        response = await http.post("/ask", json={"question": ""})
        ok = await http.post("/ask", json=ASK)

    assert response.status_code == 422
    assert ok.headers["RateLimit-Remaining"] == "1"  # El 422 no gastó cuota.
