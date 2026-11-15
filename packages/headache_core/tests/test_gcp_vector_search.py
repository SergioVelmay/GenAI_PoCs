"""Agent Retrieval como almacén del carril B (3.G2) sin red, con los tipos reales del SDK.

Los dobles salen de la documentación; se sustituyen por respuestas capturadas en la primera
validación en la nube (pendientes-cloud.md, fase 3 · GCP).
"""

from __future__ import annotations

import re
from typing import Any, cast

import pytest
from google.api_core import exceptions
from google.cloud.vectorsearch_v1 import (
    BatchCreateDataObjectsRequest,
    BatchDeleteDataObjectsRequest,
    CreateDataObjectRequest,
    DataObject,
    DataObjectSearchServiceAsyncClient,
    DataObjectServiceAsyncClient,
    DistanceMetric,
    SearchDataObjectsRequest,
    SearchDataObjectsResponse,
    SearchResult,
    UpdateDataObjectRequest,
)

from headache_core.adapters.gcp_vector_search import (
    AgentRetrievalVectorStore,
    json_filter,
    object_id,
    to_data,
)
from headache_core.ports import VectorRecord

COLLECTION = "projects/headache-test/locations/europe-west4/collections/knowledge"
RECORD = VectorRecord(
    id="policy-candlelit-en::0000",
    text="Refundable up to 48 hours before the start.",
    embedding=[0.1, 0.2, 0.3],
    metadata={"document_id": "policy-candlelit-en", "language": "en", "chunk": 0, "title": "T"},
)
RFC1035 = re.compile(r"^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$")


# Las excepciones de `google.api_core` no anotan su constructor.
def already_exists() -> Exception:
    return exceptions.AlreadyExists("existe")  # type: ignore[no-untyped-call]


def not_found() -> Exception:
    return exceptions.NotFound("no existe")  # type: ignore[no-untyped-call]


class FakeObjects:
    """Colección en memoria con la semántica documentada: create falla si existe."""

    def __init__(self) -> None:
        self.stored: dict[str, DataObject] = {}
        self.calls: list[str] = []

    async def batch_create_data_objects(self, *, request: BatchCreateDataObjectsRequest) -> None:
        self.calls.append(f"batch_create {len(request.requests)}")
        # Atómico: si alguno existe, no se crea ninguno.
        if any(self._name(r) in self.stored for r in request.requests):
            raise already_exists()
        for r in request.requests:
            self.stored[self._name(r)] = r.data_object

    async def create_data_object(self, *, request: CreateDataObjectRequest) -> None:
        self.calls.append("create")
        if self._name(request) in self.stored:
            raise already_exists()
        self.stored[self._name(request)] = request.data_object

    async def update_data_object(self, *, request: UpdateDataObjectRequest) -> None:
        self.calls.append("update")
        self.stored[request.data_object.name] = request.data_object

    async def batch_delete_data_objects(self, *, request: BatchDeleteDataObjectsRequest) -> None:
        self.calls.append(f"batch_delete {len(request.requests)}")
        if any(r.name not in self.stored for r in request.requests):
            raise not_found()
        for r in request.requests:
            del self.stored[r.name]

    async def delete_data_object(self, *, name: str) -> None:
        self.calls.append("delete")
        if name not in self.stored:
            raise not_found()
        del self.stored[name]

    @staticmethod
    def _name(request: CreateDataObjectRequest) -> str:
        return f"{request.parent}/dataObjects/{request.data_object_id}"


class FakeSearch:
    def __init__(self, response: SearchDataObjectsResponse) -> None:
        self.requests: list[SearchDataObjectsRequest] = []
        self._response = response

    async def search_data_objects(
        self, *, request: SearchDataObjectsRequest
    ) -> SearchDataObjectsResponse:
        self.requests.append(request)
        return self._response


