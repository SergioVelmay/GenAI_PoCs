"""Carril C: ingesta con LlamaIndex (`IngestionPipeline`). Requiere `headache-core[llamaindex]`.

Qué aporta el framework frente al carril nativo:
- Node parsers listos: tokens fijos, frases, semántico (por embeddings) y jerárquico.
- Deduplicación e ingesta incremental con docstore + `DocstoreStrategy.UPSERTS`.
- Caché de transformaciones (`IngestionCache`): reprocesar la misma entrada no recalcula nada.
A cambio, el esquema de las tablas y la semántica de borrado son del framework, no nuestros.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from llama_index.core import Document
from llama_index.core.base.embeddings.base import BaseEmbedding, Embedding
from llama_index.core.bridge.pydantic import PrivateAttr
from llama_index.core.ingestion import DocstoreStrategy, IngestionCache, IngestionPipeline
from llama_index.core.node_parser import (
    HierarchicalNodeParser,
    NodeParser,
    SemanticSplitterNodeParser,
    SentenceSplitter,
    TokenTextSplitter,
    get_leaf_nodes,
)
from llama_index.core.schema import BaseNode, MetadataMode, TransformComponent
from llama_index.core.storage.docstore.types import BaseDocumentStore
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore,
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
)

from headache_core.application.ingestion import IndexingOutcome, IndexingResult
from headache_core.config import PostgresSettings
from headache_core.domain import KnowledgeDocument
from headache_core.ports import EmbeddingPort, MetadataValue, SearchResult, VectorRecord

type ChunkStrategy = Literal["fixed", "sentence", "semantic", "hierarchical"]

# Lo único de los metadatos que entra en el texto a embeber (equivale a la cabecera contextual
# del carril nativo). Por defecto LlamaIndex embebe TODOS los metadatos: ids y hashes incluidos.
EMBEDDED_METADATA = frozenset({"title", "section"})
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


class PortEmbedding(BaseEmbedding):
    """Puente: LlamaIndex usa nuestro `EmbeddingPort`, el mismo modelo que el carril nativo."""

    _port: EmbeddingPort = PrivateAttr()

    def __init__(self, port: EmbeddingPort, *, model_name: str, embed_batch_size: int = 16) -> None:
        # model_name forma parte de la clave de la caché: cambiar de modelo la invalida.
        super().__init__(model_name=model_name, embed_batch_size=embed_batch_size)
        self._port = port

    @classmethod
    def class_name(cls) -> str:
        return "HeadachePortEmbedding"

    async def _aget_query_embedding(self, query: str) -> Embedding:
        return await self._port.embed_query(query)

    async def _aget_text_embedding(self, text: str) -> Embedding:
        return (await self._port.embed_texts([text]))[0]

    async def _aget_text_embeddings(self, texts: list[str]) -> list[Embedding]:
        return await self._port.embed_texts(texts)

    # Rutas síncronas (scripts sin event loop); el pipeline usa siempre las asíncronas.
    def _get_query_embedding(self, query: str) -> Embedding:
        return asyncio.run(self._aget_query_embedding(query))

    def _get_text_embedding(self, text: str) -> Embedding:
        return asyncio.run(self._aget_text_embedding(text))

    def _get_text_embeddings(self, texts: list[str]) -> list[Embedding]:
        return asyncio.run(self._aget_text_embeddings(texts))


class MarkdownSectionExtractor(TransformComponent):
    """Enriquecimiento sin LLM: la sección Markdown de cada chunk y su tamaño en palabras.

    Los extractores de LlamaIndex con LLM (títulos, preguntas, keywords) cuestan tokens por
    chunk; esta heurística es gratis y basta para documentos con estructura.
    """

    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> Sequence[BaseNode]:
        current: dict[str, str] = {}
        for node in nodes:
            ref = node.ref_doc_id or node.node_id
            text = node.get_content(metadata_mode=MetadataMode.NONE)
            headings = _HEADING.findall(text)
            starts_with_heading = bool(headings) and text.lstrip().startswith("#")
            node.metadata["section"] = headings[0] if starts_with_heading else current.get(ref, "")
            node.metadata["chunk_words"] = len(text.split())
            if headings:
                current[ref] = headings[-1]
        return nodes


class HierarchicalLeaves(TransformComponent):
    """Parent-child: toda la jerarquía va al docstore (para auto-merging en la fase 4) y
    solo las hojas siguen hacia el embedding y el vector store.

    Envuelve `HierarchicalNodeParser` porque en LlamaIndex 0.14 solo trocea por la ruta
    síncrona: su `acall` (la que usa `IngestionPipeline.arun`) devuelve la entrada sin partir.
    """

    _parser: HierarchicalNodeParser = PrivateAttr()
    _docstore: BaseDocumentStore = PrivateAttr()

    def __init__(self, parser: HierarchicalNodeParser, docstore: BaseDocumentStore) -> None:
        super().__init__()
        self._parser = parser
        self._docstore = docstore

    def __call__(self, nodes: Sequence[BaseNode], **kwargs: Any) -> Sequence[BaseNode]:
        hierarchy = self._parser.get_nodes_from_documents(_documents(nodes))
        self._docstore.add_documents(hierarchy)
        return get_leaf_nodes(hierarchy)

    async def acall(self, nodes: Sequence[BaseNode], **kwargs: Any) -> Sequence[BaseNode]:
        hierarchy = self._parser.get_nodes_from_documents(_documents(nodes))
        await self._docstore.async_add_documents(hierarchy)
        return get_leaf_nodes(hierarchy)


def _documents(nodes: Sequence[BaseNode]) -> list[Document]:
    documents = [n for n in nodes if isinstance(n, Document)]
    if len(documents) != len(nodes):
        raise TypeError("El parser jerárquico debe ser la primera transformación.")
    return documents


def build_chunking(
    strategy: ChunkStrategy,
    *,
    embed_model: BaseEmbedding,
    chunk_size: int = 128,
    chunk_overlap: int = 20,
) -> list[NodeParser]:
    """Parsers a encadenar. `chunk_size` en tokens (tiktoken), no en palabras como el nativo."""
    match strategy:
        case "fixed":
            return [TokenTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)]
        case "sentence":
            return [SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)]
        case "semantic":
            # Corta donde la similitud entre frases consecutivas cae por debajo del percentil.
            # No acota el tamaño: el SentenceSplitter posterior pone el tope.
            return [
                SemanticSplitterNodeParser.from_defaults(
                    embed_model=embed_model, buffer_size=1, breakpoint_percentile_threshold=90
                ),
                SentenceSplitter(chunk_size=chunk_size * 2, chunk_overlap=0),
            ]
        case "hierarchical":
            # Hojas del mismo tamaño que las otras estrategias: comparación justa.
            return [
                HierarchicalNodeParser.from_defaults(
                    chunk_sizes=[chunk_size * 4, chunk_size * 2, chunk_size],
                    chunk_overlap=chunk_overlap,
                )
            ]


def to_llama_document(document: KnowledgeDocument) -> Document:
    metadata: dict[str, Any] = {
        **document.metadata,
        "document_id": document.id,
        "title": document.title,
        "doc_type": document.doc_type.value,
        "language": document.language,
    }
    hidden = [key for key in metadata if key not in EMBEDDED_METADATA] + ["chunk_words"]
    return Document(
        id_=document.id,
        text=document.content,
        metadata=metadata,
        excluded_embed_metadata_keys=hidden,
        excluded_llm_metadata_keys=hidden,
    )


class LlamaIndexIndexer:
    """Implementa `KnowledgeIndexer` con un `IngestionPipeline`."""

    def __init__(
        self,
        pipeline: IngestionPipeline,
        *,
        vector_store: BasePydanticVectorStore,
        docstore: BaseDocumentStore,
        embed_model: BaseEmbedding,
    ) -> None:
        self._pipeline = pipeline
        self._vector_store = vector_store
        self._docstore = docstore
        self._embed_model = embed_model

    @classmethod
    def create(
        cls,
        *,
        vector_store: BasePydanticVectorStore,
        docstore: BaseDocumentStore,
        embed_model: BaseEmbedding,
        strategy: ChunkStrategy,
        chunk_size: int = 128,
        chunk_overlap: int = 20,
        cache: IngestionCache | None = None,
    ) -> Self:
        parsers = build_chunking(
            strategy, embed_model=embed_model, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        chunking: list[TransformComponent] = [
            HierarchicalLeaves(p, docstore) if isinstance(p, HierarchicalNodeParser) else p
            for p in parsers
        ]
        transformations: list[TransformComponent] = [
            *chunking,
            MarkdownSectionExtractor(),
            embed_model,
        ]
        pipeline = IngestionPipeline(
            transformations=transformations,
            docstore=docstore,
            vector_store=vector_store,
            docstore_strategy=DocstoreStrategy.UPSERTS,
            cache=cache or IngestionCache(),
            # Un paso con efectos (guardar padres) no se puede cachear: si hay acierto en la
            # caché, el paso no se ejecuta y los padres no llegarían al docstore.
            disable_cache=strategy == "hierarchical",
        )
        return cls(pipeline, vector_store=vector_store, docstore=docstore, embed_model=embed_model)

    async def index(self, document: KnowledgeDocument) -> IndexingResult:
        # UPSERTS: hash igual -> se salta; distinto -> borra los nodos viejos y reindexa.
        nodes = await self._pipeline.arun(documents=[to_llama_document(document)])
        if not nodes:
            return IndexingResult(document.id, IndexingOutcome.UNCHANGED)
        return IndexingResult(document.id, IndexingOutcome.INDEXED, chunks_upserted=len(nodes))

    async def remove(self, document_id: str) -> IndexingResult:
        if await self._docstore.aget_document_hash(document_id) is None:
            return IndexingResult(document_id, IndexingOutcome.NOT_FOUND)
        await self._vector_store.adelete(document_id)
        await self._docstore.adelete_ref_doc(document_id, raise_error=False)
        await self._docstore.adelete_document(document_id, raise_error=False)
        return IndexingResult(document_id, IndexingOutcome.DELETED)

    async def aclose(self) -> None:
        close = getattr(self._vector_store, "close", None)
        if close is not None:
            await close()

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        embedding = await self._embed_model.aget_query_embedding(query)
        result = await self._vector_store.aquery(
            VectorStoreQuery(
                query_embedding=embedding,
                similarity_top_k=top_k,
                filters=MetadataFilters(
                    filters=[MetadataFilter(key=k, value=v) for k, v in filters.items()]
                )
                if filters
                else None,
            )
        )
        nodes = result.nodes or []
        similarities = result.similarities or [0.0] * len(nodes)
        return [
            SearchResult(
                VectorRecord(
                    id=node.node_id,
                    text=node.get_content(metadata_mode=MetadataMode.NONE),
                    embedding=[],
                    metadata={
                        k: v for k, v in node.metadata.items() if isinstance(v, str | int | float)
                    },
                ),
                score=float(score),
            )
            for node, score in zip(nodes, similarities, strict=True)
        ]


def build_postgres_indexer(
    settings: PostgresSettings,
    *,
    embed_model: BaseEmbedding,
    embed_dim: int,
    table: str,
    strategy: ChunkStrategy,
    chunk_size: int = 128,
    chunk_overlap: int = 20,
    schema: str = "llamaindex",
) -> LlamaIndexIndexer:
    """Vector store, docstore y caché en PostgreSQL, en un esquema propio del framework.

    El esquema aparte deja estas tablas fuera de Alembic: las gestiona LlamaIndex (lock-in).
    El docstore y la caché compartidos permiten varios workers (un JSON local no).
    """
    from llama_index.storage.docstore.postgres import PostgresDocumentStore
    from llama_index.storage.kvstore.postgres import PostgresKVStore
    from llama_index.vector_stores.postgres import PGVectorStore

    sync_url = settings.dsn("postgresql+psycopg2")
    async_url = settings.dsn()

    def kvstore(name: str) -> Any:
        return PostgresKVStore(
            table_name=name,
            connection_string=sync_url,
            async_connection_string=async_url,
            schema_name=schema,
            use_jsonb=True,
        )

    vector_store = PGVectorStore(
        connection_string=sync_url,
        async_connection_string=async_url,
        table_name=table,
        schema_name=schema,
        embed_dim=embed_dim,
        use_jsonb=True,
        hnsw_kwargs={
            "hnsw_m": 16,
            "hnsw_ef_construction": 64,
            "hnsw_ef_search": 40,
            "hnsw_dist_method": "vector_cosine_ops",
        },
    )
    # PostgresDocumentStore.from_params está roto en esta combinación de versiones
    # (pasa `create_engine_kwargs` a un PostgresKVStore que ya no lo acepta).
    docstore = PostgresDocumentStore(kvstore("docstore"), namespace=table)
    return LlamaIndexIndexer.create(
        vector_store=vector_store,
        docstore=docstore,
        embed_model=embed_model,
        strategy=strategy,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        cache=IngestionCache(cache=kvstore("ingestion_cache"), collection=table),
    )
