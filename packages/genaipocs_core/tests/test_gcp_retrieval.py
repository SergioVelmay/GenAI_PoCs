"""Carril B de GCP (4.G1): Agent Retrieval como `RetrievalPort` y Ranking API como reranker.

Sin red, con los tipos reales de los SDK (`google-cloud-vectorsearch` 0.11 y
`google-cloud-discoveryengine` 0.20). Los dobles salen de la documentación; se sustituyen por
respuestas capturadas en la primera validación en la nube (pendientes-cloud.md, fase 4 · GCP).
"""

from __future__ import annotations

import json
import math
from typing import Any, cast

import pytest
from google.api_core import exceptions
from google.cloud.discoveryengine_v1 import RankingRecord, RankRequest, RankResponse
from google.cloud.vectorsearch_v1 import (
    BatchSearchDataObjectsRequest,
    BatchSearchDataObjectsResponse,
    DataObject,
    DataObjectSearchServiceAsyncClient,
    DataObjectServiceAsyncClient,
    DistanceMetric,
    SearchDataObjectsRequest,
    SearchDataObjectsResponse,
    SearchResult,
)

from genaipocs_core.adapters.gcp_retrieval import (
    VERTEX_RANKER_MODEL,
    AgentRetrievalSearch,
    VertexRankingReranker,
    access_filter,
)
from genaipocs_core.adapters.gcp_vector_search import object_id
from genaipocs_core.adapters.memory import HashingEmbedding
from genaipocs_core.application.retrieval import (
    RetrievalRequest,
    RetrievalService,
    RetrievalUnavailableError,
)
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.retrieval import SearchMode
from genaipocs_core.ports.vector_store import SearchResult as PortResult
from genaipocs_core.ports.vector_store import VectorRecord

COLLECTION = "projects/genaipocs-test/locations/europe-west4/collections/knowledge"
RANKING_CONFIG = "projects/genaipocs-test/locations/global/rankingConfigs/default_ranking_config"


def data(chunk_id: str = "policy-candlelit-en::0000", acl: str | None = "public") -> DataObject:
    metadata: dict[str, Any] = {
        "document_id": chunk_id.split("::")[0],
        "language": "en",
        "category": "candlelit",
        "title": "Refunds",
    }
    if acl is not None:
        metadata["acl"] = acl
    return DataObject(
        name=f"{COLLECTION}/dataObjects/{object_id(chunk_id)}",
        data={
            "chunk_id": chunk_id,
            "text": "Refundable up to 48 hours before the start.",
            "title": "Refunds",
            "metadata_json": json.dumps(metadata),
            **{key: str(value) for key, value in metadata.items() if key != "title"},
        },
    )


def result(distance: float | None = 0.2, **kwargs: Any) -> SearchResult:
    item = SearchResult(data_object=data(**kwargs))
    if distance is not None:
        item.distance = distance
    return item


class SearchDouble:
    """Captura las peticiones; responde lo que el test le prepara."""

    def __init__(self, *results: SearchResult, lists: int = 1) -> None:
        self.results = list(results)
        self.lists = lists
        self.single: list[SearchDataObjectsRequest] = []
        self.batch: list[BatchSearchDataObjectsRequest] = []

    async def search_data_objects(
        self, *, request: SearchDataObjectsRequest
    ) -> SearchDataObjectsResponse:
        self.single.append(request)
        return SearchDataObjectsResponse(results=self.results)

    async def batch_search_data_objects(
        self, *, request: BatchSearchDataObjectsRequest
    ) -> BatchSearchDataObjectsResponse:
        self.batch.append(request)
        response = SearchDataObjectsResponse(results=self.results)
        return BatchSearchDataObjectsResponse(results=[response] * self.lists)


class ObjectsDouble:
    def __init__(self, stored: DataObject | None = None) -> None:
        self.stored = stored
        self.names: list[str] = []

    async def get_data_object(self, *, name: str) -> DataObject:
        self.names.append(name)
        if self.stored is None:
            raise exceptions.NotFound("no existe")  # type: ignore[no-untyped-call]
        return self.stored


