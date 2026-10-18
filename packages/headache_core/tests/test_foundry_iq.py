"""Carril A Azure (Foundry IQ + Agent Service) con dobles locales: sin red ni coste."""

import json
import re
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from azure.search.documents.knowledgebases.models import (
    KnowledgeBaseRetrievalRequest,
    KnowledgeBaseRetrievalResponse,
)
from openai.types.responses import Response, ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_output_item import McpCall
from openai.types.responses.response_output_text import AnnotationURLCitation

from headache_core.adapters.azure_search import document_key
from headache_core.adapters.foundry_iq import (
    ABSTENTION,
    KB_TOOL,
    FoundryIQKnowledge,
    agent_definition,
    knowledge_base_definition,
    knowledge_source_definition,
    mcp_connection_definition,
    mcp_endpoint,
    publish_agent,
)
from headache_core.application.retrieval import InvalidCitationError, RetrievalUnavailableError
from headache_core.domain.retrieval import AccessScope

SOURCE = "knowledge-public-ks"


def search_reference(chunk_id: str = "policy::0000", acl: str = "public") -> dict[str, Any]:
    metadata = {"acl": acl, "language": "en", "document_id": "policy", "title": "Policy"}
    return {
        "type": "searchIndex",
        "id": "0",
        "activitySource": 1,
        "docKey": document_key(chunk_id),
        "rerankerScore": 3.1,
        "sourceData": {
            "chunk_id": chunk_id,
            "text": "Refund within 48 hours.",
            "metadata_json": json.dumps(metadata),
        },
    }


class RetrievalDouble:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.requests: list[KnowledgeBaseRetrievalRequest] = []

    async def retrieve(self, request: KnowledgeBaseRetrievalRequest) -> Any:
        self.requests.append(request)
        return KnowledgeBaseRetrievalResponse(self.body)


def retrieval(*references: dict[str, Any], activity: list[Any] | None = None) -> RetrievalDouble:
    return RetrievalDouble({"activity": activity or [], "references": list(references)})


async def test_retrieve_sends_acl_filter_and_maps_canonical_chunks() -> None:
    client = retrieval(search_reference(), search_reference())
    knowledge = FoundryIQKnowledge(client, knowledge_source=SOURCE)  # type: ignore[arg-type]
    result = await knowledge.retrieve(
        "refund", top_k=5, filters={"language": "en"}, scope=AccessScope()
    )
    body = client.requests[0].as_dict()
    params = body["knowledgeSourceParams"][0]
    assert body["intents"] == [{"search": "refund", "type": "semantic"}]
    assert params["filterAddOn"] == "(acl eq 'public') and (language eq 'en')"
    assert params["includeReferenceSourceData"] is True
    assert [(r.source_id, r.document_id) for r in result] == [("policy::0000", "policy")]
    assert result[0].uri == f"foundry-iq://{SOURCE}/policy::0000"


@pytest.mark.parametrize("defect", ["private", "missing-acl", "doc-key", "no-source-data", "blob"])
async def test_retrieve_fails_closed_on_untrusted_references(defect: str) -> None:
    reference = search_reference()
    if defect == "private":
        reference = search_reference(acl="internal")
    elif defect == "missing-acl":
        reference["sourceData"]["metadata_json"] = json.dumps({"document_id": "policy"})
    elif defect == "doc-key":
        reference["docKey"] = document_key("other::0000")
    elif defect == "no-source-data":
        reference["sourceData"] = None
    else:
        reference = {"type": "azureBlob", "id": "0", "activitySource": 1, "blobUrl": "u"}
    knowledge = FoundryIQKnowledge(retrieval(reference), knowledge_source=SOURCE)  # type: ignore[arg-type]
    with pytest.raises(InvalidCitationError):
        await knowledge.retrieve("refund", top_k=5, filters={}, scope=AccessScope())


