from pathlib import Path

import pytest

from genaipocs_core.adapters.local_retrieval import LocalRetrieval
from genaipocs_core.adapters.memory import FakeLLM, HashingEmbedding
from genaipocs_core.application.retrieval import RetrievalService
from genaipocs_core.config import LLMProvider
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.llm import LLMContentFilterError, LLMResponse
from genaipocs_core.ports.vector_store import VectorRecord
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, GoldenItem, load_golden
from rag_evals.retrieval_eval import (
    REPORTS_DIR,
    Engine,
    Outcome,
    cloud_reports_dir,
    cloud_settings,
    engines_cloud,
    evaluate_service,
    llm_reports_dir,
    render_markdown,
    run_retrieval_eval,
    summarize,
)


def _item(item_id: str, relevance: dict[str, int], **extra: object) -> GoldenItem:
    data: dict[str, object] = {
        "id": item_id,
        "question": "question",
        "language": "en",
        "question_type": "factual",
        "topic": "t",
        "doc_type": "policy",
        "relevance": relevance,
        "expected_answer": "a",
        "evidence": ["48 hours"],
    }
    return GoldenItem.model_validate({**data, **extra})


def test_summarize_aggregates_answerable_items_and_counts_leaks() -> None:
    hit = _item("hit", {"gold": 2, "translation": 1})
    miss = _item("miss", {"gold": 2}, language="es")
    unanswerable = GoldenItem(
        id="none",
        question="q",
        language="en",
        question_type="unanswerable",
        topic="t",
    )
    outcomes = [
        Outcome(hit, ("gold", "noise"), ("up to 48 hours",), 10.0, ()),
        Outcome(miss, ("noise",), ("nothing",), 30.0, ()),
        Outcome(unanswerable, (), (), 20.0, ("handbook",)),
    ]
    summary = summarize("native", "bm25", outcomes, top_k=5)
    assert summary.overall.questions == 2
    assert summary.overall.hit_at_k == 0.5
    assert summary.overall.recall_at_k == 0.25
    assert summary.overall.evidence_at_k == 0.5
    assert summary.segments["language=es"].hit_at_k == 0.0
    assert summary.acl_leaks == 1
    assert (summary.unanswerable, summary.unanswerable_empty) == (1, 1)
    assert summary.latency_p50_ms == 20.0
    assert summary.missed == ["miss"]


async def test_eval_flags_internal_results_even_if_backend_leaks() -> None:
    embeddings = HashingEmbedding(32)
    secret = VectorRecord(
        "handbook::0000",
        "gift cards are valid for twelve months",
        await embeddings.embed_query("gift cards are valid for twelve months"),
        {"document_id": "handbook", "acl": "internal"},
    )
    leaky = AccessScope(allowed_acls=("public", "internal"))
    service = RetrievalService(LocalRetrieval([secret], embeddings=embeddings))
    item = GoldenItem(
        id="acl",
        question="gift cards",
        language="en",
        question_type="acl_adversarial",
        topic="t",
        forbidden_document_ids=["handbook"],
    )
    outcomes = await evaluate_service(
        service, [item], strategy="bm25", top_k=5, candidate_k=20, scope=leaky
    )
    assert outcomes[0].leaks == ("handbook",)


async def test_failed_llm_plans_are_counted_not_fatal() -> None:
    embeddings = HashingEmbedding(32)
    policy = VectorRecord(
        "policy::0000",
        "Refundable up to 48 hours before.",
        await embeddings.embed_query("Refundable up to 48 hours before."),
        {"document_id": "policy", "acl": "public"},
    )
    good = '{"query": "refund hours", "filters": {}}'
    service = RetrievalService(
        LocalRetrieval([policy], embeddings=embeddings),
        llm=FakeLLM([good, "no es json", "tampoco"]),
    )
    items = [_item("ok", {"policy": 2}), _item("bad", {"policy": 2})]
    ok, bad = await evaluate_service(service, items, strategy="rewrite", top_k=5, candidate_k=20)
    assert ok.error is None and ok.documents == ("policy",)
    assert bad.error is not None and bad.documents == ()
    assert summarize("azure", "rewrite", [ok, bad], top_k=5).plan_errors == 1

    class Shielded:
        async def complete(self, *args: object, **kwargs: object) -> LLMResponse:
            raise LLMContentFilterError("content_filter: jailbreak")

    blocked = RetrievalService(LocalRetrieval([policy], embeddings=embeddings), llm=Shielded())
    [shielded] = await evaluate_service(
        blocked, items[:1], strategy="hyde", top_k=5, candidate_k=20
    )
    assert shielded.documents == () and "jailbreak" in (shielded.error or "")


