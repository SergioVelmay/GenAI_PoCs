"""Retrieval del carril B de GCP (fase 4.G1): Agent Retrieval y Ranking API, clientes inyectados.

`AgentRetrievalSearch` es el `RetrievalPort` sobre la colección que llena el indexer (3.G2), como
`AzureSearchRetrieval` sobre el índice de AI Search. `VertexRankingReranker` es el `RerankerPort`
con la Ranking API, como `BedrockReranker`.

    AI Search                 Agent Retrieval
    search_text               TextSearch (texto completo sobre `title` y `text`)
    VectorizedQuery           VectorSearch (distancia coseno)
    híbrida con RRF           batch_search_data_objects: varias búsquedas + Ranker(rrf)
    Semantic Ranker           VertexRanker dentro del batch (semantic-ranker-fast)
    filtro OData (prefiltro)  filtro JSON (`$in`, `$eq`, `$and`) en cada búsqueda

Dos reglas del carril B, iguales en las tres nubes:

- **La ACL viaja como filtro del servidor y se vuelve a comprobar en cada resultado.** Un
  resultado fuera del alcance es un error, no algo que se descarta en silencio: indica que el
  filtro no se aplicó.
- **Nada degrada en silencio.** Un score ausente o no finito es un error. El VertexRanker
  integrado sí degrada (si el ranking falla, el batch devuelve el RRF sin avisar): por eso el
  reranker por defecto de GCP es la Ranking API, que falla de forma explícita, y el integrado
  queda como motor de la comparativa (`semantic=True`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from google.api_core import exceptions
from google.cloud.discoveryengine_v1 import RankingRecord, RankRequest, RankServiceAsyncClient
from google.cloud.vectorsearch_v1 import (
    BatchSearchDataObjectsRequest,
    DataObjectSearchServiceAsyncClient,
    DataObjectServiceAsyncClient,
    DenseVector,
    DistanceMetric,
    OutputFields,
    Ranker,
    ReciprocalRankFusion,
    Search,
    SearchDataObjectsRequest,
    TextSearch,
    VectorSearch,
    VertexRanker,
)
from google.cloud.vectorsearch_v1 import SearchResult as DataObjectResult

from genaipocs_core.adapters.gcp_vector_search import VECTOR_FIELD, object_id, to_record
from genaipocs_core.application.retrieval import FILTER_KEYS, RetrievalUnavailableError
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.retrieval import SearchMode
from genaipocs_core.ports.vector_store import (
    EmbeddingPort,
    MetadataValue,
    SearchResult,
    VectorRecord,
)

# Solo lo que necesita el puerto: el embedding no se devuelve (ahorra ~4 KB por resultado).
OUTPUT_FIELDS = ["chunk_id", "text", "metadata_json"]
TEXT_FIELDS = ["title", "text"]
# Único modelo admitido por el VertexRanker integrado (verificado en el SDK 0.11.6).
VERTEX_RANKER_MODEL = "semantic-ranker-fast@latest"
# Candidatos que el ranker integrado reordena, como la ventana del Semantic Ranker de AI Search.
RANKER_WINDOW = 50
# Registros por petición de la Ranking API.
MAX_RANKING_RECORDS = 200


def access_filter(filters: Mapping[str, MetadataValue], scope: AccessScope) -> dict[str, Any]:
    """Filtro JSON del servidor: la ACL del scope y los filtros de la petición, con `$and`.

    La ACL va con `$in` (un solo nivel); el filtro admite como mucho dos niveles de anidación.
    """
    if filters.keys() - FILTER_KEYS:
        raise ValueError("Filtros no permitidos: la ACL pertenece al servidor")
    clauses: list[dict[str, Any]] = [{"acl": {"$in": list(scope.allowed_acls)}}]
    # Los campos filtrables se guardan como texto (`gcp_vector_search.to_data`).
    clauses.extend({key: {"$eq": str(value)}} for key, value in sorted(filters.items()))
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class AgentRetrievalSearch:
    def __init__(
        self,
        search: DataObjectSearchServiceAsyncClient,
        objects: DataObjectServiceAsyncClient,
        *,
        collection: str,
        embeddings: EmbeddingPort,
        semantic: bool = False,
        ranker_model: str = VERTEX_RANKER_MODEL,
    ) -> None:
        self._search = search
        self._objects = objects
        # projects/{p}/locations/{l}/collections/{c}
        self._collection = collection
        self._embeddings = embeddings
        self._semantic = semantic
        self._ranker_model = ranker_model

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
            raise RetrievalUnavailableError("Agent Retrieval no implementa el auto-merging local")
        if not query.strip() or not 1 <= top_k <= 100:
            raise ValueError("Consulta o top_k invalido")
        if self._semantic and top_k > RANKER_WINDOW:
            raise ValueError(f"El VertexRanker solo reordena hasta {RANKER_WINDOW} candidatos")
        predicate = access_filter(filters, scope)
        if not scope.allowed_acls:
            return []
        if mode == "vector" and not self._semantic:
            # Única búsqueda con una escala conocida: distancia coseno (0 = idéntico).
            vector = await self._vector(query, top_k, predicate)
            pager = await self._search.search_data_objects(
                request=SearchDataObjectsRequest(parent=self._collection, vector_search=vector)
            )
            # Solo la primera página (ya acotada por `top_k`): iterar el pager pediría más.
            return self._results(pager.results, top_k, filters, scope, cosine=True)
        response = await self._search.batch_search_data_objects(
            request=await self._batch(query, mode, top_k, predicate)
        )
        if len(response.results) != 1:
            # Con un ranker, el servicio devuelve una sola lista combinada.
            raise RetrievalUnavailableError("Agent Retrieval no devolvio una lista combinada")
        return self._results(response.results[0].results, top_k, filters, scope, cosine=False)

    async def get(self, record_id: str, *, scope: AccessScope) -> VectorRecord | None:
        if not scope.allowed_acls:
            return None
        try:
            data_object = await self._objects.get_data_object(
                name=f"{self._collection}/dataObjects/{object_id(record_id)}"
            )
        except exceptions.NotFound:
            return None
        record = to_record(dict(data_object.data))
        if record.id != record_id:
            raise ValueError("Agent Retrieval devolvio un ID de fuente distinto")
        return record if scope.permits(record.metadata) else None

    async def close(self) -> None:
        # El transporte generado no anota `close`.
        await self._search.transport.close()  # type: ignore[no-untyped-call]
        await self._objects.transport.close()  # type: ignore[no-untyped-call]

    async def _vector(self, query: str, top_k: int, predicate: dict[str, Any]) -> VectorSearch:
        embedding = await self._embeddings.embed_query(query)
        if len(embedding) != self._embeddings.dimensions:
            raise ValueError("Dimensiones de consulta incompatibles")
        return VectorSearch(
            vector=DenseVector(values=[float(v) for v in embedding]),
            search_field=VECTOR_FIELD,
            top_k=top_k,
            filter=predicate,
            output_fields=OutputFields(data_fields=OUTPUT_FIELDS),
            distance_metric=DistanceMetric.COSINE_DISTANCE,
        )

    async def _batch(
        self, query: str, mode: SearchMode, top_k: int, predicate: dict[str, Any]
    ) -> BatchSearchDataObjectsRequest:
        """BM25, híbrida o cualquier modo con ranker: el score es siempre el del ranker (RRF o
        VertexRanker), documentado en `SearchResult.distance` del batch."""
        candidates = RANKER_WINDOW if self._semantic else top_k
        searches: list[Search] = []
        if mode in ("vector", "hybrid"):
            searches.append(Search(vector_search=await self._vector(query, candidates, predicate)))
        if mode in ("bm25", "hybrid"):
            searches.append(
                Search(
                    text_search=TextSearch(
                        search_text=query,
                        data_field_names=TEXT_FIELDS,
                        top_k=candidates,
                        filter=predicate,
                        output_fields=OutputFields(data_fields=OUTPUT_FIELDS),
                    )
                )
            )
        ranker = Ranker(rrf=ReciprocalRankFusion(weights=[1.0] * len(searches)))
        if self._semantic:
            ranker.vertex_ranker = VertexRanker(
                model=self._ranker_model,
                top_n=RANKER_WINDOW,
                text_record_spec=VertexRanker.TextRecordSpec(
                    query=query, title_template="{title}", content_template="{text}"
                ),
            )
        return BatchSearchDataObjectsRequest(
            parent=self._collection,
            searches=searches,
            combine=BatchSearchDataObjectsRequest.CombineResultsOptions(
                ranker=ranker,
                top_k=top_k,
                output_fields=OutputFields(data_fields=OUTPUT_FIELDS),
            ),
        )

    @staticmethod
    def _results(
        found: Iterable[DataObjectResult],
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        *,
        cosine: bool,
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        for result in found:
            record = to_record(dict(result.data_object.data))
            if not record.id or not scope.permits(record.metadata):
                raise ValueError("Agent Retrieval devolvio una fuente fuera del alcance autorizado")
            if any(record.metadata.get(key) != value for key, value in filters.items()):
                raise ValueError("Agent Retrieval devolvio una fuente que no cumple los filtros")
            # `distance` es opcional en el proto: ausente no puede convertirse en un 0.
            if "distance" not in result:
                raise RetrievalUnavailableError("Agent Retrieval no devolvio el score")
            score = 1.0 - result.distance if cosine else result.distance
            if not math.isfinite(score):
                raise ValueError("Agent Retrieval devolvio un score no finito")
            if record.id not in seen:
                seen.add(record.id)
                results.append(SearchResult(record, score))
            if len(results) == top_k:
                break
        return results


class VertexRankingReranker:
    """Ranking API (`semantic-ranker-default-004`). Solo recibe candidatos ya autorizados por
    `RetrievalService`: envía su texto y aplica el orden devuelto sobre los registros originales."""

    def __init__(self, client: RankServiceAsyncClient, *, ranking_config: str, model: str) -> None:
        self._client = client
        # projects/{p}/locations/global/rankingConfigs/default_ranking_config
        self._ranking_config = ranking_config
        self._model = model

    async def rerank(
        self, query: str, candidates: Sequence[SearchResult], *, top_k: int
    ) -> list[SearchResult]:
        if not query.strip() or top_k < 1 or len(candidates) > MAX_RANKING_RECORDS:
            raise ValueError("Consulta, top_k o numero de candidatos invalido")
        if not candidates:
            return []
        response = await self._client.rank(
            request=RankRequest(
                ranking_config=self._ranking_config,
                model=self._model,
                top_n=min(top_k, len(candidates)),
                query=query,
                records=[
                    # El id es la posición: el servicio no ve nuestros identificadores.
                    RankingRecord(
                        id=str(index),
                        title=str(item.record.metadata.get("title", "")),
                        content=item.record.text,
                    )
                    for index, item in enumerate(candidates)
                ],
                # Solo id y score: el texto devuelto nunca sustituye al original.
                ignore_record_details_in_response=True,
            )
        )
        ranked: list[tuple[float, int]] = []
        seen: set[int] = set()
        for record in response.records:
            index = int(record.id) if record.id.isdigit() else -1
            if not 0 <= index < len(candidates) or index in seen or not math.isfinite(record.score):
                raise ValueError("Id o score invalido en la Ranking API")
            seen.add(index)
            ranked.append((record.score, index))
        # Orden por score descendente, estable ante empates (no se confía en el orden recibido).
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [SearchResult(candidates[index].record, score) for score, index in ranked[:top_k]]

    async def close(self) -> None:
        await self._client.transport.close()  # type: ignore[no-untyped-call]