async def test_retrieve_rejects_partial_results_and_acl_injection() -> None:
    activity = [{"type": "searchIndex", "id": 1, "error": {"code": "x", "message": "boom"}}]
    client = retrieval(search_reference(), activity=activity)
    knowledge = FoundryIQKnowledge(client, knowledge_source=SOURCE)  # type: ignore[arg-type]
    with pytest.raises(RetrievalUnavailableError, match="parcial"):
        await knowledge.retrieve("refund", top_k=5, filters={}, scope=AccessScope())
    with pytest.raises(ValueError, match="ACL"):
        await knowledge.retrieve(
            "refund", top_k=5, filters={"acl": "internal"}, scope=AccessScope()
        )
    assert await knowledge.retrieve("refund", top_k=5, filters={}, scope=AccessScope(())) == []
    with pytest.raises(RetrievalUnavailableError, match="HYBRID"):
        await knowledge.retrieve("refund", top_k=5, filters={}, scope=AccessScope(), mode="HYBRID")
    assert len(client.requests) == 1


def mcp_call(results: list[dict[str, Any]], **overrides: Any) -> McpCall:
    values: dict[str, Any] = {
        "id": "call-1",
        "arguments": "{}",
        "name": KB_TOOL,
        "server_label": "knowledge-base",
        "type": "mcp_call",
        "output": json.dumps(results),
        "status": "completed",
    }
    return McpCall(**(values | overrides))


def message(text: str) -> ResponseOutputMessage:
    content = [ResponseOutputText(text=text, annotations=[], type="output_text")]
    return ResponseOutputMessage(
        id="msg-1", content=content, role="assistant", status="completed", type="message"
    )


RESULTS = [
    {"ref_id": 0, "title": "Policy", "text": "Refund within 48 hours."},
    {"ref_id": 1, "title": "Fees", "text": "Fees are not refundable."},
]


class ResponsesDouble:
    def __init__(self, *output: Any) -> None:
        self.output = list(output)
        self.calls: list[dict[str, Any]] = []
        self.responses = self

    async def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        return Response(
            id="resp-1",
            created_at=0,
            model="gpt-4.1-mini",
            object="response",
            output=self.output,
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
        )


def agent(responses: ResponsesDouble, audience: AccessScope | None = None) -> FoundryIQKnowledge:
    return FoundryIQKnowledge(
        retrieval(),  # type: ignore[arg-type]
        knowledge_source=SOURCE,
        responses=responses,  # type: ignore[arg-type]
        agent_name="headache-public-agent",
        agent_audience=audience or AccessScope(),
    )


async def test_agent_answer_maps_markers_to_mcp_results() -> None:
    text = (
        "You can get a refund within 48 hours.【0:0†Policy】【0:0†Policy】"
        " Fees are kept.【0:1†Fees】"
    )
    responses = ResponsesDouble(mcp_call(RESULTS), message(text))
    result = await agent(responses).answer("refund", top_k=5, filters={}, scope=AccessScope())
    assert responses.calls[0]["extra_body"]["agent_reference"]["name"] == "headache-public-agent"
    assert not result.abstained and "【" not in result.answer
    first, second = result.citations
    assert result.answer[first.start : first.end] == first.text
    assert result.answer[second.start : second.end] == "Fees are kept."
    assert len(first.references) == 1 and first.references[0].text == RESULTS[0]["text"]
    assert first.text != first.references[0].text


@pytest.mark.parametrize(
    "text",
    [
        "Refund within 48 hours.【0:7†Policy】",
        "Refund within 48 hours.【3:0†Policy】",
        "Refund within 48 hours.【0:0†Policy】 Also free tickets.",
        "【0:0†Policy】",
    ],
)
async def test_agent_citations_fail_closed(text: str) -> None:
    responses = ResponsesDouble(mcp_call(RESULTS), message(text))
    with pytest.raises(InvalidCitationError):
        await agent(responses).answer("refund", top_k=5, filters={}, scope=AccessScope())


@pytest.mark.parametrize(
    "call",
    [
        mcp_call(RESULTS, name="delete_index"),
        mcp_call(RESULTS, server_label="other"),
        mcp_call(RESULTS, output="not json"),
    ],
)
async def test_agent_rejects_untrusted_tool_calls(call: McpCall) -> None:
    responses = ResponsesDouble(call, message("Refund.【0:0†Policy】"))
    with pytest.raises(InvalidCitationError):
        await agent(responses).answer("refund", top_k=5, filters={}, scope=AccessScope())


