"""Piezas de prueba del edge (sin red ni coste): asistente falso, tokens y cliente HTTP."""

from __future__ import annotations

import asyncio

import httpx
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from edge_api.app import create_app
from edge_api.assistant import Answer
from edge_api.identity import jwt_resolver
from edge_api.settings import EdgeSettings
from headache_core.adapters.memory import InMemoryRateLimiter
from headache_core.application.admission import AdmissionController, plan_rules
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.admission import Caller, ClientPlan

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


def client(
    assistant: FakeAssistant, *, capacity: int = 10, cache: SemanticCache | None = None
) -> httpx.AsyncClient:
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
        cache=cache,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


ASK = {"question": "¿Hasta cuándo puedo pedir un reembolso?"}
