"""Edge (fase 10.2): identidad, rate limit por usuario, load shedding y errores del modelo."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from edge_api.app import create_app
from edge_api.assistant import Answer
from edge_api.identity import jwt_resolver
from edge_api.settings import EdgeSettings
from headache_core.adapters.memory import InMemoryRateLimiter
from headache_core.application.admission import AdmissionController, plan_rules
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.ports import LLMDeadlineExceededError, LLMOverloadedError

ISSUER, AUDIENCE = "https://auth.headache.local", "api://headache-edge"
KEYS = RSAKeyPair.generate()


class FakeAssistant:
    def __init__(self) -> None:
        self.callers: list[Caller] = []
        self.arrivals: asyncio.Queue[None] = asyncio.Queue()
        self.gate: asyncio.Event | None = None
        self.error: Exception | None = None

    async def answer(self, question: str, caller: Caller) -> Answer:
        self.callers.append(caller)
        self.arrivals.put_nowait(None)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return Answer(f"Respuesta a: {question}", ("doc_1#0",), False, "extractive")


def token(subject: str, *, plan: str | None = None, audience: str = AUDIENCE) -> str:
    return KEYS.create_token(
        subject=subject,
        issuer=ISSUER,
        audience=audience,
        additional_claims={"plan": plan} if plan else None,
    )


def client(assistant: FakeAssistant, *, capacity: int = 10) -> httpx.AsyncClient:
    controller = AdmissionController(
        InMemoryRateLimiter(),
        rules=plan_rules({ClientPlan.ANONYMOUS: 2, ClientPlan.PUBLIC: 3, ClientPlan.INTERNAL: 50}),
        capacity=capacity,
    )
    verifier = JWTVerifier(
        public_key=KEYS.public_key, issuer=ISSUER, audience=AUDIENCE, algorithm="RS256"
    )
    app = create_app(
        EdgeSettings(_env_file=None, request_budget_seconds=5),
        assistant=assistant,
        controller=controller,
        resolver=jwt_resolver(verifier),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


ASK = {"question": "¿Hasta cuándo puedo pedir un reembolso?"}


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