async def test_agent_failed_tool_call_and_unsupported_requests() -> None:
    failed = ResponsesDouble(mcp_call(RESULTS, error="boom", status="failed"), message("x"))
    with pytest.raises(RetrievalUnavailableError, match="fallo"):
        await agent(failed).answer("refund", top_k=5, filters={}, scope=AccessScope())
    unused = ResponsesDouble()
    knowledge = agent(unused)
    with pytest.raises(RetrievalUnavailableError, match="audiencia"):
        await knowledge.answer(
            "refund", top_k=5, filters={}, scope=AccessScope(("public", "internal"))
        )
    with pytest.raises(RetrievalUnavailableError, match="filtros"):
        await knowledge.answer("refund", top_k=5, filters={"language": "en"}, scope=AccessScope())
    unconfigured = FoundryIQKnowledge(retrieval(), knowledge_source=SOURCE)  # type: ignore[arg-type]
    with pytest.raises(RetrievalUnavailableError, match="configurado"):
        await unconfigured.answer("refund", top_k=5, filters={}, scope=AccessScope())
    assert (await knowledge.answer("refund", top_k=5, filters={}, scope=AccessScope(()))).abstained
    assert not unused.calls


@pytest.mark.parametrize("output", [[message(ABSTENTION)], [mcp_call([]), message(ABSTENTION)]])
async def test_agent_abstention(output: list[Any]) -> None:
    result = await agent(ResponsesDouble(*output)).answer(
        "refund", top_k=5, filters={}, scope=AccessScope()
    )
    assert result.abstained and result.answer == "" and not result.citations


async def test_agent_answer_without_tool_call_is_rejected() -> None:
    responses = ResponsesDouble(message("Refunds are always free."))
    with pytest.raises(InvalidCitationError, match="sin consultar"):
        await agent(responses).answer("refund", top_k=5, filters={}, scope=AccessScope())


def test_definitions_bind_audience_and_disable_kb_models() -> None:
    source = knowledge_source_definition(SOURCE, index_name="knowledge", audience=AccessScope())
    parameters = source["searchIndexParameters"]
    assert parameters["baseFilter"] == "(acl eq 'public')"
    assert parameters["semanticConfigurationName"] == "knowledge-semantic"
    # Sin modelo: el esfuerzo minimal y la salida extractiva deben ir explicitos (MCP preview).
    assert knowledge_base_definition("kb", knowledge_source=SOURCE) == {
        "name": "kb",
        "knowledgeSources": [{"name": SOURCE}],
        "retrievalReasoningEffort": {"kind": "minimal"},
        "outputMode": "extractiveData",
        "retrieveDefaults": {"maxOutputDocuments": 5},
    }
    url = mcp_endpoint("https://svc.search.windows.net/", "kb")
    # Preview: el endpoint MCP no existe en la versión GA (regresión validada en Azure).
    assert url == (
        "https://svc.search.windows.net/knowledgebases/kb/mcp?api-version=2026-08-01-preview"
    )
    tool = agent_definition("a", model="gpt-4.1-mini", mcp_url=url, connection="c")["definition"][
        "tools"
    ][0]
    assert tool["allowed_tools"] == [KB_TOOL] and tool["require_approval"] == "never"
    connection = mcp_connection_definition("c", mcp_url=url)["properties"]
    assert connection["authType"] == "ProjectManagedIdentity" and connection["target"] == url
    with pytest.raises(ValueError):
        knowledge_source_definition(SOURCE, index_name="knowledge", audience=AccessScope(()))


AGENT = {"name": "agent", "definition": {"kind": "prompt", "model": "m"}}