def backend(
    search: SearchDouble, objects: ObjectsDouble | None = None, *, semantic: bool = False
) -> AgentRetrievalSearch:
    return AgentRetrievalSearch(
        cast(DataObjectSearchServiceAsyncClient, search),
        cast(DataObjectServiceAsyncClient, objects or ObjectsDouble()),
        collection=COLLECTION,
        embeddings=HashingEmbedding(16),
        semantic=semantic,
    )


def as_dict(value: Any) -> Any:
    """Struct de protobuf → dict (los filtros viajan como `google.protobuf.Struct`)."""
    if hasattr(value, "items"):
        return {key: as_dict(item) for key, item in value.items()}
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        return value
    return [as_dict(item) for item in value]


# ---------- Filtro del servidor ----------


def test_access_filter_puts_acl_first_and_rejects_acl_injection() -> None:
    assert access_filter({}, AccessScope()) == {"acl": {"$in": ["public"]}}
    assert access_filter(
        {"language": "en", "category": "candlelit"}, AccessScope(("public", "internal"))
    ) == {
        "$and": [
            {"acl": {"$in": ["public", "internal"]}},
            {"category": {"$eq": "candlelit"}},
            {"language": {"$eq": "en"}},
        ]
    }
    with pytest.raises(ValueError, match="ACL pertenece al servidor"):
        access_filter({"acl": "internal"}, AccessScope())


# ---------- Modos de búsqueda ----------


async def test_vector_alone_is_a_single_cosine_search_with_prefilter() -> None:
    search = SearchDouble(result(distance=0.25))

    results = await backend(search).search(
        "refund", mode="vector", top_k=3, filters={"language": "en"}, scope=AccessScope()
    )

    assert not search.batch and len(search.single) == 1
    vector = search.single[0].vector_search
    assert vector.top_k == 3 and vector.distance_metric == DistanceMetric.COSINE_DISTANCE
    assert vector.search_field == "embedding" and len(vector.vector.values) == 16
    assert as_dict(vector.filter) == {
        "$and": [{"acl": {"$in": ["public"]}}, {"language": {"$eq": "en"}}]
    }
    assert list(vector.output_fields.data_fields) == ["chunk_id", "text", "metadata_json"]
    # Distancia coseno → similitud, como en el almacén de la 3.G2.
    assert results[0].record.id == "policy-candlelit-en::0000"
    assert math.isclose(results[0].score, 0.75)


@pytest.mark.parametrize(
    ("mode", "kinds"),
    [("bm25", ["text_search"]), ("hybrid", ["vector_search", "text_search"])],
)
async def test_text_and_hybrid_go_through_batch_with_rrf(
    mode: SearchMode, kinds: list[str]
) -> None:
    search = SearchDouble(result(distance=0.032))

    results = await backend(search).search(
        "refund", mode=mode, top_k=5, filters={}, scope=AccessScope()
    )

    request = search.batch[0]
    assert request.parent == COLLECTION and not search.single
    assert [search_kind(s) for s in request.searches] == kinds
    for item in request.searches:
        sub = getattr(item, search_kind(item))
        assert sub.top_k == 5 and as_dict(sub.filter) == {"acl": {"$in": ["public"]}}
    text = request.searches[-1].text_search
    assert text.search_text == "refund" and list(text.data_field_names) == ["title", "text"]
    ranker = request.combine.ranker
    assert list(ranker.rrf.weights) == [1.0] * len(kinds) and "vertex_ranker" not in ranker
    assert request.combine.top_k == 5
    # Con ranker, el score es el del ranker (RRF): se expone tal cual, sin invertirlo.
    assert results[0].score == pytest.approx(0.032)


