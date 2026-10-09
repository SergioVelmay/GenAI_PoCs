from pathlib import Path

import pytest

from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalService
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.vector_store import VectorRecord
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, GoldenItem, load_golden
from rag_evals.retrieval_eval import (
    Engine,
    Outcome,
    evaluate_service,
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