async def test_publish_agent_creates_then_versions_on_conflict() -> None:
    # POST /agents no es idempotente: si el agente existe (409), se publica una versi?n nueva.
    calls: list[tuple[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/agents"):
            return httpx.Response(409, json={"error": "exists"})
        return httpx.Response(200, json={"version": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        version = await publish_agent(http, "https://p.ai/api/projects/x/", "t", AGENT)

    assert version == "2"
    assert calls == [
        ("/api/projects/x/agents", AGENT),
        ("/api/projects/x/agents/agent/versions", {"definition": AGENT["definition"]}),
    ]


async def test_publish_agent_creates_new_agent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json={"versions": {"latest": {"version": "1"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await publish_agent(http, "https://p.ai/api/projects/x", "t", AGENT) == "1"


# --- Formato real de Foundry Agent Service (capturado en Azure, checkpoint 8->9) ---


def agent_document(chunk_id: str, text: str, acl: str | None = "public") -> dict[str, str]:
    metadata = {"document_id": chunk_id.split("::")[0]} | ({"acl": acl} if acl else {})
    fields = {
        "chunk_id": chunk_id,
        "text": text,
        "title": "T",
        "metadata_json": json.dumps(metadata),
    }
    return {"id": "uuid", "content": json.dumps(fields)}


def annotated_message(text: str, cited: list[str]) -> ResponseOutputMessage:
    """Una anotacion url_citation por marca, con la clave del chunk en el indice."""
    starts = [match.start() for match in re.finditer("\u3010", text)]
    annotations = [
        AnnotationURLCitation(
            type="url_citation",
            start_index=start,
            end_index=text.index("\u3011", start) + 1,
            title="t",
            url=f"https://svc.search.windows.net/indexes/knowledge/docs/"
            f"{quote(document_key(chunk_id))}?$select=text",
        )
        for start, chunk_id in zip(starts, cited, strict=True)
    ]
    content = [ResponseOutputText(text=text, annotations=annotations, type="output_text")]
    return ResponseOutputMessage(
        id="msg-1", content=content, role="assistant", status="completed", type="message"
    )


AGENT_OUTPUT = {
    "documents": [
        agent_document("policy-nightlife-en::0000", "Nightlife tickets are non-refundable."),
        agent_document("event-evt_000024::0000", "Concert on Friday."),
    ]
}


async def test_agent_real_format_maps_annotations_to_chunks() -> None:
    # message_idx (4) no corresponde a nada observable: manda la anotacion.
    marks = "\u30104:0\u2020source\u3011\u30104:1\u2020source\u3011"
    text = f"Nightlife tickets are non-refundable.{marks}"
    responses = ResponsesDouble(
        mcp_call([], output=json.dumps(AGENT_OUTPUT)),
        annotated_message(text, ["policy-nightlife-en::0000", "event-evt_000024::0000"]),
    )
    result = await agent(responses).answer("refund", top_k=5, filters={}, scope=AccessScope())
    (citation,) = result.citations
    assert result.answer == "Nightlife tickets are non-refundable."
    assert [r.source_id for r in citation.references] == [
        "policy-nightlife-en::0000",
        "event-evt_000024::0000",
    ]
    assert citation.references[0].document_id == "policy-nightlife-en"


@pytest.mark.parametrize(
    ("documents", "cited"),
    [
        # Cita a un chunk que la knowledge base no devolvio en esta llamada.
        ([agent_document("policy-nightlife-en::0000", "x")], "handbook-help-center-en::0001"),
        # Documento fuera de la audiencia del agente.
        (
            [agent_document("handbook-help-center-en::0001", "x", acl="internal")],
            "handbook-help-center-en::0001",
        ),
        # Sin ACL se deniega.
        ([agent_document("policy-nightlife-en::0000", "x", acl=None)], "policy-nightlife-en::0000"),
    ],
)
async def test_agent_real_format_fails_closed(documents: list[dict[str, str]], cited: str) -> None:
    text = "Answer.\u30104:0\u2020source\u3011"
    responses = ResponsesDouble(
        mcp_call([], output=json.dumps({"documents": documents})),
        annotated_message(text, [cited]),
    )
    with pytest.raises(InvalidCitationError):
        await agent(responses).answer("refund", top_k=5, filters={}, scope=AccessScope())


@pytest.mark.parametrize(
    "text",
    [
        ABSTENTION,
        "I don't know.",
        "No dispongo de información sobre mascotas. ¿Puedes concretar?",
    ],
)
async def test_agent_real_format_without_results_abstains(text: str) -> None:
    # Sin resultados, el MCP devuelve "\n": nada fundamentado, se descarta el texto del modelo.
    responses = ResponsesDouble(mcp_call([], output="\n"), message(text))
    result = await agent(responses).answer("pets", top_k=5, filters={}, scope=AccessScope())
    assert result.abstained and result.answer == "" and not result.citations


async def test_agent_without_results_cannot_cite() -> None:
    text = "Pets are welcome.\u30104:0\u2020source\u3011"
    responses = ResponsesDouble(mcp_call([], output="\n"), message(text))
    with pytest.raises(InvalidCitationError):
        await agent(responses).answer("pets", top_k=5, filters={}, scope=AccessScope())