def make_store(
    response: SearchDataObjectsResponse | None = None,
) -> tuple[AgentRetrievalVectorStore, FakeObjects, FakeSearch]:
    objects, search = FakeObjects(), FakeSearch(response or SearchDataObjectsResponse())
    store = AgentRetrievalVectorStore(
        cast(DataObjectServiceAsyncClient, objects),
        cast(DataObjectSearchServiceAsyncClient, search),
        collection=COLLECTION,
    )
    return store, objects, search


def test_object_ids_are_deterministic_and_rfc1035() -> None:
    """`doc::0003` no es un id válido; el hash sí, y siempre el mismo para el mismo chunk."""
    assert object_id(RECORD.id) == object_id(RECORD.id)
    assert RFC1035.match(object_id(RECORD.id))
    assert object_id("a::0000") != object_id("a::0001")


def test_data_keeps_typed_metadata_and_flattens_filterable_fields() -> None:
    data = to_data(RECORD)

    assert data["chunk_id"] == RECORD.id
    assert (data["language"], data["chunk"]) == ("en", 0)
    assert '"chunk": 0' in data["metadata_json"]


def test_filter_uses_eq_and_and_and_rejects_undeclared_fields() -> None:
    assert json_filter({"language": "es"}) == {"language": {"$eq": "es"}}
    assert json_filter({"acl": "public", "chunk": 2}) == {
        "$and": [{"acl": {"$eq": "public"}}, {"chunk": {"$eq": 2}}]
    }
    with pytest.raises(ValueError, match="no es filtrable"):
        json_filter({"content_hash": "x"})


async def test_upsert_creates_in_batch_and_replaces_existing_chunks() -> None:
    store, objects, _ = make_store()
    await store.upsert([RECORD])

    changed = VectorRecord(
        RECORD.id, "Refundable up to 24 hours.", [0.3, 0.2, 0.1], RECORD.metadata
    )
    new = VectorRecord("policy-candlelit-en::0001", "New chunk.", [0.1, 0.1, 0.1], RECORD.metadata)
    await store.upsert([changed, new])

    assert objects.calls == ["batch_create 1", "batch_create 2", "create", "update", "create"]
    texts = sorted(str(dict(o.data)["text"]) for o in objects.stored.values())
    assert texts == ["New chunk.", "Refundable up to 24 hours."]


async def test_delete_is_idempotent() -> None:
    store, objects, _ = make_store()
    await store.upsert([RECORD])

    await store.delete([RECORD.id, "policy-candlelit-en::0009"])
    await store.delete([RECORD.id])

    assert objects.stored == {}


async def test_search_prefilters_and_converts_cosine_distance_to_score() -> None:
    data: dict[str, Any] = to_data(RECORD)
    response = SearchDataObjectsResponse(
        results=[SearchResult(data_object=DataObject(data=data), distance=0.25)]
    )
    store, _, search = make_store(response)

    results = await store.search([0.1, 0.2, 0.3], top_k=3, filters={"acl": "public"})

    (request,) = search.requests
    assert request.parent == COLLECTION
    assert request.vector_search.top_k == 3
    assert request.vector_search.distance_metric == DistanceMetric.COSINE_DISTANCE
    assert dict(request.vector_search.filter) == {"acl": {"$eq": "public"}}
    assert list(request.vector_search.output_fields.data_fields) == ["*"]
    (result,) = results
    assert result.score == pytest.approx(0.75)
    assert (result.record.id, result.record.text) == (RECORD.id, RECORD.text)
    # Los metadatos vuelven con sus tipos (el Struct convertiría `chunk` en 0.0).
    assert result.record.metadata["chunk"] == 0


def test_without_project_fails_loudly() -> None:
    from headache_core.config import GcpSettings, GcpVectorSearchSettings

    with pytest.raises(ValueError, match="GCP__PROJECT"):
        AgentRetrievalVectorStore.from_settings(GcpVectorSearchSettings(), GcpSettings())
