"""Agent Retrieval (antes Vector Search 2.0) como `VectorStorePort` (carril B de GCP, 3.G2).

Una *collection* guarda objetos JSON (`data`) con sus vectores (`vectors`): el texto y los
metadatos viven junto al embedding, como en S3 Vectors y AI Search (en Vector Search 1.0, el índice
solo guardaba vectores y *restricts*). Equivalencias:

    AI Search            S3 Vectors              Agent Retrieval
    índice               bucket + índice         collection (+ índice ANN opcional)
    documento            vector (+ metadatos)    data object (data + vectors)
    filtro OData         filtro `$eq`/`$and`     filtro JSON `$eq`/`$and`

Lo que el adaptador absorbe:

- **IDs RFC1035** (1-63 caracteres, minúsculas, empieza por letra): `policy-x-es::0003` no vale.
  Se usa un hash determinista del id del chunk (`object_id`) y el id original viaja en `chunk_id`.
- **Sin upsert**: se crea en lote y, si alguno ya existe, se cae a crear o reemplazar uno a uno
  (el ledger del indexer hace que lo normal sea "todo nuevo" o "todo existente").
- **Borrado idempotente**: un objeto que ya no existe no es un error (reintentos del worker).
- **Struct de protobuf**: los números vuelven como `float`. Los metadatos se guardan además en
  `metadata_json` para devolverlos con sus tipos, como en AI Search.
- La colección (esquema de datos y de vectores) la crea Terraform (`vector_search.tf`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Self

from google.api_core import exceptions
from google.cloud.vectorsearch_v1 import (
    BatchCreateDataObjectsRequest,
    BatchDeleteDataObjectsRequest,
    CreateDataObjectRequest,
    DataObject,
    DataObjectSearchServiceAsyncClient,
    DataObjectServiceAsyncClient,
    DeleteDataObjectRequest,
    DenseVector,
    DistanceMetric,
    OutputFields,
    SearchDataObjectsRequest,
    UpdateDataObjectRequest,
    Vector,
    VectorSearch,
)

from headache_core.adapters.gcp_auth import gcp_credentials
from headache_core.config import GcpSettings, GcpVectorSearchSettings
from headache_core.ports import MetadataValue, SearchResult, VectorRecord

VECTOR_FIELD = "embedding"
BATCH = 1000  # Máximo de data objects por lote (crear, actualizar o borrar).
# Los mismos campos filtrables que el índice de AI Search (`azure_search.FILTERABLE_FIELDS`),
# declarados en el `data_schema` de la colección (vector_search.tf).
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


def object_id(chunk_id: str) -> str:
    """Id RFC1035 determinista: 'c' + 32 hex (128 bits) del SHA-256 del id del chunk."""
    return "c" + hashlib.sha256(chunk_id.encode()).hexdigest()[:32]


def to_data(record: VectorRecord) -> dict[str, Any]:
    data: dict[str, Any] = {
        "chunk_id": record.id,
        "text": record.text,
        "title": str(record.metadata.get("title", "")),
        "metadata_json": json.dumps(record.metadata, ensure_ascii=False),
    }
    chunk = record.metadata.get("chunk")
    if isinstance(chunk, int) and not isinstance(chunk, bool):
        data["chunk"] = chunk
    for field in FILTERABLE_FIELDS:
        if field in record.metadata:
            data[field] = str(record.metadata[field])
    return data


def json_filter(filters: Mapping[str, MetadataValue]) -> dict[str, Any]:
    """Filtro JSON de Agent Retrieval: un operador por clave, varias claves con `$and`."""
    clauses: list[dict[str, Any]] = []
    for key, value in filters.items():
        if key == "chunk":
            clauses.append({key: {"$eq": value}})
        elif key in FILTERABLE_FIELDS:
            # Se guardan como texto (como en AI Search): el filtro también.
            clauses.append({key: {"$eq": str(value)}})
        else:
            raise ValueError(f"'{key}' no es filtrable: no está declarado en la colección.")
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def to_record(data: Mapping[str, Any]) -> VectorRecord:
    raw = data.get("metadata_json")
    metadata: dict[str, MetadataValue] = json.loads(raw) if isinstance(raw, str) and raw else {}
    return VectorRecord(
        id=str(data.get("chunk_id", "")),
        text=str(data.get("text", "")),
        embedding=[],
        metadata=metadata,
    )


class AgentRetrievalVectorStore:
    def __init__(
        self,
        objects: DataObjectServiceAsyncClient,
        search: DataObjectSearchServiceAsyncClient,
        *,
        collection: str,
    ) -> None:
        self._objects = objects
        self._search = search
        # projects/{p}/locations/{l}/collections/{c}
        self._collection = collection

    @classmethod
    def from_settings(cls, settings: GcpVectorSearchSettings, gcp: GcpSettings) -> Self:
        if not gcp.project:
            raise ValueError("Falta GCP__PROJECT para Agent Retrieval.")
        location = settings.location or gcp.region
        credentials = gcp_credentials(gcp)
        return cls(
            DataObjectServiceAsyncClient(credentials=credentials),
            DataObjectSearchServiceAsyncClient(credentials=credentials),
            collection=(
                f"projects/{gcp.project}/locations/{location}/collections/{settings.collection}"
            ),
        )

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for start in range(0, len(records), BATCH):
            batch = records[start : start + BATCH]
            try:
                await self._objects.batch_create_data_objects(
                    request=BatchCreateDataObjectsRequest(
                        parent=self._collection,
                        requests=[self._create(record) for record in batch],
                    )
                )
            except exceptions.AlreadyExists:
                # Documento modificado: sus chunks ya existen. Uno a uno, crear o reemplazar
                # (vale tanto si el lote es atómico como si creó una parte antes de fallar).
                for record in batch:
                    await self._create_or_replace(record)

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        vector_search = VectorSearch(
            vector=DenseVector(values=[float(v) for v in query_embedding]),
            search_field=VECTOR_FIELD,
            top_k=top_k,
            output_fields=OutputFields(data_fields=["*"]),
            distance_metric=DistanceMetric.COSINE_DISTANCE,
        )
        if filters:
            vector_search.filter = json_filter(filters)
        response = await self._search.search_data_objects(
            request=SearchDataObjectsRequest(parent=self._collection, vector_search=vector_search)
        )
        return [
            # Distancia coseno: 0 = idéntico. El puerto espera similitud (mayor = mejor).
            SearchResult(to_record(dict(result.data_object.data)), score=1.0 - result.distance)
            for result in response.results
        ]

    async def delete(self, ids: Sequence[str]) -> None:
        names = [self._name(chunk_id) for chunk_id in ids]
        for start in range(0, len(names), BATCH):
            batch = names[start : start + BATCH]
            try:
                await self._objects.batch_delete_data_objects(
                    request=BatchDeleteDataObjectsRequest(
                        parent=self._collection,
                        requests=[DeleteDataObjectRequest(name=name) for name in batch],
                    )
                )
            except exceptions.NotFound:
                # Algún chunk ya no estaba (reintento del worker): se borra el resto uno a uno.
                for name in batch:
                    with contextlib.suppress(exceptions.NotFound):
                        await self._objects.delete_data_object(name=name)

    async def close(self) -> None:
        # El transporte generado no anota `close`.
        await self._objects.transport.close()  # type: ignore[no-untyped-call]
        await self._search.transport.close()  # type: ignore[no-untyped-call]

    def _name(self, chunk_id: str) -> str:
        return f"{self._collection}/dataObjects/{object_id(chunk_id)}"

    def _object(self, record: VectorRecord, *, name: str = "") -> DataObject:
        return DataObject(
            name=name,
            data=to_data(record),
            vectors={
                VECTOR_FIELD: Vector(dense=DenseVector(values=[float(v) for v in record.embedding]))
            },
        )

    def _create(self, record: VectorRecord) -> CreateDataObjectRequest:
        return CreateDataObjectRequest(
            parent=self._collection,
            data_object_id=object_id(record.id),
            data_object=self._object(record),
        )

    async def _create_or_replace(self, record: VectorRecord) -> None:
        try:
            await self._objects.create_data_object(request=self._create(record))
        except exceptions.AlreadyExists:
            # Sin máscara, el objeto enviado sustituye al guardado (datos y vectores).
            await self._objects.update_data_object(
                request=UpdateDataObjectRequest(
                    data_object=self._object(record, name=self._name(record.id))
                )
            )
