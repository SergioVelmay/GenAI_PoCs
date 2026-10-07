"""Amazon S3 Vectors como `VectorStorePort` (carril B: SDK nativo boto3).

Elegido en lugar de OpenSearch Serverless por coste: se paga por almacenamiento y consulta,
sin capacidad mínima reservada (OpenSearch Serverless cobra OCUs por hora aunque no se use).

A tener en cuenta:
- El bucket y el índice (dimensión, métrica y claves no filtrables) los crea Terraform.
- Metadatos filtrables: máx. 2 KB por vector. El texto del chunk va como metadato
  NO filtrable (`text`), declarado al crear el índice.
- Filtros estilo MongoDB (`$eq`, `$and`...). Los índices ENHANCED filtran antes de buscar.
- La API devuelve distancia (coseno): score = 1 - distancia.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Self

from headache_core.config import S3VectorsSettings
from headache_core.ports import MetadataValue, SearchResult, VectorRecord

if TYPE_CHECKING:
    from mypy_boto3_s3vectors import S3VectorsClient
    from mypy_boto3_s3vectors.type_defs import PutInputVectorTypeDef

TEXT_KEY = "text"
BATCH = 500  # Máximo de vectores por PutVectors / DeleteVectors.


def s3_filter(filters: Mapping[str, MetadataValue]) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{key: {"$eq": value}} for key, value in filters.items()]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class S3VectorStore:
    def __init__(self, client: S3VectorsClient, *, bucket: str, index: str) -> None:
        self._client = client
        self._bucket = bucket
        self._index = index

    @classmethod
    def from_settings(cls, settings: S3VectorsSettings) -> Self:
        import boto3

        session = boto3.Session(profile_name=settings.profile or None, region_name=settings.region)
        return cls(
            session.client("s3vectors"), bucket=settings.bucket_name, index=settings.index_name
        )

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        vectors: list[PutInputVectorTypeDef] = [
            {
                "key": r.id,
                "data": {"float32": [float(v) for v in r.embedding]},
                "metadata": {**r.metadata, TEXT_KEY: r.text},
            }
            for r in records
        ]
        for start in range(0, len(vectors), BATCH):
            # PutVectors sobrescribe por clave: es un upsert idempotente.
            await asyncio.to_thread(
                self._client.put_vectors,
                vectorBucketName=self._bucket,
                indexName=self._index,
                vectors=vectors[start : start + BATCH],
            )

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        request: dict[str, Any] = {
            "vectorBucketName": self._bucket,
            "indexName": self._index,
            "queryVector": {"float32": [float(v) for v in query_embedding]},
            "topK": top_k,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if filters:
            request["filter"] = s3_filter(filters)
        response = await asyncio.to_thread(lambda: self._client.query_vectors(**request))
        results = []
        for vector in response["vectors"]:
            metadata = dict(vector.get("metadata", {}))
            text = str(metadata.pop(TEXT_KEY, ""))
            results.append(
                SearchResult(
                    VectorRecord(id=vector["key"], text=text, embedding=[], metadata=metadata),
                    score=1.0 - float(vector.get("distance", 1.0)),
                )
            )
        return results

    async def delete(self, ids: Sequence[str]) -> None:
        keys = list(ids)
        for start in range(0, len(keys), BATCH):
            await asyncio.to_thread(
                self._client.delete_vectors,
                vectorBucketName=self._bucket,
                indexName=self._index,
                keys=keys[start : start + BATCH],
            )