def search_kind(search: Any) -> str:
    return "vector_search" if "vector_search" in search else "text_search"


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
async def test_semantic_uses_vertex_ranker_over_a_window_of_50(mode: SearchMode) -> None:
    search = SearchDouble(result(distance=0.91))

    await backend(search, semantic=True).search(
        "refund", mode=mode, top_k=5, filters={}, scope=AccessScope()
    )

    request = search.batch[0]
    assert all(getattr(s, search_kind(s)).top_k == 50 for s in request.searches)
    vertex = request.combine.ranker.vertex_ranker
    assert vertex.model == VERTEX_RANKER_MODEL and vertex.top_n == 50
    assert vertex.text_record_spec.query == "refund"
    assert vertex.text_record_spec.content_template == "{text}"
    assert request.combine.top_k == 5


async def test_semantic_window_and_invalid_requests_fail_before_calling() -> None:
    search = SearchDouble()
    with pytest.raises(ValueError, match="50 candidatos"):
        await backend(search, semantic=True).search(
            "refund", mode="hybrid", top_k=51, filters={}, scope=AccessScope()
        )
    for query, top_k in (("   ", 5), ("refund", 0), ("refund", 101)):
        with pytest.raises(ValueError, match="top_k"):
            await backend(search).search(
                query, mode="hybrid", top_k=top_k, filters={}, scope=AccessScope()
            )
    with pytest.raises(RetrievalUnavailableError, match="auto-merging"):
        await backend(search).search(
            "refund", mode="auto-merge", top_k=5, filters={}, scope=AccessScope()
        )
    assert not search.single and not search.batch


async def test_empty_scope_denies_without_calling() -> None:
    search = SearchDouble(result())
    assert (
        await backend(search).search(
            "refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope(())
        )
        == []
    )
    assert (
        await backend(search, ObjectsDouble(data())).get(
            "policy-candlelit-en::0000", scope=AccessScope(())
        )
        is None
    )
    assert not search.batch


# ---------- Defensa en profundidad: nada se acepta en silencio ----------


@pytest.mark.parametrize("acl", ["internal", None])
async def test_source_outside_scope_is_an_error_not_a_silent_drop(acl: str | None) -> None:
    search = SearchDouble(result(acl=acl))
    with pytest.raises(ValueError, match="fuera del alcance"):
        await backend(search).search(
            "refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope()
        )


async def test_source_that_breaks_the_filter_is_an_error() -> None:
    search = SearchDouble(result())
    with pytest.raises(ValueError, match="no cumple los filtros"):
        await backend(search).search(
            "refund", mode="hybrid", top_k=5, filters={"language": "es"}, scope=AccessScope()
        )


async def test_missing_score_is_not_a_zero() -> None:
    search = SearchDouble(result(distance=None))
    with pytest.raises(RetrievalUnavailableError, match="score"):
        await backend(search).search(
            "refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope()
        )


@pytest.mark.parametrize("score", [math.nan, math.inf])
async def test_nonfinite_score_is_rejected(score: float) -> None:
    with pytest.raises(ValueError, match="no finito"):
        await backend(SearchDouble(result(distance=score))).search(
            "refund", mode="bm25", top_k=5, filters={}, scope=AccessScope()
        )


async def test_batch_without_a_single_combined_list_is_rejected() -> None:
    search = SearchDouble(result(), lists=2)
    with pytest.raises(RetrievalUnavailableError, match="lista combinada"):
        await backend(search).search(
            "refund", mode="hybrid", top_k=5, filters={}, scope=AccessScope()
        )


async def test_duplicates_collapse_and_top_k_is_respected() -> None:
    search = SearchDouble(
        result(distance=0.9),
        result(distance=0.8),
        result(distance=0.7, chunk_id="faq-candlelit-en::0000"),
        result(distance=0.6, chunk_id="faq-candlelit-en::0001"),
    )
    results = await backend(search).search(
        "refund", mode="hybrid", top_k=2, filters={}, scope=AccessScope()
    )
    assert [r.record.id for r in results] == ["policy-candlelit-en::0000", "faq-candlelit-en::0000"]


# ---------- get (citas y parent-child) ----------


