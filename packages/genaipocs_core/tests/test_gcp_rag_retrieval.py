"""Carril A de GCP con RAG Engine (4.G2): `retrieve_contexts` y Gemini con *grounding*.

Sin red, con los tipos reales de `agentplatform` y `google-genai`. Los dobles salen de la
documentación ("Retrieval and generation output of RAG Engine"); se sustituyen por respuestas
capturadas en la primera validación en la nube (pendientes-cloud.md, fase 4 · GCP).
"""

from __future__ import annotations

from typing import Any

import pytest
from agentplatform import types as rag_types
from google.genai import errors, types

from genaipocs_core.adapters.gcp_rag_retrieval import ANSWER_INSTRUCTIONS, RagEngineKnowledge
from genaipocs_core.application.retrieval import InvalidCitationError, RetrievalUnavailableError
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.llm import LLMRateLimitError

BUCKET = "genaipocs-dev-rag-docs"
CORPORA = {
    "public": "projects/p/locations/europe-west4/ragCorpora/1",
    "internal": "projects/p/locations/europe-west4/ragCorpora/2",
}
INTERNAL = AccessScope(("public", "internal"))
MODEL = "gemini-3.5-flash-lite"


def uri(document_id: str, acl: str = "public", doc_type: str = "policy") -> str:
    return f"gs://{BUCKET}/{acl}/{doc_type}/{document_id}.md"


def context(
    document_id: str, acl: str = "public", text: str = "Refundable up to 48 hours."
) -> rag_types.RagContextsContext:
    return rag_types.RagContextsContext(source_uri=uri(document_id, acl), text=text, score=0.2)


class RagDouble:
    """Responde por corpus: lo que el test prepara para cada ACL."""

    def __init__(self, **by_acl: list[Any]) -> None:
        self.by_corpus = {CORPORA[acl]: contexts for acl, contexts in by_acl.items()}
        self.calls: list[tuple[types.VertexRagStore, Any]] = []

    async def retrieve_contexts(
        self, *, vertex_rag_store: types.VertexRagStore, query: Any, config: Any = None
    ) -> rag_types.RetrieveContextsResponse:
        self.calls.append((vertex_rag_store, query))
        corpus = str((vertex_rag_store.rag_resources or [])[0].rag_corpus)
        contexts = self.by_corpus.get(corpus, [])
        return rag_types.RetrieveContextsResponse(contexts=rag_types.RagContexts(contexts=contexts))


class ModelsDouble:
    def __init__(self, response: types.GenerateContentResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def generate_content_stream(self, **_: Any) -> Any:
        raise NotImplementedError


def knowledge(
    rag: RagDouble | None = None,
    models: ModelsDouble | None = None,
    *,
    rerank_model: str = "",
) -> RagEngineKnowledge:
    return RagEngineKnowledge(
        rag or RagDouble(),
        bucket=BUCKET,
        corpora=CORPORA,
        models=models,
        generation_model=MODEL if models else "",
        rerank_model=rerank_model,
    )


# ---------- retrieve ----------


async def test_public_scope_only_asks_the_public_corpus() -> None:
    rag = RagDouble(public=[context("policy-candlelit-en")], internal=[context("x", "internal")])

    references = await knowledge(rag).retrieve("refund", top_k=3, filters={}, scope=AccessScope())

    assert len(rag.calls) == 1
    store, query = rag.calls[0]
    assert [r.rag_corpus for r in store.rag_resources or []] == [CORPORA["public"]]
    assert query.text == "refund" and query.rag_retrieval_config.top_k == 3
    assert query.rag_retrieval_config.ranking is None
    [reference] = references
    assert reference.document_id == "policy-candlelit-en" == reference.title
    assert reference.uri == uri("policy-candlelit-en") and reference.text.startswith("Refundable")
    assert len(reference.source_id) == 64


async def test_internal_scope_asks_one_corpus_per_request_and_fuses_by_position() -> None:
    """Una petición admite un solo corpus: se pregunta a cada uno y se fusiona por RRF, porque
    la escala del score depende del almacén."""
    rag = RagDouble(
        public=[context("public-a"), context("public-b")],
        internal=[context("handbook-a", "internal"), context("handbook-b", "internal")],
    )

    references = await knowledge(rag).retrieve("refund", top_k=3, filters={}, scope=INTERNAL)

    assert [str((s.rag_resources or [])[0].rag_corpus) for s, _ in rag.calls] == [
        CORPORA["public"],
        CORPORA["internal"],
    ]
    # Primero los primeros de cada corpus (empate deshecho por id estable), después los segundos.
    assert {r.document_id for r in references[:2]} == {"public-a", "handbook-a"}
    assert len(references) == 3


async def test_rerank_model_turns_on_the_ranking_api_inside_rag_engine() -> None:
    rag = RagDouble(public=[context("policy")])
    await knowledge(rag, rerank_model="semantic-ranker-default-004").retrieve(
        "refund", top_k=5, filters={}, scope=AccessScope()
    )
    ranking = rag.calls[0][1].rag_retrieval_config.ranking
    assert ranking.rank_service.model_name == "semantic-ranker-default-004"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"filters": {"language": "en"}}, "filtros"),
        ({"mode": "HYBRID"}, "SEMANTIC"),
    ],
)
async def test_what_cannot_be_guaranteed_is_rejected_before_calling(
    kwargs: dict[str, Any], error: str
) -> None:
    rag = RagDouble(public=[context("policy")])
    request: dict[str, Any] = {"top_k": 5, "filters": {}, "scope": AccessScope()} | kwargs
    with pytest.raises(RetrievalUnavailableError, match=error):
        await knowledge(rag).retrieve("refund", **request)
    for query, top_k in (("  ", 5), ("refund", 0), ("refund", 101)):
        with pytest.raises(ValueError, match="top_k"):
            await knowledge(rag).retrieve(query, top_k=top_k, filters={}, scope=AccessScope())
    assert not rag.calls


