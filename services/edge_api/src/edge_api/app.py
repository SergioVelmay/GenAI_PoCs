"""Edge de Headache (fase 10): la única puerta pública, que decide pronto y barato.

    uv run uvicorn edge_api.app:create_app --factory --port 8200

Cada `POST /ask` pasa, en orden: identidad (token firmado o anónimo por IP) → *load shedding*
por prioridad (503) → rate limit por usuario en Redis (429) → caché semántica (10.3, cabecera
`X-Cache`) → asistente con presupuesto de tiempo (504 si se agota; 503 + `Retry-After` si el
modelo está saturado). Un acierto de caché también cuenta para el límite del usuario.

`/cases` (10.5, `cases.py`): alta idempotente con 202 y seguimiento por SSE.

`POST /ask/stream` (10.4) hace lo mismo en Server-Sent Events (protocolo en `sse.py`): el hueco
de admisión se ocupa mientras dura el stream, no solo hasta el primer trozo.
"""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError

from edge_api.assistant import Answer, Assistant, RagAssistant
from edge_api.cache import CachedAssistant
from edge_api.cases import CaseBackend
from edge_api.cases import build_router as build_cases_router
from edge_api.identity import CallerResolver, UnauthenticatedError, build_resolver
from edge_api.settings import EdgeSettings
from edge_api.sse import body as sse_body
from edge_api.sse import prime, pump
from headache_core.adapters.memory import (
    HashingEmbedding,
    InMemoryCaseIntakeStore,
    InMemoryCaseStore,
    InMemoryOutbox,
    InMemoryRateLimiter,
    InMemorySemanticCache,
)
from headache_core.adapters.otel import instrument_fastapi, setup_observability
from headache_core.application.admission import (
    AdmissionController,
    AdmissionRejectedError,
    CallerRateLimitedError,
    plan_rules,
)
from headache_core.application.answer_stream import StreamingAnswerer
from headache_core.application.case_intake import InvalidIdempotencyKeyError
from headache_core.application.llm import time_budget
from headache_core.application.retrieval import RetrievalService, RetrievalUnavailableError
from headache_core.application.semantic_cache import SemanticCache
from headache_core.application.telemetry import genai, get_telemetry
from headache_core.config import LLMProvider, Settings
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.domain.semantic_cache import CALIBRATED_THRESHOLDS
from headache_core.ports import (
    ConflictError,
    EmbeddingPort,
    LLMDeadlineExceededError,
    LLMError,
    LLMTransientError,
    RateLimiterPort,
)
from headache_core.ports.semantic_cache import SemanticCachePort

logger = logging.getLogger(__name__)


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=1000)


