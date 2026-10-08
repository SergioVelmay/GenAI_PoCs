"""FastAPI local: una ruta por estrategia y acceso solo a fuentes publicas."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal, cast
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from headache_core.application.retrieval import (
    InvalidCitationError,
    QueryFilters,
    RetrievalRequest,
    RetrievalService,
    RetrievalUnavailableError,
)
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.llm import LLMError
from headache_core.ports.retrieval import RetrievalStrategy
from headache_core.ports.vector_store import MetadataValue, VectorRecord
from retrieval.bootstrap import build_local_runtime
from retrieval.settings import RetrievalSettings

STRATEGIES: tuple[RetrievalStrategy, ...] = (
    "bm25",
    "vector",
    "hybrid",
    "rerank",
    "rewrite",
    "hyde",
    "subquestions",
    "router",
    "auto",
    "parent-child",
    "auto-merge",
)


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=2000)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    top_k: int = Field(default=5, ge=1, le=100)
    candidate_k: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_request(self) -> "SearchIn":
        if not self.query.strip() or self.candidate_k < self.top_k:
            raise ValueError("Consulta vacia o candidate_k menor que top_k")
        return self

    def to_request(self, strategy: RetrievalStrategy) -> RetrievalRequest:
        return RetrievalRequest(
            self.query,
            strategy,
            self.top_k,
            self.candidate_k,
            self.filters.as_metadata(),
            AccessScope(),
        )


class RagIn(SearchIn):
    strategy: RetrievalStrategy = "hybrid"


class SourceOut(BaseModel):
    id: str
    text: str
    metadata: dict[str, MetadataValue]

    @classmethod
    def from_record(cls, record: VectorRecord) -> "SourceOut":
        return cls(id=record.id, text=record.text, metadata=record.metadata)


class HitOut(SourceOut):
    score: float


class SearchOut(BaseModel):
    strategy: RetrievalStrategy
    queries: tuple[str, ...]
    filters: dict[str, MetadataValue]
    hits: list[HitOut]


class CitationOut(BaseModel):
    source_id: str
    document_id: str
    title: str
    quote: str
    href: str


class RagOut(BaseModel):
    answer: str
    citations: list[CitationOut]
    abstained: bool
    mode: Literal["extractive", "llm-extractive"]


def get_service(request: Request) -> RetrievalService:
    return cast(RetrievalService, request.app.state.retrieval_service)


def search_endpoint(
    strategy: RetrievalStrategy,
) -> Callable[[SearchIn, Request], Awaitable[SearchOut]]:
    async def endpoint(body: SearchIn, request: Request) -> SearchOut:
        response = await get_service(request).retrieve(body.to_request(strategy))
        return SearchOut(
            strategy=strategy,
            queries=response.queries,
            filters=response.filters,
            hits=[
                HitOut(
                    id=result.record.id,
                    text=result.record.text,
                    metadata=result.record.metadata,
                    score=result.score,
                )
                for result in response.results
            ],
        )

    return endpoint


def rag_endpoint(generate: bool) -> Callable[[RagIn, Request], Awaitable[RagOut]]:
    async def endpoint(body: RagIn, request: Request) -> RagOut:
        response = await get_service(request).answer(
            body.to_request(body.strategy), generate=generate
        )
        return RagOut(
            answer=response.answer,
            abstained=response.abstained,
            mode=response.mode,
            citations=[
                CitationOut(
                    source_id=citation.source_id,
                    document_id=citation.document_id,
                    title=citation.title,
                    quote=citation.quote,
                    href=f"/sources/{quote(citation.source_id, safe='')}",
                )
                for citation in response.citations
            ],
        )

    return endpoint


def error_handler(status_code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handler(_: Request, exc: Exception) -> JSONResponse:
        detail = "Respuesta del modelo no valida" if isinstance(exc, LLMError) else str(exc)
        return JSONResponse(status_code=status_code, content={"detail": detail})

    return handler


def create_app(
    service: RetrievalService | None = None,
    settings: RetrievalSettings | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is None:
            runtime = await build_local_runtime(settings or RetrievalSettings())
            app.state.retrieval_service = runtime.service
            app.state.engine = runtime.engine
            app.state.corpus = {
                "source": runtime.source,
                "documents": runtime.documents,
                "chunks": runtime.chunks,
            }
        yield

    app = FastAPI(title="Headache retrieval API", version="0.1.0", lifespan=lifespan)
    app.state.retrieval_service = service
    app.state.engine = settings.engine if settings else "injected"
    app.state.corpus = {"source": "injected"}
    app.add_exception_handler(ValueError, error_handler(422))
    app.add_exception_handler(RetrievalUnavailableError, error_handler(503))
    app.add_exception_handler(InvalidCitationError, error_handler(502))
    app.add_exception_handler(LLMError, error_handler(502))

    @app.get("/health", include_in_schema=False)
    async def health(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "engine": request.app.state.engine,
            "embeddings": "hashing-not-semantic",
            "reranker": "token-coverage-not-neural",
            "cloud_calls": False,
            "corpus": request.app.state.corpus,
        }

    @app.get("/sources/{source_id:path}", response_model=SourceOut)
    async def source(source_id: str, request: Request) -> SourceOut:
        record = await get_service(request).source(source_id)
        if record is None:
            raise HTTPException(404, "Fuente no encontrada")
        return SourceOut.from_record(record)

    for strategy in STRATEGIES:
        app.add_api_route(
            f"/search/{strategy}",
            search_endpoint(strategy),
            methods=["POST"],
            response_model=SearchOut,
            name=f"search_{strategy}",
            tags=["retrieval"],
        )
    for mode, generate in (("extractive", False), ("llm-extractive", True)):
        app.add_api_route(
            f"/rag/{mode}",
            rag_endpoint(generate),
            methods=["POST"],
            response_model=RagOut,
            name=f"rag_{mode}",
            tags=["rag"],
        )
    return app
