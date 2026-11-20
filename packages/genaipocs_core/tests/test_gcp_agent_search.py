"""Carril A de GCP con Agent Search (4.G3): ingesta HTML + importación FULL, `search` y
`answer_query`. Sin red, con los tipos reales de `google-cloud-discoveryengine` 0.20.

Los dobles salen de la documentación (troceo y modo CHUNKS, respuestas con citas); se
sustituyen por respuestas capturadas en la primera validación en la nube (pendientes-cloud.md).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from google.cloud.discoveryengine_v1 import (
    Answer,
    AnswerQueryRequest,
    AnswerQueryResponse,
    Chunk,
    ImportDocumentsMetadata,
    ImportDocumentsRequest,
    ImportDocumentsResponse,
    SearchRequest,
    SearchResponse,
)

from genaipocs_core.adapters.gcp_agent_search import (
    AgentSearchIngestion,
    AgentSearchKnowledge,
    discovery_engine_options,
    html_md5,
    manifest,
    object_name,
    render_html,
    search_filter,
    struct_data,
)
from genaipocs_core.application.retrieval import InvalidCitationError, RetrievalUnavailableError
from genaipocs_core.domain import DocumentType, KnowledgeDocument
from genaipocs_core.domain.retrieval import AccessScope

BUCKET = "genaipocs-dev-rag-docs"
BRANCH = "projects/p/locations/eu/collections/default_collection/dataStores/knowledge/branches/0"
SERVING = (
    "projects/p/locations/eu/collections/default_collection/engines/knowledge-search"
    "/servingConfigs/default_search"
)
INTERNAL = AccessScope(("public", "internal"))


def doc(
    doc_id: str = "policy-candlelit-es",
    *,
    acl: str | None = "public",
    doc_type: DocumentType = DocumentType.POLICY,
    content: str = "# Reembolsos\nTexto.",
) -> KnowledgeDocument:
    metadata = {"category": "candlelit", "city": "madrid"} | ({"acl": acl} if acl else {})
    return KnowledgeDocument(
        id=doc_id,
        title="Reembolsos <candlelit>",
        doc_type=doc_type,
        language="es",
        content=content,
        metadata=metadata,
    )


# ---------- Ingesta ----------


def test_html_keeps_structure_and_escapes() -> None:
    page = render_html(
        doc(content="# Política\nIntro <b>x</b>.\n\n- Uno\n- Dos & tres\n## Detalle\nFin.")
    )
    assert '<html lang="es">' in page and "<title>Reembolsos &lt;candlelit&gt;</title>" in page
    assert "<h1>Política</h1>\n<p>Intro &lt;b&gt;x&lt;/b&gt;.</p>" in page
    assert "<ul>\n<li>Uno</li>\n<li>Dos &amp; tres</li>\n</ul>\n<h2>Detalle</h2>" in page


def test_struct_data_has_acl_filters_and_title_as_text() -> None:
    assert struct_data(doc()) == {
        "acl": "public",
        "category": "candlelit",
        "city": "madrid",
        "doc_type": "policy",
        "document_id": "policy-candlelit-es",
        "language": "es",
        "title": "Reembolsos <candlelit>",
    }
    # Sin ACL explícita, la del dominio (handbook → internal), como en RAG Engine.
    assert struct_data(doc("handbook-a", acl=None, doc_type=DocumentType.HANDBOOK))["acl"] == (
        "internal"
    )


def test_object_names_carry_the_acl_and_reject_invalid_ids() -> None:
    assert object_name(doc(), "agent-search") == (
        "agent-search/public/policy/policy-candlelit-es.html"
    )
    with pytest.raises(ValueError, match="Id no admitido"):
        object_name(doc("policy::0000"), "agent-search")


def test_manifest_is_sorted_and_points_to_the_html() -> None:
    lines = manifest([doc("b-doc"), doc("a-doc", acl="internal")], BUCKET, "agent-search")
    first, second = (json.loads(line) for line in lines.splitlines())
    assert [first["id"], second["id"]] == ["a-doc", "b-doc"]
    assert first["content"] == {
        "mimeType": "text/html",
        "uri": f"gs://{BUCKET}/agent-search/internal/policy/a-doc.html",
    }
    assert first["structData"]["acl"] == "internal"


@dataclass
class FakeBlob:
    name: str
    bucket: FakeBucket
    md5_hash: str | None = None

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.bucket.uploads.append((self.name, content_type))
        self.md5_hash = base64.b64encode(hashlib.md5(data).digest()).decode()
        self.bucket.blobs[self.name] = self
        self.bucket.contents[self.name] = data

    def delete(self) -> None:
        del self.bucket.blobs[self.name]
        self.bucket.deleted.append(self.name)


@dataclass
class FakeBucket:
    name: str = BUCKET
    blobs: dict[str, FakeBlob] = field(default_factory=dict)
    contents: dict[str, bytes] = field(default_factory=dict)
    uploads: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def get_blob(self, blob_name: str) -> FakeBlob | None:
        return self.blobs.get(blob_name)

    def blob(self, blob_name: str) -> FakeBlob:
        return FakeBlob(blob_name, self)

    def list_blobs(self, *, prefix: str) -> list[FakeBlob]:
        return [b for name, b in self.blobs.items() if name.startswith(prefix)]


class FakeOperation:
    def __init__(self, errors: list[str]) -> None:
        self.metadata = ImportDocumentsMetadata(success_count=2, failure_count=len(errors))
        self._errors = errors

    async def result(self) -> ImportDocumentsResponse:
        return ImportDocumentsResponse(
            error_samples=[{"code": 3, "message": m} for m in self._errors]
        )


@dataclass
class FakeDocuments:
    errors: list[str] = field(default_factory=list)
    requests: list[ImportDocumentsRequest] = field(default_factory=list)

    async def import_documents(self, *, request: ImportDocumentsRequest) -> FakeOperation:
        self.requests.append(request)
        return FakeOperation(self.errors)


def ingestion(documents: FakeDocuments, bucket: FakeBucket) -> AgentSearchIngestion:
    return AgentSearchIngestion(documents=documents, bucket=bucket, branch=BRANCH)


async def test_sync_uploads_changed_html_imports_full_and_prunes_orphans() -> None:
    documents, bucket = FakeDocuments(errors=["fichero vacio"]), FakeBucket()
    orphan = FakeBlob("agent-search/public/policy/old-doc.html", bucket)
    bucket.blobs[orphan.name] = orphan

    result = await ingestion(documents, bucket).sync([doc("a-doc"), doc("b-doc")])

    request = documents.requests[0]
    assert request.parent == BRANCH
    assert list(request.gcs_source.input_uris) == [f"gs://{BUCKET}/agent-search/metadata.jsonl"]
    assert request.gcs_source.data_schema == "document"
    assert request.reconciliation_mode == ImportDocumentsRequest.ReconciliationMode.FULL
    assert ("agent-search/public/policy/a-doc.html", "text/html; charset=utf-8") in bucket.uploads
    assert bucket.contents["agent-search/metadata.jsonl"].decode().count("\n") == 2
    assert bucket.deleted == ["agent-search/public/policy/old-doc.html"]
    assert result.uploaded == 2 and result.succeeded == 2 and result.pruned_objects == 1
    assert result.errors == ("fichero vacio",)


async def test_republishing_unchanged_documents_uploads_no_html() -> None:
    documents, bucket = FakeDocuments(), FakeBucket()
    sync = ingestion(documents, bucket)
    await sync.sync([doc("a-doc")])
    bucket.uploads.clear()

    result = await sync.sync([doc("a-doc")])

    assert result.uploaded == 0 and bucket.uploads == [
        ("agent-search/metadata.jsonl", "application/x-ndjson")
    ]
    assert bucket.blobs["agent-search/public/policy/a-doc.html"].md5_hash == html_md5(doc("a-doc"))


async def test_acl_change_moves_the_html_and_the_old_one_is_pruned_after_import() -> None:
    documents, bucket = FakeDocuments(), FakeBucket()
    sync = ingestion(documents, bucket)
    await sync.sync([doc("a-doc")])

    await sync.sync([doc("a-doc", acl="internal")])

    assert "agent-search/internal/policy/a-doc.html" in bucket.blobs
    assert bucket.deleted == ["agent-search/public/policy/a-doc.html"]
    assert len(documents.requests) == 2


async def test_duplicated_ids_are_rejected_before_touching_the_cloud() -> None:
    documents, bucket = FakeDocuments(), FakeBucket()
    with pytest.raises(ValueError, match="repetidos"):
        await ingestion(documents, bucket).sync([doc("a-doc"), doc("a-doc")])
    assert not bucket.uploads and not documents.requests


def test_regional_endpoint_outside_global() -> None:
    options = discovery_engine_options("eu")
    assert options is not None and options.api_endpoint == "eu-discoveryengine.googleapis.com"
    assert discovery_engine_options("global") is None


# ---------- Filtro del servidor ----------


def test_filter_expression_has_acl_and_rejects_injection() -> None:
    assert search_filter({}, INTERNAL) == 'acl: ANY("public", "internal")'
    assert search_filter({"language": "es", "doc_type": "policy"}, AccessScope()) == (
        'acl: ANY("public") AND doc_type: ANY("policy") AND language: ANY("es")'
    )
    with pytest.raises(ValueError, match="ACL pertenece al servidor"):
        search_filter({"acl": "internal"}, AccessScope())
    for value in ('es") OR acl: ANY("internal', "es\\"):
        with pytest.raises(ValueError, match="no admitido"):
            search_filter({"language": value}, AccessScope())


# ---------- search (retrieve) ----------


def chunk(
    document_id: str = "policy-candlelit-es",
    acl: str = "public",
    *,
    text: str = "Reembolsable hasta 48 horas antes.",
    data: dict[str, Any] | None = None,
    uri: str | None = None,
) -> Chunk:
    metadata = Chunk.DocumentMetadata(
        uri=uri or f"gs://{BUCKET}/agent-search/{acl}/policy/{document_id}.html",
        title=document_id,
    )
    if data is not None:  # El proto no admite `struct_data=None`.
        metadata.struct_data = data
    return Chunk(content=text, document_metadata=metadata)


def full_data(document_id: str = "policy-candlelit-es", acl: str = "public") -> dict[str, Any]:
    return {
        "acl": acl,
        "document_id": document_id,
        "language": "es",
        "doc_type": "policy",
        "title": "Reembolsos",
    }


class SearchDouble:
    def __init__(self, *chunks: Chunk) -> None:
        self.chunks = chunks
        self.requests: list[SearchRequest] = []

    async def search(self, *, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(results=[SearchResponse.SearchResult(chunk=c) for c in self.chunks])


class AnswerDouble:
    def __init__(self, answer: Answer | None = None) -> None:
        self.answer = answer or Answer()
        self.requests: list[AnswerQueryRequest] = []

    async def answer_query(self, *, request: AnswerQueryRequest) -> AnswerQueryResponse:
        self.requests.append(request)
        return AnswerQueryResponse(answer=self.answer)


def knowledge(
    search: SearchDouble | None = None, answers: AnswerDouble | None = None
) -> AgentSearchKnowledge:
    return AgentSearchKnowledge(
        search=search or SearchDouble(),
        answers=answers or AnswerDouble(),
        serving_config=SERVING,
        bucket=BUCKET,
    )


async def test_retrieve_asks_for_chunks_with_the_server_filter() -> None:
    search = SearchDouble(chunk(data=full_data()))

    [reference] = await knowledge(search).retrieve(
        "reembolso", top_k=3, filters={"language": "es"}, scope=AccessScope()
    )

    request = search.requests[0]
    assert request.serving_config == SERVING and request.page_size == 3
    assert request.filter == 'acl: ANY("public") AND language: ANY("es")'
    mode = request.content_search_spec.search_result_mode
    assert mode == SearchRequest.ContentSearchSpec.SearchResultMode.CHUNKS
    assert reference.document_id == "policy-candlelit-es" and reference.title == "Reembolsos"
    assert reference.text.startswith("Reembolsable") and len(reference.source_id) == 64


async def test_retrieve_without_struct_data_checks_the_path_but_cannot_check_filters() -> None:
    plain = SearchDouble(chunk())
    references = await knowledge(plain).retrieve(
        "reembolso", top_k=3, filters={}, scope=AccessScope()
    )
    assert references[0].title == "policy-candlelit-es"
    with pytest.raises(InvalidCitationError, match="filtros sin comprobar"):
        await knowledge(SearchDouble(chunk())).retrieve(
            "reembolso", top_k=3, filters={"language": "es"}, scope=AccessScope()
        )


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        (chunk("handbook-a", "internal"), "fuera del alcance"),
        (chunk(uri="gs://otro/agent-search/public/policy/x.html"), "prefijo"),
        (chunk(uri=f"gs://{BUCKET}/agent-search/public/x.html"), "ruta"),
        (chunk(uri=f"gs://{BUCKET}/agent-search/partners/policy/x.html"), "fuera del alcance"),
        (chunk(data=full_data(acl="internal")), "no coincide"),
        (chunk(data=full_data("otro-doc")), "no coincide"),
        (chunk(text=""), "sin texto"),
    ],
)
async def test_fragments_that_cannot_be_verified_fail_closed(bad: Chunk, error: str) -> None:
    with pytest.raises(InvalidCitationError, match=error):
        await knowledge(SearchDouble(bad)).retrieve(
            "reembolso", top_k=3, filters={}, scope=AccessScope()
        )


async def test_filter_mismatch_in_struct_data_is_an_error() -> None:
    data = full_data() | {"language": "en"}
    with pytest.raises(InvalidCitationError, match="no cumple los filtros"):
        await knowledge(SearchDouble(chunk(data=data))).retrieve(
            "reembolso", top_k=3, filters={"language": "es"}, scope=AccessScope()
        )


async def test_empty_scope_and_invalid_input_make_no_calls() -> None:
    search, answers = SearchDouble(chunk()), AnswerDouble()
    lane = knowledge(search, answers)
    assert await lane.retrieve("x", top_k=3, filters={}, scope=AccessScope(())) == []
    empty = await lane.answer("x", top_k=3, filters={}, scope=AccessScope(()))
    assert empty.abstained
    with pytest.raises(ValueError, match="top_k"):
        await lane.retrieve("x", top_k=0, filters={}, scope=AccessScope())
    assert not search.requests and not answers.requests


# ---------- answer_query ----------

TEXT = "Sí, hasta 48 horas antes. La gestión no se devuelve."
FIRST, SECOND = "Sí, hasta 48 horas antes.", "La gestión no se devuelve."


def citation(fragment: str, *ids: str) -> Answer.Citation:
    """Citas en **bytes UTF-8**, como las documenta `Answer.Citation`."""
    start = TEXT.encode().index(fragment.encode())
    return Answer.Citation(
        start_index=start,
        end_index=start + len(fragment.encode()),
        sources=[Answer.CitationSource(reference_id=i) for i in ids],
    )


def reference(data: dict[str, Any] | None = None, acl: str = "public") -> Answer.Reference:
    return Answer.Reference(
        chunk_info=Answer.Reference.ChunkInfo(
            content="Reembolsable hasta 48 horas.",
            document_metadata=Answer.Reference.ChunkInfo.DocumentMetadata(
                uri=f"gs://{BUCKET}/agent-search/{acl}/policy/policy-candlelit-es.html",
                title="Reembolsos",
                struct_data=data if data is not None else full_data(acl=acl),
            ),
        )
    )


def generated(
    citations: list[Answer.Citation] | None = None,
    references: list[Answer.Reference] | None = None,
    **kwargs: Any,
) -> Answer:
    values: dict[str, Any] = {
        "state": Answer.State.SUCCEEDED,
        "answer_text": TEXT,
        "citations": citations
        if citations is not None
        else [citation(FIRST, "0"), citation(SECOND, "0")],
        "references": references if references is not None else [reference()],
    }
    return Answer(**(values | kwargs))


async def test_answer_request_asks_for_citations_and_service_abstentions() -> None:
    answers = AnswerDouble(generated())

    response = await knowledge(answers=answers).answer(
        "¿Me devuelven el dinero?", top_k=4, filters={}, scope=AccessScope()
    )

    request = answers.requests[0]
    assert request.serving_config == SERVING and request.query.text == "¿Me devuelven el dinero?"
    params = request.search_spec.search_params
    assert params.max_return_results == 4 and params.filter == 'acl: ANY("public")'
    spec = request.answer_generation_spec
    assert spec.include_citations and spec.model_spec.model_version == "stable"
    assert spec.ignore_adversarial_query and spec.ignore_jail_breaking_query
    assert spec.ignore_low_relevant_content and spec.ignore_non_answer_seeking_query
    assert not response.abstained and response.answer == TEXT


async def test_byte_citations_become_character_spans() -> None:
    response = await knowledge(answers=AnswerDouble(generated())).answer(
        "x", top_k=4, filters={}, scope=AccessScope()
    )
    first, second = response.citations
    assert response.answer[first.start : first.end] == FIRST == first.text
    assert response.answer[second.start : second.end] == SECOND == second.text
    assert first.references[0].document_id == "policy-candlelit-es"


@pytest.mark.parametrize(
    "answer",
    [
        generated(answer_skipped_reasons=[Answer.AnswerSkippedReason.NO_RELEVANT_CONTENT]),
        generated(answer_skipped_reasons=[Answer.AnswerSkippedReason.ADVERSARIAL_QUERY_IGNORED]),
        generated(citations=[]),
        generated(answer_text="  "),
    ],
)
async def test_abstains_when_the_service_skips_or_cites_nothing(answer: Answer) -> None:
    response = await knowledge(answers=AnswerDouble(answer)).answer(
        "x", top_k=4, filters={}, scope=AccessScope()
    )
    assert response.abstained and response.answer == "" and response.citations == ()


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (generated(citations=[citation(FIRST, "0")]), "sin cita"),
        (generated(citations=[citation(FIRST, "7"), citation(SECOND, "0")]), "inexistente"),
        (generated(citations=[citation(FIRST), citation(SECOND, "0")]), "sin referencias"),
        (generated(references=[reference(acl="internal")]), "fuera del alcance"),
        (generated(references=[Answer.Reference()]), "no admitido"),
        (
            generated(citations=[Answer.Citation(start_index=0, end_index=2, sources=[])]),
            "corta un caracter",
        ),
    ],
)
async def test_answer_citations_fail_closed(answer: Answer, error: str) -> None:
    with pytest.raises(InvalidCitationError, match=error):
        await knowledge(answers=AnswerDouble(answer)).answer(
            "x", top_k=4, filters={}, scope=AccessScope()
        )


async def test_failed_answer_is_unavailable_not_an_abstention() -> None:
    with pytest.raises(RetrievalUnavailableError, match="FAILED"):
        await knowledge(answers=AnswerDouble(generated(state=Answer.State.FAILED))).answer(
            "x", top_k=4, filters={}, scope=AccessScope()
        )


async def test_unstructured_references_are_verified_too() -> None:
    unstructured = Answer.Reference(
        unstructured_document_info=Answer.Reference.UnstructuredDocumentInfo(
            uri=f"gs://{BUCKET}/agent-search/public/policy/policy-candlelit-es.html",
            title="Reembolsos",
            chunk_contents=[
                Answer.Reference.UnstructuredDocumentInfo.ChunkContent(content="Parte uno.")
            ],
            struct_data=full_data(),
        )
    )
    response = await knowledge(answers=AnswerDouble(generated(references=[unstructured]))).answer(
        "x", top_k=4, filters={}, scope=AccessScope()
    )
    assert response.citations[0].references[0].text == "Parte uno."
