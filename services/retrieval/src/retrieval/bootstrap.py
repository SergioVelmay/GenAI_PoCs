"""Composicion del servicio: corpus local por defecto; piezas cloud solo si se piden."""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from itertools import islice

import numpy as np
from pydantic import BaseModel, ConfigDict, TypeAdapter

from headache_core.adapters.local_retrieval import LocalRetrieval, TokenCoverageReranker
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.ingestion.chunking import SentenceChunker
from headache_core.application.ingestion.indexer import chunk_id
from headache_core.application.retrieval import RetrievalService
from headache_core.config import Settings
from headache_core.domain import DocumentType
from headache_core.ports.llm import LLMPort
from headache_core.ports.retrieval import ManagedKnowledgePort, RerankerPort, RetrievalPort
from headache_core.ports.vector_store import EmbeddingPort, MetadataValue, VectorRecord
from retrieval.settings import RetrievalSettings

PUBLIC_TYPES = frozenset({"policy", "faq", "venue_guide", "event_sheet"})


class SyntheticDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    doc_type: DocumentType
    language: str
    content: str
    metadata: dict[str, MetadataValue]


@dataclass
class Runtime:
    service: RetrievalService
    documents: int
    chunks: int
    source: str
    engine: str
    managed: ManagedKnowledgePort | None = None
    components: dict[str, str] = field(default_factory=dict)
    close: Callable[[], Awaitable[None]] | None = None

    async def aclose(self) -> None:
        if self.close is not None:
            await self.close()


def access_metadata(metadata: dict[str, MetadataValue]) -> dict[str, MetadataValue]:
    acl = metadata.get("acl")
    if acl is None:
        acl = "public" if metadata.get("doc_type") in PUBLIC_TYPES else "internal"
    return {**metadata, "acl": acl}


async def load_synthetic(
    settings: RetrievalSettings,
    embeddings: HashingEmbedding,
) -> tuple[list[VectorRecord], list[VectorRecord]]:
    chunker = SentenceChunker(settings.chunk_max_words, settings.chunk_overlap_words)
    records: list[VectorRecord] = []
    parents: list[VectorRecord] = []
    with settings.documents_path.open(encoding="utf-8") as source:
        for line in islice((line for line in source if line.strip()), settings.max_documents):
            document = SyntheticDocument.model_validate_json(line)
            parent_id = f"{document.id}::parent"
            metadata = access_metadata(
                {
                    **document.metadata,
                    "document_id": document.id,
                    "title": document.title,
                    "doc_type": document.doc_type.value,
                    "language": document.language,
                }
            )
            parents.append(VectorRecord(parent_id, document.content, [], metadata))
            pieces = chunker.split(document.content)
            vectors = await embeddings.embed_texts(
                [f"{document.title}\n\n{piece}" for piece in pieces]
            )
            records.extend(
                VectorRecord(
                    chunk_id(document.id, ordinal),
                    piece,
                    vector,
                    {
                        **metadata,
                        "chunk": ordinal,
                        "parent_id": parent_id,
                    },
                )
                for ordinal, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
            )
    return records, parents