async def test_empty_or_unknown_scope_asks_nothing() -> None:
    rag = RagDouble(public=[context("policy")])
    for scope in (AccessScope(()), AccessScope(("partners",))):
        assert await knowledge(rag).retrieve("refund", top_k=5, filters={}, scope=scope) == []
    assert not rag.calls


@pytest.mark.parametrize(
    "bad",
    [
        rag_types.RagContextsContext(source_uri=uri("handbook", "internal"), text="Interno."),
        rag_types.RagContextsContext(source_uri="gs://otro-bucket/public/policy/x.md", text="X."),
        rag_types.RagContextsContext(source_uri=f"gs://{BUCKET}/public/x.md", text="X."),
        rag_types.RagContextsContext(source_uri=f"gs://{BUCKET}/public/policy/x.pdf", text="X."),
        rag_types.RagContextsContext(source_uri=None, text="X."),
        rag_types.RagContextsContext(source_uri=uri("policy"), text=""),
    ],
)
async def test_fragment_outside_its_corpus_or_unverifiable_fails_closed(bad: Any) -> None:
    """Un fragmento interno en el corpus público es un fallo de la partición, no un resultado."""
    with pytest.raises(InvalidCitationError):
        await knowledge(RagDouble(public=[bad])).retrieve(
            "refund", top_k=5, filters={}, scope=AccessScope()
        )


def test_constructor_requires_bucket_and_every_corpus() -> None:
    with pytest.raises(ValueError, match="corpus"):
        RagEngineKnowledge(RagDouble(), bucket=BUCKET, corpora={"public": "x", "internal": ""})
    with pytest.raises(ValueError, match="bucket"):
        RagEngineKnowledge(RagDouble(), bucket="", corpora=CORPORA)


# ---------- answer ----------

ANSWER = "Sí, hasta 48 horas antes. La gestión no se devuelve."
FIRST = "Sí, hasta 48 horas antes."
SECOND = "La gestión no se devuelve."


def chunk(document_id: str, acl: str = "public") -> types.GroundingChunk:
    return types.GroundingChunk(
        retrieved_context=types.GroundingChunkRetrievedContext(
            uri=uri(document_id, acl), title=f"{document_id}.md", text="Reembolsable hasta 48 h."
        )
    )


def support(text: str, indices: list[int], *, in_text: str = ANSWER) -> types.GroundingSupport:
    """Segmento con índices en **bytes UTF-8**; `start_index` 0 se omite, como en la API."""
    raw = in_text.encode()
    start = raw.index(text.encode())
    return types.GroundingSupport(
        segment=types.Segment(
            start_index=start or None, end_index=start + len(text.encode()), text=text
        ),
        grounding_chunk_indices=indices,
    )


def generated(
    text: str = ANSWER,
    supports: list[types.GroundingSupport] | None = None,
    chunks: list[types.GroundingChunk] | None = None,
    *,
    finish: types.FinishReason = types.FinishReason.STOP,
    thought: bool = False,
) -> types.GenerateContentResponse:
    parts = [types.Part(text="Pienso...", thought=True)] if thought else []
    parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=finish,
                grounding_metadata=types.GroundingMetadata(
                    grounding_chunks=chunks if chunks is not None else [chunk("policy-a")],
                    grounding_supports=(
                        supports
                        if supports is not None
                        else [support(FIRST, [0]), support(SECOND, [0])]
                    ),
                ),
            )
        ]
    )


