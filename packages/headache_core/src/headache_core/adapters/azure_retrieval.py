"""Retrieval del carril B con AI Search; clientes inyectados, sin activacion automatica."""

import json
import math
from collections.abc import Mapping
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from pydantic import TypeAdapter

from headache_core.adapters.azure_search import SEMANTIC_CONFIGURATION, document_key, odata_filter
from headache_core.application.retrieval import FILTER_KEYS, RetrievalUnavailableError
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import SearchMode
from headache_core.ports.vector_store import (
    EmbeddingPort,
    MetadataValue,
    SearchResult,
    VectorRecord,
)

METADATA = TypeAdapter(dict[str, MetadataValue])
SOURCE_FIELDS = ["chunk_id", "text", "metadata_json"]


def access_filter(filters: Mapping[str, MetadataValue], scope: AccessScope) -> str:
    if filters.keys() - FILTER_KEYS:
        raise ValueError("Filtros no permitidos: la ACL pertenece al servidor")
    if not scope.allowed_acls:
        return "false"
    acl = " or ".join(odata_filter({"acl": value}) for value in scope.allowed_acls)
    metadata = odata_filter(filters)
    return f"({acl})" + (f" and ({metadata})" if metadata else "")


def to_record(document: Mapping[str, Any]) -> VectorRecord:
    record_id, text = document.get("chunk_id"), document.get("text")
    if not isinstance(record_id, str) or not record_id or not isinstance(text, str):
        raise ValueError("Fuente AI Search invalida")
    return VectorRecord(
        record_id, text, [], METADATA.validate_python(json.loads(document["metadata_json"]))
    )


class AzureSearchRetrieval:
    def __init__(
        self,
        client: SearchClient,
        *,
        embeddings: EmbeddingPort,
        semantic: bool = False,
        semantic_configuration: str = SEMANTIC_CONFIGURATION,
    ) -> None:
        self._client = client
        self._embeddings = embeddings
        self._semantic = semantic
        self._semantic_configuration = semantic_configuration

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
    ) -> list[SearchResult]:
        if mode == "auto-merge":
            raise RetrievalUnavailableError("AI Search no implementa el auto-merging local")
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("Consulta o top_k invalido")
        if self._semantic and top_k > 50:
            raise ValueError("Semantic Ranker solo reordena hasta 50 candidatos")
        predicate = access_filter(filters, scope)
        if not scope.allowed_acls:
            return []
        kwargs: dict[str, Any] = {
            "search_text": query if mode != "vector" else None,
            "filter": predicate,
            "select": SOURCE_FIELDS,
            "top": top_k,
        }
        if mode in ("vector", "hybrid"):
            embedding = await self._embeddings.embed_query(query)
            if len(embedding) != self._embeddings.dimensions:
                raise ValueError("Dimensiones de consulta incompatibles")
            kwargs.update(
                vector_queries=[
                    VectorizedQuery(
                        vector=embedding,
                        fields="embedding",
                        k_nearest_neighbors=50 if self._semantic else top_k,
                    )
                ],
                vector_filter_mode="preFilter",
            )
        if self._semantic:
            kwargs.update(
                semantic_query=query,
                semantic_configuration_name=self._semantic_configuration,
                semantic_error_mode="fail",
            )
        documents = await self._client.search(**kwargs)
        results: list[SearchResult] = []
        seen: set[str] = set()
        async for document in documents:
            record = to_record(document)
            if not scope.permits(record.metadata) or any(
                record.metadata.get(key) != value for key, value in filters.items()
            ):
                raise ValueError("AI Search devolvio una fuente fuera del alcance autorizado")
            score = document.get("@search.rerankerScore" if self._semantic else "@search.score")
            if score is None:
                raise RetrievalUnavailableError("AI Search no devolvio el score solicitado")
            if not math.isfinite(float(score)):
                raise ValueError("AI Search devolvio un score no finito")
            if record.id not in seen:
                seen.add(record.id)
                results.append(SearchResult(record, float(score)))
            if len(results) == top_k:
                break
        return results

    async def get(self, record_id: str, *, scope: AccessScope) -> VectorRecord | None:
        if not scope.allowed_acls:
            return None
        try:
            document = await self._client.get_document(
                key=document_key(record_id),
                selected_fields=SOURCE_FIELDS,
            )
        except ResourceNotFoundError:
            return None
        record = to_record(document)
        if record.id != record_id:
            raise ValueError("AI Search devolvio un ID de fuente distinto")
        return record if scope.permits(record.metadata) else None