async def load_postgres(
    settings: RetrievalSettings,
    embeddings: HashingEmbedding,
) -> tuple[list[VectorRecord], list[VectorRecord]]:
    from sqlalchemy import select

    from headache_core.adapters.postgres import create_engine, create_session_factory
    from headache_core.adapters.postgres.tables import KnowledgeChunkRow
    from headache_core.config import get_settings

    engine = create_engine(get_settings().postgres, application_name="retrieval-snapshot")
    metadata_adapter = TypeAdapter(dict[str, MetadataValue])
    records: list[VectorRecord] = []
    try:
        async with create_session_factory(engine)() as session:
            doc_id = KnowledgeChunkRow.attributes["document_id"].astext
            ids = list(
                (
                    await session.scalars(
                        select(doc_id)
                        .where(KnowledgeChunkRow.index_name == settings.index_name)
                        .distinct()
                        .order_by(doc_id)
                        .limit(settings.max_documents)
                    )
                ).all()
            )
            rows = (
                await session.scalars(
                    select(KnowledgeChunkRow)
                    .where(KnowledgeChunkRow.index_name == settings.index_name, doc_id.in_(ids))
                    .order_by(KnowledgeChunkRow.id)
                )
            ).all()
            for row in rows:
                metadata = access_metadata(metadata_adapter.validate_python(row.attributes))
                parent_id = f"{metadata['document_id']}::parent"
                records.append(
                    VectorRecord(
                        row.id,
                        row.text,
                        list(row.embedding),
                        {
                            **metadata,
                            "parent_id": parent_id,
                        },
                    )
                )
    finally:
        await engine.dispose()
    expected = await embeddings.embed_texts(
        [f"{record.metadata.get('title', '')}\n\n{record.text}" for record in records]
    )
    if any(
        not np.allclose(record.embedding, vector, atol=1e-6)
        for record, vector in zip(records, expected, strict=True)
    ):
        raise ValueError("El indice PostgreSQL no usa hashing: no mezclar encoders")
    groups: dict[str, list[VectorRecord]] = defaultdict(list)
    for record in records:
        groups[str(record.metadata["parent_id"])].append(record)
    parents: list[VectorRecord] = []
    for parent_id, children in groups.items():
        children.sort(key=lambda record: int(record.metadata.get("chunk", 0)))
        if any(child.metadata.get("acl") != children[0].metadata.get("acl") for child in children):
            raise ValueError("ACL inconsistente entre chunks del mismo documento")
        metadata = {
            key: value
            for key, value in children[0].metadata.items()
            if key not in ("chunk", "parent_id")
        }
        parents.append(
            replace(
                children[0],
                id=parent_id,
                text="\n\n".join(child.text for child in children),
                embedding=[],
                metadata=metadata,
            )
        )
    return records, parents


def build_local_service(
    settings: RetrievalSettings,
    *,
    records: Sequence[VectorRecord],
    parents: Sequence[VectorRecord],
    embeddings: EmbeddingPort,
    reranker: RerankerPort | None = None,
    llm: LLMPort | None = None,
) -> RetrievalService:
    backend: RetrievalPort
    if settings.engine == "llamaindex":
        from headache_core.adapters.llamaindex_retrieval import LlamaIndexRetrieval

        backend = LlamaIndexRetrieval(
            records,
            embeddings=embeddings,
            parents=parents,
            merge_ratio=settings.merge_ratio,
        )
    else:
        backend = LocalRetrieval(records, embeddings=embeddings, parents=parents)
    return RetrievalService(backend, reranker=reranker or TokenCoverageReranker(), llm=llm)


async def build_runtime(config: RetrievalSettings, settings: Settings | None = None) -> Runtime:
    if not config.uses_cloud:
        return await build_local_runtime(config)
    from retrieval.cloud import build_cloud_parts

    parts = await build_cloud_parts(config, settings or Settings())
    try:
        if parts.backend is not None:
            service = RetrievalService(
                parts.backend, reranker=parts.reranker or TokenCoverageReranker(), llm=parts.llm
            )
            runtime = Runtime(service, 0, 0, "azure_search", "azure_search")
        else:
            runtime = await build_local_runtime(config, reranker=parts.reranker, llm=parts.llm)
    except BaseException:
        await parts.aclose()
        raise
    runtime.managed = parts.managed
    runtime.components = parts.components
    runtime.close = parts.aclose
    return runtime


async def build_local_runtime(
    settings: RetrievalSettings,
    *,
    reranker: RerankerPort | None = None,
    llm: LLMPort | None = None,
) -> Runtime:
    embeddings = HashingEmbedding(1024)
    records, parents = await (
        load_synthetic(settings, embeddings)
        if settings.source == "synthetic"
        else load_postgres(settings, embeddings)
    )
    service = build_local_service(
        settings,
        records=records,
        parents=parents,
        embeddings=embeddings,
        reranker=reranker,
        llm=llm,
    )
    return Runtime(service, len(parents), len(records), settings.source, settings.engine)