async def test_answer_sends_one_corpus_and_the_versioned_instructions() -> None:
    models = ModelsDouble(generated())

    response = await knowledge(models=models).answer(
        "¿Me devuelven el dinero?", top_k=4, filters={}, scope=AccessScope()
    )

    call = models.calls[0]
    assert call["model"] == MODEL
    config: types.GenerateContentConfig = call["config"]
    assert config.system_instruction == ANSWER_INSTRUCTIONS
    tool = (config.tools or [])[0]
    assert isinstance(tool, types.Tool)
    retrieval = tool.retrieval
    store = retrieval.vertex_rag_store if retrieval else None
    assert store is not None and store.rag_retrieval_config is not None
    assert [r.rag_corpus for r in store.rag_resources or []] == [CORPORA["public"]]
    assert store.rag_retrieval_config.top_k == 4
    assert not response.abstained and response.answer == ANSWER


@pytest.mark.parametrize("thought", [False, True])
async def test_utf8_byte_offsets_become_character_spans(thought: bool) -> None:
    """Con tildes, los bytes y los caracteres no coinciden: la cita debe caer en su frase."""
    response = await knowledge(models=ModelsDouble(generated(thought=thought))).answer(
        "refund", top_k=4, filters={}, scope=AccessScope()
    )

    first, second = response.citations
    assert (first.text, response.answer[first.start : first.end]) == (FIRST, FIRST)
    assert (second.text, response.answer[second.start : second.end]) == (SECOND, SECOND)
    assert second.start == len(FIRST) + 1  # en bytes sería uno más: la "í" ocupa dos
    assert first.references[0].document_id == "policy-a"


@pytest.mark.parametrize(
    "response",
    [
        generated(supports=[]),
        generated(text="I don't know", supports=[]),
        generated(finish=types.FinishReason.SAFETY),
        types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(
                block_reason=types.BlockedReason.SAFETY
            )
        ),
        types.GenerateContentResponse(candidates=[]),
    ],
)
async def test_abstains_without_grounding_or_when_blocked(
    response: types.GenerateContentResponse,
) -> None:
    result = await knowledge(models=ModelsDouble(response)).answer(
        "refund", top_k=4, filters={}, scope=AccessScope()
    )
    assert result.abstained and result.answer == "" and result.citations == ()


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (generated(supports=[support(FIRST, [0])]), "sin cita"),
        (generated(supports=[support(FIRST, [3]), support(SECOND, [0])]), "inexistente"),
        (generated(supports=[support(FIRST, []), support(SECOND, [0])]), "sin fragmentos"),
        (generated(chunks=[chunk("handbook", "internal")]), "ruta"),
        (
            generated(
                supports=[
                    types.GroundingSupport(
                        # Corta la "í" de "Sí" por la mitad (2 bytes).
                        segment=types.Segment(end_index=2),
                        grounding_chunk_indices=[0],
                    )
                ]
            ),
            "corta un caracter",
        ),
        (
            generated(
                supports=[
                    types.GroundingSupport(
                        segment=types.Segment(end_index=len(FIRST.encode()), text="Otro texto"),
                        grounding_chunk_indices=[0],
                    ),
                    support(SECOND, [0]),
                ]
            ),
            "no corresponde",
        ),
        (
            generated(
                supports=[
                    types.GroundingSupport(
                        segment=types.Segment(end_index=10_000), grounding_chunk_indices=[0]
                    )
                ]
            ),
            "fuera del texto",
        ),
    ],
)
async def test_citations_fail_closed(response: types.GenerateContentResponse, error: str) -> None:
    with pytest.raises(InvalidCitationError, match=error):
        await knowledge(models=ModelsDouble(response)).answer(
            "refund", top_k=4, filters={}, scope=AccessScope()
        )


async def test_answer_needs_a_model_and_a_single_corpus() -> None:
    with pytest.raises(RetrievalUnavailableError, match="GENERATION_MODEL"):
        await knowledge().answer("refund", top_k=4, filters={}, scope=AccessScope())
    models = ModelsDouble(generated())
    with pytest.raises(RetrievalUnavailableError, match="un solo corpus"):
        await knowledge(models=models).answer("refund", top_k=4, filters={}, scope=INTERNAL)
    empty = await knowledge(models=models).answer(
        "refund", top_k=4, filters={}, scope=AccessScope(())
    )
    assert empty.abstained and not models.calls


async def test_sdk_errors_are_translated_like_the_gateway() -> None:
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "cuota"}})
    with pytest.raises(LLMRateLimitError):
        await knowledge(models=ModelsDouble(quota)).answer(
            "refund", top_k=4, filters={}, scope=AccessScope()
        )