class AskOut(BaseModel):
    answer: str
    citations: list[str]
    abstained: bool
    mode: str
    cache: str
    verified: bool
    # Plan B (10.7): el modelo estaba saturado y se respondió con citas literales.
    degraded: bool = False

    @classmethod
    def from_answer(cls, answer: Answer) -> AskOut:
        return cls(
            answer=answer.text,
            citations=list(answer.citations),
            abstained=answer.abstained,
            mode=answer.mode,
            cache=answer.cache,
            verified=answer.verified,
            degraded=answer.degraded,
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

    async def conflict(_: Request, exc: ConflictError) -> Response:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    async def bad_key(_: Request, exc: InvalidIdempotencyKeyError) -> Response:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    async def database_down(_: Request, exc: Exception) -> Response:
        logger.warning("Base de datos no disponible: %s", exc)
        return JSONResponse(
            {"detail": "Servicio no disponible."}, status_code=503, headers={"Retry-After": "5"}
        )

    # Del más concreto al más general: Starlette busca por la jerarquía (MRO) de la excepción.
    return {
        AdmissionRejectedError: rejected,
        UnauthenticatedError: unauthenticated,
        LLMDeadlineExceededError: deadline,
        LLMTransientError: saturated,
        RetrievalUnavailableError: unavailable,
        LLMError: model_error,
        ConflictError: conflict,
        InvalidIdempotencyKeyError: bad_key,
        # Conexión rechazada (OSError) o error del driver: 503, no 500.
        OSError: database_down,
        DBAPIError: database_down,
    }


def build_case_backend(
    settings: Settings, edge: EdgeSettings
) -> tuple[CaseBackend | None, Callable[[], Awaitable[None]] | None]:
    """Casos en PostgreSQL (los mismos que leen y escriben los workers). Conecta al primer uso."""
    if edge.cases_backend == "disabled":
        return None, None
    if edge.cases_backend == "memory":
        outbox = InMemoryOutbox()
        return CaseBackend(InMemoryCaseIntakeStore(outbox), InMemoryCaseStore(outbox)), None
    from headache_core.adapters.postgres import create_engine, create_session_factory
    from headache_core.adapters.postgres.cases import PostgresCaseStore
    from headache_core.adapters.postgres.outbox import PostgresCaseIntakeStore

    engine = create_engine(settings.postgres, application_name="edge-api")
    factory = create_session_factory(engine)
    return (
        CaseBackend(PostgresCaseIntakeStore(factory), PostgresCaseStore(factory)),
        engine.dispose,
    )


def build_semantic_cache(
    settings: Settings, embeddings: EmbeddingPort, *, embeddings_name: str
) -> SemanticCache | None:
    """La caché con el umbral calibrado para estos embeddings; sin calibrar, sin caché."""
    config = settings.semantic_cache
    threshold = config.threshold or CALIBRATED_THRESHOLDS.get(embeddings_name)
    if not config.enabled or threshold is None:
        if config.enabled:
            logger.warning("Sin umbral calibrado para '%s': caché desactivada.", embeddings_name)
        return None
    store: SemanticCachePort = InMemorySemanticCache()
    if config.backend == "redis":
        from headache_core.adapters.redis_semantic_cache import RedisSemanticCache

        store = RedisSemanticCache.from_settings(
            settings.scale_redis, dimensions=embeddings.dimensions, key_prefix=config.key_prefix
        )
    return SemanticCache(
        store,
        embeddings,
        threshold=threshold,
        ttl_seconds=config.ttl_seconds,
        candidates=config.candidates,
    )


def with_cache(
    assistant: Assistant, cache: SemanticCache | None, settings: Settings, edge: EdgeSettings
) -> Assistant:
    if cache is None:
        return assistant
    return CachedAssistant(
        assistant,
        cache,
        index_version=settings.semantic_cache.index_version,
        mode=edge.answer_mode,
    )


def build_assistant(service: RetrievalService, settings: Settings, edge: EdgeSettings) -> Assistant:
    """El RAG en proceso. En modo generativo, con el gateway completo (cuota, breaker, coste)."""
    degrade = edge.degrade_on_saturation
    if edge.answer_mode != "generative":
        return RagAssistant(service, mode=edge.answer_mode, degrade_on_saturation=degrade)
    from headache_core.adapters.llm_factory import build_gateway

    answerer = StreamingAnswerer(service, build_gateway(settings))
    return RagAssistant(
        service, mode="generative", answerer=answerer, degrade_on_saturation=degrade
    )


def create_app(
    edge: EdgeSettings | None = None,
    *,
    settings: Settings | None = None,
    assistant: Assistant | None = None,
    controller: AdmissionController | None = None,
    cases_controller: AdmissionController | None = None,
    resolver: CallerResolver | None = None,
    cache: SemanticCache | None = None,
    cases: CaseBackend | None = None,
) -> FastAPI:
    edge = edge or EdgeSettings()
    settings = settings or Settings()
    setup_observability(settings, service_name="edge_api")
    controller = controller or build_controller(settings, edge)
    # Mismo rate limit por usuario, capacidad separada: un bulkhead por ruta (fase 10.7).
    cases_controller = cases_controller or controller.bulkhead(edge.cases_capacity)
    resolve = resolver or build_resolver(edge)
    close_cases: Callable[[], Awaitable[None]] | None = None
    if cases is None:
        cases, close_cases = build_case_backend(settings, edge)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            if close_cases is not None:
                stack.push_async_callback(close_cases)
            if assistant is None:
                stack.push_async_callback(await _load_assistant(app))
            yield

    async def _load_assistant(app: FastAPI) -> Callable[[], Awaitable[None]]:
        from retrieval.bootstrap import build_runtime
        from retrieval.settings import RetrievalSettings

        config = RetrievalSettings()
        runtime = await build_runtime(config, settings)
        embeddings: EmbeddingPort = HashingEmbedding(1024)
        if config.embeddings != "hashing":
            from retrieval.cloud import build_embeddings

            embeddings = build_embeddings(config, settings)
        semantic = build_semantic_cache(settings, embeddings, embeddings_name=config.embeddings)
        app.state.assistant = with_cache(
            build_assistant(runtime.service, settings, edge), semantic, settings, edge
        )
        return runtime.aclose

    app = FastAPI(title="Headache edge API", version="0.1.0", lifespan=lifespan)
    instrument_fastapi(app, settings)
    app.include_router(build_cases_router(edge, cases_controller, cases))
    app.state.assistant = (
        with_cache(assistant, cache, settings, edge) if assistant is not None else None
    )
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
            "cases": {
                "in_flight": cases_controller.in_flight,
                "capacity": cases_controller.capacity,
                "outcomes": dict(cases_controller.outcomes),
            },
            "cache": _cache_outcomes(app.state.assistant),
            "assistant": _assistant_outcomes(app.state.assistant),
            "llm": _llm_outcomes(settings),
        }

    @app.post("/ask", response_model=AskOut, tags=["asistente"])
    async def ask(body: AskIn, request: Request, response: Response, who: CurrentCaller) -> AskOut:
        async with controller.admit(who) as admission:
            response.headers["RateLimit-Limit"] = str(int(admission.rule.limit))
            response.headers["RateLimit-Remaining"] = str(admission.remaining)
            backend: Assistant = request.app.state.assistant
            with time_budget(edge.request_budget_seconds):
                answer = await backend.answer(body.question, who)
        _annotate(answer)
        response.headers["X-Cache"] = answer.cache.upper()
        return AskOut.from_answer(answer)

    @app.post(
        "/ask/stream",
        tags=["asistente"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def ask_stream(body: AskIn, request: Request, who: CurrentCaller) -> StreamingResponse:
        stack = AsyncExitStack()
        # 422, 401, 429 y 503 salen aquí, antes de abrir el stream.
        admission = await stack.enter_async_context(controller.admit(who))
        backend: Assistant = request.app.state.assistant
        events = pump(
            backend.stream(body.question, who),
            budget_seconds=edge.request_budget_seconds,
            heartbeat_seconds=edge.heartbeat_seconds,
        )
        try:
            first = await prime(events)
        except BaseException:
            await events.aclose()
            await stack.aclose()
            raise
        # TTFT visto desde el edge: la distancia entre el inicio del span y este evento.
        get_telemetry().current_span().add_event(genai.EVENT_FIRST_CHUNK)
        headers = {
            "RateLimit-Limit": str(int(admission.rule.limit)),
            "RateLimit-Remaining": str(admission.remaining),
            "Cache-Control": "no-cache",
            # Sin búfer en proxies (nginx, algunos balanceadores): cada trozo sale al momento.
            "X-Accel-Buffering": "no",
        }

        def done(answer: Answer) -> AskOut:
            _annotate(answer)
            return AskOut.from_answer(answer)

        return StreamingResponse(
            sse_body(first, events, stack, done=done),
            media_type="text/event-stream",
            headers=headers,
        )

    return app


def _annotate(answer: Answer) -> None:
    get_telemetry().current_span().set_attributes(
        {
            genai.HEADACHE_ANSWER_MODE: answer.mode,
            genai.HEADACHE_ABSTAINED: answer.abstained,
            genai.HEADACHE_DEGRADED: answer.degraded,
            genai.HEADACHE_CACHE: answer.cache,
        }
    )


def _llm_outcomes(settings: Settings) -> dict[str, int]:
    """Llamadas, 429 y fallos del proveedor simulado (pruebas de carga, fase 10.7)."""
    if settings.llm_provider is not LLMProvider.SIMULATED:
        return {}
    from headache_core.adapters.simulated_llm import STATS

    return dict(STATS)


def _cache_outcomes(assistant: object) -> dict[str, object]:
    if not isinstance(assistant, CachedAssistant):
        return {}
    # `answers`: de dónde salió cada respuesta. `cache`: qué decidió la caché y por qué.
    return {"answers": dict(assistant.outcomes), "cache": dict(assistant.cache.outcomes)}


def _assistant_outcomes(assistant: object) -> dict[str, int]:
    inner = assistant.inner if isinstance(assistant, CachedAssistant) else assistant
    return dict(inner.outcomes) if isinstance(inner, RagAssistant) else {}