async def test_llm_strategies_need_a_cloud_engine() -> None:
    meta, items = load_golden(DATASET_PATH)
    with pytest.raises(ValueError, match="motores en la nube"):
        await run_retrieval_eval(items[:3], meta, engines=["native"], llm=FakeLLM([]))


def test_gcp_engines_compose_agent_retrieval_and_ranking() -> None:
    """4.G1: `gcp` evalúa BM25, vector, híbrida y `rerank` con la Ranking API; `gcp-semantic`,
    la híbrida con el VertexRanker integrado. Sin red: solo la configuración."""
    plain = cloud_settings(CORPUS_PATH, "gcp")
    semantic = cloud_settings(CORPUS_PATH, "gcp-semantic")
    assert (plain.backend, plain.embeddings, plain.reranker, plain.semantic_ranker) == (
        "gcp_vector_search",
        "gcp",
        "gcp",
        False,
    )
    assert semantic.semantic_ranker and semantic.reranker == "token-coverage"
    assert cloud_settings(CORPUS_PATH, "azure").backend == "azure_search"


def test_one_run_one_cloud_and_its_own_reports_dir() -> None:
    assert engines_cloud(["native", "llamaindex"]) is None
    assert engines_cloud(["native", "gcp", "gcp-semantic"]) == "gcp"
    with pytest.raises(ValueError, match="una sola nube"):
        engines_cloud(["azure", "gcp"])
    assert cloud_reports_dir("gcp") == REPORTS_DIR / "gcp"


def test_llm_reports_go_to_the_cloud_of_the_provider() -> None:
    """Una calibracion con Gemini no puede pisar la de Azure: mismos nombres, otra carpeta."""
    assert llm_reports_dir(LLMProvider.AZURE) == REPORTS_DIR / "azure"
    assert llm_reports_dir(LLMProvider.AWS) == REPORTS_DIR / "aws"
    assert llm_reports_dir(LLMProvider.GCP) == REPORTS_DIR / "gcp"
    with pytest.raises(ValueError, match="simulado"):
        llm_reports_dir(LLMProvider.SIMULATED)


@pytest.mark.parametrize("engine", ["native", "llamaindex"])
async def test_runner_on_versioned_dataset_has_no_acl_leaks(engine: Engine) -> None:
    meta, items = load_golden(DATASET_PATH)
    subset = [item for item in items if item.question_type != "factual"][:40]
    report = await run_retrieval_eval(
        subset, meta, corpus=CORPUS_PATH, engines=[engine], strategies=["bm25", "hybrid"]
    )
    assert [run.strategy for run in report.runs] == ["bm25", "hybrid"]
    for run in report.runs:
        assert run.acl_leaks == 0
        assert 0.0 <= run.overall.mrr <= run.overall.hit_at_k <= 1.0
    assert "| Motor | Estrategia |" in render_markdown(report)


async def test_runner_refuses_a_different_corpus(tmp_path: Path) -> None:
    meta, items = load_golden(DATASET_PATH)
    other = tmp_path / "knowledge.jsonl"
    other.write_text(CORPUS_PATH.read_text(encoding="utf-8")[:-200], encoding="utf-8")
    with pytest.raises(ValueError, match="no es el corpus"):
        await run_retrieval_eval(items, meta, corpus=other)
