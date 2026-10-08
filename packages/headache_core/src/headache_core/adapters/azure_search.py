"""Azure AI Search como `VectorStorePort` (carril B: SDK nativo `azure-search-documents`).

Diferencias con pgvector que conviene saber explicar:
- Esquema fijo: solo se filtra por campos declarados en el índice (no hay JSONB libre).
  El resto de metadatos viaja en `metadata_json`, recuperable pero no filtrable.
- La clave del documento solo admite letras, dígitos, `_`, `-` y `=`: los ids `doc::0000`
  se codifican en base64 URL-safe y el id original va en `chunk_id`.
- `vector_filter_mode=preFilter`: filtra antes de buscar (recall completo con filtros).
- Terraform (azurerm) crea el servicio, no el índice: el índice se crea con `build_index`.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any, Self

from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from headache_core.config import AzureSearchSettings
from headache_core.ports import MetadataValue, SearchResult, VectorRecord

FILTERABLE_FIELDS = (
    "document_id",
    "doc_type",
    "language",
    "category",
    "city",
    "venue_id",
    "event_id",
    "acl",
)
VECTOR_PROFILE = "hnsw-cosine"
SEMANTIC_CONFIGURATION = "knowledge-semantic"
UPLOAD_BATCH = 1000  # Máximo de documentos por petición de indexación.


def document_key(chunk_id: str) -> str:
    return base64.urlsafe_b64encode(chunk_id.encode()).decode()


def build_index(name: str, dimensions: int) -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.STRING, key=True),
        SimpleField(name="chunk_id", type=SearchFieldDataType.STRING),
        # Texto también indexado en BM25: deja preparada la búsqueda híbrida de la fase 4.
        SearchableField(name="text", type=SearchFieldDataType.STRING),
        SearchableField(name="title", type=SearchFieldDataType.STRING),
        SearchField(
            name="embedding",
            type="Collection(Edm.Single)",
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        SimpleField(name="chunk", type=SearchFieldDataType.INT32, filterable=True),
        SimpleField(name="metadata_json", type=SearchFieldDataType.STRING),
        *(
            SimpleField(name=field, type=SearchFieldDataType.STRING, filterable=True)
            for field in FILTERABLE_FIELDS
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="hnsw", parameters=HnswParameters(m=16, ef_construction=400, metric="cosine")
            )
        ],
        profiles=[VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name="hnsw")],
    )
    return SearchIndex(
        name=name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name=SEMANTIC_CONFIGURATION,
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="text")],
                    ),
                )
            ]
        ),
    )


def to_document(record: VectorRecord) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": document_key(record.id),
        "chunk_id": record.id,
        "text": record.text,
        "title": str(record.metadata.get("title", "")),
        "embedding": record.embedding,
        "metadata_json": json.dumps(record.metadata, ensure_ascii=False),
    }
    chunk = record.metadata.get("chunk")
    if isinstance(chunk, int):
        document["chunk"] = chunk
    for field in FILTERABLE_FIELDS:
        if field in record.metadata:
            document[field] = str(record.metadata[field])
    return document


def odata_filter(filters: Mapping[str, MetadataValue]) -> str:
    clauses = []
    for key, value in filters.items():
        if key not in (*FILTERABLE_FIELDS, "chunk"):
            raise ValueError(f"'{key}' no es filtrable: no está declarado en el índice.")
        if isinstance(value, bool):
            literal = str(value).lower()
        elif isinstance(value, int | float):
            literal = str(value)
        else:
            # OData escapa la comilla simple duplicándola (evita inyección en el filtro).
            literal = "'" + str(value).replace("'", "''") + "'"
        clauses.append(f"{key} eq {literal}")
    return " and ".join(clauses)


class AzureAISearchVectorStore:
    def __init__(self, client: SearchClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: AzureSearchSettings) -> Self:
        if not settings.endpoint:
            raise ValueError("Falta AZURE_SEARCH__ENDPOINT.")
        if settings.api_key is not None:
            from azure.core.credentials import AzureKeyCredential

            credential: Any = AzureKeyCredential(settings.api_key.get_secret_value())
        else:
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()
        return cls(SearchClient(settings.endpoint, settings.index_name, credential))

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        documents = [to_document(r) for r in records]
        for start in range(0, len(documents), UPLOAD_BATCH):
            results = await self._client.merge_or_upload_documents(
                documents[start : start + UPLOAD_BATCH]
            )
            # La API devuelve 200/207 aunque fallen documentos sueltos: hay que mirarlos.
            failed = [r.key for r in results if not r.succeeded]
            if failed:
                raise RuntimeError(f"AI Search rechazó {len(failed)} documentos: {failed[:5]}")

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        results = await self._client.search(
            search_text=None,
            vector_queries=[
                VectorizedQuery(
                    vector=list(query_embedding), k_nearest_neighbors=top_k, fields="embedding"
                )
            ],
            filter=odata_filter(filters) if filters else None,
            vector_filter_mode="preFilter",
            select=["chunk_id", "text", "metadata_json"],
            top=top_k,
        )
        hits: list[SearchResult] = []
        async for doc in results:
            hits.append(
                SearchResult(
                    VectorRecord(
                        id=doc["chunk_id"],
                        text=doc["text"],
                        embedding=[],
                        metadata=json.loads(doc["metadata_json"]),
                    ),
                    # Para coseno, AI Search devuelve 1 / (1 + distancia): ordena igual,
                    # pero no es comparable en valor absoluto con pgvector.
                    score=float(doc["@search.score"]),
                )
            )
        return hits

    async def delete(self, ids: Sequence[str]) -> None:
        keys = [{"id": document_key(i)} for i in ids]
        for start in range(0, len(keys), UPLOAD_BATCH):
            await self._client.delete_documents(keys[start : start + UPLOAD_BATCH])

    async def close(self) -> None:
        await self._client.close()
