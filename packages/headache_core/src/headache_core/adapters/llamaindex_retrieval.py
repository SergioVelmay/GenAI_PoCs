"""Carril C local: retrievers LlamaIndex con los mismos chunks del carril nativo."""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.indices.vector_store.retrievers.retriever import VectorIndexRetriever
from llama_index.core.llms import CompletionResponse, CompletionResponseGen, CustomLLM, LLMMetadata
from llama_index.core.retrievers import AutoMergingRetriever, QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import BaseNode, NodeRelationship, NodeWithScore, QueryBundle, TextNode
from llama_index.retrievers.bm25 import BM25Retriever

from headache_core.adapters.llamaindex import PortEmbedding
from headache_core.adapters.local_retrieval import tokenize
from headache_core.application.retrieval import RetrievalUnavailableError
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import SearchMode
from headache_core.ports.vector_store import (
    EmbeddingPort,
    MetadataValue,
    SearchResult,
    VectorRecord,
)


class DisabledLLM(CustomLLM):
    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name="disabled-no-model")

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        raise RetrievalUnavailableError("El carril C local no puede invocar un LLM")

    def stream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseGen:
        raise RetrievalUnavailableError("El carril C local no puede invocar un LLM")


class PositiveScoreRetriever(BaseRetriever):
    def __init__(self, retriever: BaseRetriever, *, normalize_query: bool = False) -> None:
        super().__init__()
        self._retriever = retriever
        self._normalize_query = normalize_query

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        if self._normalize_query:
            query_bundle = QueryBundle(query_str=" ".join(tokenize(query_bundle.query_str)))
        return [
            result
            for result in self._retriever.retrieve(query_bundle)
            if result.score is not None and result.score > 1e-6
        ]


class PositiveVectorRetriever(VectorIndexRetriever):
    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        return [
            result
            for result in super()._retrieve(query_bundle)
            if result.score is not None and result.score > 1e-6
        ]

    async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        return [
            result
            for result in await super()._aretrieve(query_bundle)
            if result.score is not None and result.score > 1e-6
        ]


class LlamaIndexRetrieval:
    def __init__(
        self,
        records: Sequence[VectorRecord],
        *,
        embeddings: EmbeddingPort,
        parents: Sequence[VectorRecord] = (),
        merge_ratio: float = 0.5,
    ) -> None:
        if not 0 < merge_ratio < 1:
            raise ValueError("merge_ratio debe estar entre 0 y 1")
        self._merge_ratio = merge_ratio
        self._records = tuple(records)
        self._parents = tuple(parents)
        self._embeddings = embeddings
        self._sources = {record.id: record for record in (*records, *parents)}
        if len(self._sources) != len(records) + len(parents):
            raise ValueError("IDs de fuentes duplicados")
        if any(len(record.embedding) != embeddings.dimensions for record in records):
            raise ValueError("Dimensiones incompatibles con el modelo de embeddings")

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> list[SearchResult]:
        if top_k < 1:
            raise ValueError("top_k debe ser positivo")
        candidates = [
            record
            for record in self._records
            if scope.permits(record.metadata)
            and all(record.metadata.get(key) == value for key, value in filters.items())
            and tokenize(record.text)
        ]
        if not candidates or not tokenize(query):
            return []
        embedding = await self._embeddings.embed_query(query) if mode != "bm25" else None
        if embedding is not None and len(embedding) != self._embeddings.dimensions:
            raise ValueError("Dimensiones de consulta incompatibles")
        parents = [
            record
            for record in self._parents
            if scope.permits(record.metadata)
            and all(record.metadata.get(key) == value for key, value in filters.items())
        ]
        return await asyncio.to_thread(
            self._retrieve, query, embedding, candidates, parents, mode, top_k
        )

    def _retrieve(
        self,
        query: str,
        embedding: list[float] | None,
        records: Sequence[VectorRecord],
        parents: Sequence[VectorRecord],
        mode: SearchMode,
        top_k: int,
    ) -> list[SearchResult]:
        nodes: list[BaseNode] = [
            TextNode(
                id_=record.id,
                text=record.text,
                embedding=list(record.embedding),
                metadata=dict(record.metadata),
                excluded_embed_metadata_keys=list(record.metadata),
                excluded_llm_metadata_keys=list(record.metadata),
            )
            for record in records
        ]
        retrievers: list[BaseRetriever] = []
        limit = min(top_k, len(nodes))
        if mode in ("bm25", "hybrid"):
            lexical_nodes = [
                node.model_copy(update={"text": " ".join(tokenize(record.text))})
                for node, record in zip(nodes, records, strict=True)
            ]
            retrievers.append(
                PositiveScoreRetriever(
                    BM25Retriever.from_defaults(
                        nodes=lexical_nodes,
                        similarity_top_k=limit,
                        skip_stemming=True,
                        token_pattern=r"(?u)\b\w+\b",
                    ),
                    normalize_query=True,
                )
            )
        if mode in ("vector", "hybrid", "auto-merge"):
            storage = StorageContext.from_defaults()
            if mode == "auto-merge":
                parent_nodes: list[BaseNode] = []
                for parent in parents:
                    children = [
                        node for node in nodes if node.metadata.get("parent_id") == parent.id
                    ]
                    if not children:
                        continue
                    parent_node = TextNode(
                        id_=parent.id,
                        text=parent.text,
                        metadata=dict(parent.metadata),
                        excluded_embed_metadata_keys=list(parent.metadata),
                        excluded_llm_metadata_keys=list(parent.metadata),
                    )
                    parent_node.relationships[NodeRelationship.CHILD] = [
                        child.as_related_node_info() for child in children
                    ]
                    for child in children:
                        child.relationships[NodeRelationship.PARENT] = (
                            parent_node.as_related_node_info()
                        )
                    parent_nodes.append(parent_node)
                storage.docstore.add_documents(parent_nodes)
            index = VectorStoreIndex(
                nodes,
                embed_model=PortEmbedding(
                    self._embeddings, model_name=f"port-{self._embeddings.dimensions}"
                ),
                storage_context=storage,
            )
            vector_retriever = PositiveVectorRetriever(index=index, similarity_top_k=limit)
            if mode == "auto-merge":
                retrievers.append(
                    PositiveScoreRetriever(
                        AutoMergingRetriever(
                            vector_retriever,
                            storage,
                            simple_ratio_thresh=self._merge_ratio,
                        )
                    )
                )
            else:
                retrievers.append(vector_retriever)
        retriever = retrievers[0]
        if mode == "hybrid":
            retriever = QueryFusionRetriever(
                retrievers,
                llm=DisabledLLM(),
                mode=FUSION_MODES.RECIPROCAL_RANK,
                num_queries=1,
                use_async=False,
                similarity_top_k=limit,
            )
        results = retriever.retrieve(QueryBundle(query_str=query, embedding=embedding))
        by_id = {record.id: record for record in (*records, *parents)}
        return [
            SearchResult(by_id[result.node.node_id], float(result.score or 0.0))
            for result in results
            if result.node.node_id in by_id
        ][:top_k]

    async def get(self, record_id: str, *, scope: AccessScope) -> VectorRecord | None:
        record = self._sources.get(record_id)
        return record if record is not None and scope.permits(record.metadata) else None