async def test_get_resolves_by_hashed_id_and_checks_acl() -> None:
    objects = ObjectsDouble(data())
    record = await backend(SearchDouble(), objects).get(
        "policy-candlelit-en::0000", scope=AccessScope()
    )
    assert record is not None and record.metadata["acl"] == "public"
    assert objects.names == [f"{COLLECTION}/dataObjects/{object_id('policy-candlelit-en::0000')}"]

    private = backend(SearchDouble(), ObjectsDouble(data(acl="internal")))
    assert await private.get("policy-candlelit-en::0000", scope=AccessScope()) is None
    assert await backend(SearchDouble()).get("missing::0000", scope=AccessScope()) is None
    with pytest.raises(ValueError, match="ID de fuente distinto"):
        await backend(SearchDouble(), ObjectsDouble(data())).get("other::0000", scope=AccessScope())


# ---------- Ranking API ----------


def candidate(index: int) -> PortResult:
    return PortResult(
        VectorRecord(f"doc-{index}::0000", f"text {index}", [], {"title": f"T{index}"}), 0.1
    )


class RankDouble:
    def __init__(self, *records: tuple[str, float]) -> None:
        self.records = records
        self.requests: list[RankRequest] = []

    async def rank(self, *, request: RankRequest) -> RankResponse:
        self.requests.append(request)
        return RankResponse(records=[RankingRecord(id=i, score=s) for i, s in self.records])


def reranker(client: RankDouble) -> VertexRankingReranker:
    return VertexRankingReranker(
        cast(Any, client), ranking_config=RANKING_CONFIG, model="semantic-ranker-default-004"
    )


async def test_ranking_keeps_original_records_and_sorts_by_score() -> None:
    client = RankDouble(("2", 0.4), ("0", 0.9))
    candidates = [candidate(i) for i in range(3)]

    ranked = await reranker(client).rerank("refund", candidates, top_k=2)

    request = client.requests[0]
    assert request.ranking_config == RANKING_CONFIG
    assert request.model == "semantic-ranker-default-004" and request.top_n == 2
    assert request.ignore_record_details_in_response
    assert [(r.id, r.title, r.content) for r in request.records] == [
        ("0", "T0", "text 0"),
        ("1", "T1", "text 1"),
        ("2", "T2", "text 2"),
    ]
    assert [r.record for r in ranked] == [candidates[0].record, candidates[2].record]
    assert [r.score for r in ranked] == pytest.approx([0.9, 0.4])


@pytest.mark.parametrize(
    "records",
    [[("3", 0.5)], [("0", 0.5), ("0", 0.4)], [("x", 0.5)], [("-1", 0.5)], [("0", math.nan)]],
)
async def test_ranking_rejects_unknown_duplicated_or_nonfinite_records(
    records: list[tuple[str, float]],
) -> None:
    with pytest.raises(ValueError, match="Ranking API"):
        await reranker(RankDouble(*records)).rerank(
            "refund", [candidate(i) for i in range(3)], top_k=3
        )


async def test_ranking_validates_input_before_calling() -> None:
    client = RankDouble()
    assert await reranker(client).rerank("refund", [], top_k=3) == []
    with pytest.raises(ValueError, match="candidatos"):
        await reranker(client).rerank("refund", [candidate(i) for i in range(201)], top_k=3)
    with pytest.raises(ValueError, match="candidatos"):
        await reranker(client).rerank(" ", [candidate(0)], top_k=3)
    assert not client.requests


async def test_service_reranks_only_authorized_candidates_with_ranking_api() -> None:
    """De extremo a extremo con `RetrievalService`: estrategia `rerank` sobre Agent Retrieval."""
    search = SearchDouble(
        result(distance=0.03), result(distance=0.02, chunk_id="faq-candlelit-en::0000")
    )
    client = RankDouble(("1", 0.8), ("0", 0.3))
    service = RetrievalService(backend(search), reranker=reranker(client))

    response = await service.retrieve(
        RetrievalRequest("refund", strategy="rerank", top_k=1, candidate_k=10)
    )

    assert search.batch[0].combine.top_k == 10
    assert [r.record.id for r in response.results] == ["faq-candlelit-en::0000"]
    assert response.results[0].score == pytest.approx(0.8)
