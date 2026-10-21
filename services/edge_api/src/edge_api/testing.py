"""Piezas de prueba del edge (sin red ni coste): asistente falso, tokens y cliente HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from edge_api.app import create_app
from edge_api.assistant import Answer, AnswerChunk, AssistantEvent, final_answer, replay
from edge_api.cases import CaseBackend
from edge_api.identity import jwt_resolver
from edge_api.settings import EdgeSettings
from headache_core.adapters.memory import (
    InMemoryCaseIntakeStore,
    InMemoryCaseStore,
    InMemoryOutbox,
    InMemoryRateLimiter,
)
from headache_core.application.admission import AdmissionController, plan_rules
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.admission import Caller, ClientPlan

ISSUER, AUDIENCE = "https://auth.headache.local", "api://headache-edge"
KEYS = RSAKeyPair.generate()


class FakeAssistant:
    """Responde "Respuesta a: <pregunta>". En streaming, palabra a palabra; `fail_after` hace
    que falle tras ese número de trozos y `verified` marca la respuesta final."""

    def __init__(self) -> None:
        self.callers: list[Caller] = []
        self.arrivals: asyncio.Queue[None] = asyncio.Queue()
        self.gate: asyncio.Event | None = None
        self.error: Exception | None = None
        self.fail_after: int | None = None
        self.verified = True

    async def answer(self, question: str, caller: Caller) -> Answer:
        return await final_answer(self.stream(question, caller))

    async def stream(self, question: str, caller: Caller) -> AsyncIterator[AssistantEvent]:
        self.callers.append(caller)
        self.arrivals.put_nowait(None)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None and self.fail_after is None:
            raise self.error
        answer = Answer(
            f"Respuesta a: {question}",
            ("doc_1#0",),
            False,
            "extractive",
            verified=self.verified,
        )
        sent = 0
        async for event in replay(answer):
            if isinstance(event, AnswerChunk) and sent == self.fail_after:
                raise self.error or RuntimeError("corte")
            sent += 1
            yield event


def token(subject: str, *, plan: str | None = None, audience: str = AUDIENCE) -> str:
    return KEYS.create_token(
        subject=subject,
        issuer=ISSUER,
        audience=audience,
        additional_claims={"plan": plan} if plan else None,
    )


def memory_cases(outbox: InMemoryOutbox | None = None) -> CaseBackend:
    """Altas y casos en memoria sobre el mismo outbox (como dos tablas de una base de datos)."""
    outbox = outbox or InMemoryOutbox()
    return CaseBackend(InMemoryCaseIntakeStore(outbox), InMemoryCaseStore(outbox))


def client(
    assistant: FakeAssistant | None = None,
    *,
    capacity: int = 10,
    cache: SemanticCache | None = None,
    cases: CaseBackend | None = None,
    **edge: Any,
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
        EdgeSettings(_env_file=None, request_budget_seconds=5, **edge),
        assistant=assistant or FakeAssistant(),
        controller=controller,
        resolver=jwt_resolver(verifier),
        cache=cache,
        cases=cases or memory_cases(),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


ASK = {"question": "¿Hasta cuándo puedo pedir un reembolso?"}
