"""Edge de Headache (fase 10.2): la única puerta pública, que decide pronto y barato.

    uv run uvicorn edge_api.app:create_app --factory --port 8200

Cada `POST /ask` pasa, en orden: identidad (token firmado o anónimo por IP) → *load shedding*
por prioridad (503) → rate limit por usuario en Redis (429) → asistente con presupuesto de
tiempo (504 si se agota; 503 + `Retry-After` si el modelo está saturado).
"""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from edge_api.assistant import Answer, Assistant, RagAssistant
from edge_api.identity import CallerResolver, UnauthenticatedError, build_resolver
from edge_api.settings import EdgeSettings
from headache_core.adapters.memory import InMemoryRateLimiter
from headache_core.application.admission import (
    AdmissionController,
    AdmissionRejectedError,
    CallerRateLimitedError,
    plan_rules,
)
from headache_core.application.llm import time_budget
from headache_core.application.retrieval import RetrievalUnavailableError
from headache_core.config import Settings
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.ports import (
    LLMDeadlineExceededError,
    LLMError,
    LLMTransientError,
    RateLimiterPort,
)

logger = logging.getLogger(__name__)


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=1000)


class AskOut(BaseModel):
    answer: str
    citations: list[str]
    abstained: bool
    mode: str

    @classmethod
    def from_answer(cls, answer: Answer) -> AskOut:
        return cls(
            answer=answer.text,
            citations=list(answer.citations),
            abstained=answer.abstained,
            mode=answer.mode,
        )


async def current_caller(request: Request) -> Caller:
    resolve: CallerResolver = request.app.state.resolve
    return await resolve(request)


CurrentCaller = Annotated[Caller, Depends(current_caller)]


def retry_after_header(seconds: float | None, default: float) -> str:
    """`Retry-After` va en segundos enteros (RFC 9110): se redondea hacia arriba, mínimo 1."""
    return str(max(1, math.ceil(seconds if seconds else default)))


def build_controller(settings: Settings, edge: EdgeSettings) -> AdmissionController:
    limits = settings.rate_limit
    limiter: RateLimiterPort = InMemoryRateLimiter()
    if limits.backend == "redis":
        from headache_core.adapters.redis_rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter.from_settings(settings.scale_redis, key_prefix=limits.key_prefix)
    return AdmissionController(
        limiter,
        rules=plan_rules(
            {
                ClientPlan.ANONYMOUS: limits.anonymous_per_minute,
                ClientPlan.PUBLIC: limits.public_per_minute,
                ClientPlan.INTERNAL: limits.internal_per_minute,
            }
        ),
        capacity=edge.capacity,
        # Plan B si Redis cae: límite aproximado por réplica en lugar de tirar el servicio.
        fallback=InMemoryRateLimiter(),
        overloaded_retry_after=edge.overloaded_retry_after_seconds,
    )


def _handlers(edge: EdgeSettings) -> dict[type[Exception], Callable[..., Awaitable[Response]]]:
    async def rejected(_: Request, exc: AdmissionRejectedError) -> Response:
        headers = {"Retry-After": retry_after_header(exc.retry_after, 1.0)}
        if isinstance(exc, CallerRateLimitedError):
            rule = exc.rule
            headers |= {
                "RateLimit-Limit": str(int(rule.limit)),
                "RateLimit-Remaining": "0",
                "RateLimit-Policy": (
                    f'"{rule.name}";q={int(rule.limit)};w={int(rule.period_seconds)}'
                ),
            }
        return JSONResponse(
            {"detail": str(exc), "reason": exc.reason}, status_code=exc.status, headers=headers
        )

    async def unauthenticated(_: Request, exc: UnauthenticatedError) -> Response:
        return JSONResponse(
            {"detail": str(exc)},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    async def deadline(_: Request, exc: LLMDeadlineExceededError) -> Response:
        return JSONResponse({"detail": "La respuesta ha tardado demasiado."}, status_code=504)

    async def saturated(_: Request, exc: LLMTransientError) -> Response:
        # Cuota del modelo agotada, circuito abierto o proveedor caído: vuelve en un rato.
        retry = retry_after_header(exc.retry_after, edge.overloaded_retry_after_seconds)
        return JSONResponse(
            {"detail": "El asistente está saturado.", "reason": "model_unavailable"},
            status_code=503,
            headers={"Retry-After": retry},
        )

    async def unavailable(_: Request, exc: Exception) -> Response:
        return JSONResponse({"detail": "Servicio no disponible."}, status_code=503)

    async def model_error(_: Request, exc: LLMError) -> Response:
        return JSONResponse({"detail": "Respuesta del modelo no válida."}, status_code=502)

    # Del más concreto al más general: Starlette busca por la jerarquía (MRO) de la excepción.
    return {
        AdmissionRejectedError: rejected,
        UnauthenticatedError: unauthenticated,
        LLMDeadlineExceededError: deadline,
        LLMTransientError: saturated,
        RetrievalUnavailableError: unavailable,
        LLMError: model_error,
    }


def create_app(
    edge: EdgeSettings | None = None,
    *,
    settings: Settings | None = None,
    assistant: Assistant | None = None,
    controller: AdmissionController | None = None,
    resolver: CallerResolver | None = None,
) -> FastAPI:
    edge = edge or EdgeSettings()
    settings = settings or Settings()
    controller = controller or build_controller(settings, edge)
    resolve = resolver or build_resolver(edge)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if assistant is not None:
            yield
            return
        from retrieval.bootstrap import build_runtime
        from retrieval.settings import RetrievalSettings

        runtime = await build_runtime(RetrievalSettings(), settings)
        app.state.assistant = RagAssistant(runtime.service, generate=edge.generate)
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(title="Headache edge API", version="0.1.0", lifespan=lifespan)
    app.state.assistant = assistant
    app.state.resolve = resolve
    for exc_type, handler in _handlers(edge).items():
        app.add_exception_handler(exc_type, handler)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready(response: Response) -> dict[str, object]:
        # El balanceador deja de mandar tráfico a una réplica llena (readiness, no liveness).
        saturated = controller.in_flight >= controller.capacity
        if saturated:
            response.status_code = 503
        return {
            "status": "saturated" if saturated else "ok",
            "in_flight": controller.in_flight,
            "capacity": controller.capacity,
            "outcomes": dict(controller.outcomes),
        }

    @app.post("/ask", response_model=AskOut, tags=["asistente"])
    async def ask(body: AskIn, request: Request, response: Response, who: CurrentCaller) -> AskOut:
        async with controller.admit(who) as admission:
            response.headers["RateLimit-Limit"] = str(int(admission.rule.limit))
            response.headers["RateLimit-Remaining"] = str(admission.remaining)
            backend: Assistant = request.app.state.assistant
            with time_budget(edge.request_budget_seconds):
                answer = await backend.answer(body.question, who)
        return AskOut.from_answer(answer)

    return app
