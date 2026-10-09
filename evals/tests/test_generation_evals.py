from decimal import Decimal

import pytest

from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import FakeLLM, HashingEmbedding, RecordingTracer
from headache_core.application.llm.gateway import ObservedLLM
from headache_core.application.llm.pricing import PriceTable
from headache_core.application.retrieval import RetrievalService
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.llm import ChatMessage, Role
from headache_core.ports.vector_store import VectorRecord
from rag_evals.generation_eval import (
    GenerationOutcome,
    evaluate_generation,
    render_markdown,
    run_generation_eval,
    stratified_sample,
    summarize_generation,
)
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, GoldenItem, load_golden
from rag_evals.judges import JudgeScores
from rag_evals.metering import BudgetExceededError, CostMeter

POLICY = "Refundable: yes, up to 48 hours before the session."
SECRET = "Internal: gift cards are valid for twelve months."


def _item(item_id: str = "q", **extra: object) -> GoldenItem:
    data: dict[str, object] = {
        "id": item_id,
        "question": "refund hours before the session",
        "language": "en",
        "question_type": "factual",
        "topic": "t",
        "doc_type": "policy",
        "relevance": {"policy": 2},
        "expected_answer": "Up to 48 hours before.",
        "evidence": ["48 hours"],
    }
    return GoldenItem.model_validate({**data, **extra})


async def _service(llm: FakeLLM | None = None) -> RetrievalService:
    embeddings = HashingEmbedding(32)
    records = [
        VectorRecord(
            "policy::0000",
            POLICY,
            await embeddings.embed_query(POLICY),
            {"document_id": "policy", "acl": "public", "title": "Policy"},
        ),
        VectorRecord(
            "handbook::0000",
            SECRET,
            await embeddings.embed_query(SECRET),
            {"document_id": "handbook", "acl": "internal", "title": "Handbook"},
        ),
    ]
    return RetrievalService(LocalRetrieval(records, embeddings=embeddings), llm=llm)


async def test_extractive_answer_is_scored_and_cited_from_the_source() -> None:
    service = await _service()
    [outcome] = await evaluate_generation(
        service, [_item()], strategy="bm25", top_k=3, candidate_k=10
    )
    assert not outcome.abstained and not outcome.rejected
    assert outcome.has_evidence
    assert (outcome.citations, outcome.valid_citations, outcome.relevant_citations) == (1, 1, 1)
    assert outcome.leaks == ()


async def test_audit_flags_internal_citations_even_with_a_leaky_scope() -> None:
    service = await _service()
    item = GoldenItem(
        id="acl",
        question="gift cards twelve months",
        language="en",
        question_type="acl_adversarial",
        topic="t",
        forbidden_document_ids=["handbook"],
    )
    leaky = AccessScope(allowed_acls=("public", "internal"))
    [outcome] = await evaluate_generation(
        service, [item], strategy="bm25", top_k=3, candidate_k=10, scope=leaky
    )
    assert outcome.leaks == ("handbook",)


async def test_llm_mode_counts_invented_quotes_as_rejected() -> None:
    valid = '{"evidence": [{"source_id": "policy::0000", "quote": "up to 48 hours"}]}'
    invented = '{"evidence": [{"source_id": "policy::0000", "quote": "up to 72 hours"}]}'
    service = await _service(FakeLLM([valid, invented]))
    good, bad = await evaluate_generation(
        service, [_item("a"), _item("b")], strategy="bm25", top_k=3, candidate_k=10, mode="llm"
    )
    assert good.has_evidence and good.valid_citations == 1
    assert bad.rejected and not bad.has_evidence


def test_summary_separates_abstention_types_and_judge_means() -> None:
    answered = _item("ok")
    abstained = _item("abst")
    unanswerable = GoldenItem(
        id="none", question="q", language="en", question_type="unanswerable", topic="t"
    )
    outcomes = [
        GenerationOutcome(
            answered, False, False, 2, 2, 1, True, (), 10.0, JudgeScores(1.0, 0.5, None, None)
        ),
        GenerationOutcome(abstained, True, False, 0, 0, 0, False, (), 20.0),
        GenerationOutcome(unanswerable, False, False, 1, 1, 0, False, ("handbook",), 30.0),
    ]
    summary = summarize_generation("native", "bm25", "extractive", "native", outcomes)
    assert summary.answer_evidence == 0.5
    assert summary.false_abstention == 0.5
    assert summary.correct_abstention == 0.0
    assert summary.citation_validity == 1.0
    assert summary.citation_precision == 0.5
    assert summary.citations_per_answer == 2.0
    assert summary.acl_leaks == 1
    assert summary.judged == 1
    assert summary.judge_scores["faithfulness"] == 1.0
    assert summary.judge_scores["context_recall"] is None
    assert summary.missed == ["abst"]


def test_stratified_sample_alternates_question_types() -> None:
    items = [
        _item("f1"),
        _item("f2"),
        _item("f3"),
        GoldenItem(id="u1", question="q", language="en", question_type="unanswerable", topic="t"),
    ]
    assert [item.id for item in stratified_sample(items, 3)] == ["f1", "u1", "f2"]
    with pytest.raises(ValueError):
        stratified_sample(items, 0)


async def test_local_generation_eval_on_golden_subset_has_no_leaks() -> None:
    meta, items = load_golden(DATASET_PATH)
    sample = stratified_sample(items, 15)
    report = await run_generation_eval(sample, meta, corpus=CORPUS_PATH, engines=("native",))
    [run] = report.runs
    assert run.acl_leaks == 0
    assert run.citation_validity == 1.0
    assert run.rejected == 0
    assert "Evals de generacion" in render_markdown(report)
    with pytest.raises(ValueError, match="LLM"):
        await run_generation_eval(sample, meta, corpus=CORPUS_PATH, mode="llm")


async def test_cost_meter_accumulates_and_stops_at_budget() -> None:
    forwarded = RecordingTracer()
    meter = CostMeter(budget_usd=Decimal("0.0000001"), forward_to=forwarded)
    llm = ObservedLLM(
        FakeLLM(["uno dos tres"], model="gpt-4.1-nano"),
        tracer=meter,
        prices=PriceTable(),
        name="eval",
    )
    await llm.complete([ChatMessage(Role.USER, "hola " * 50)])
    snapshot = meter.snapshot()
    assert snapshot.calls == 1 and snapshot.unpriced_calls == 0
    assert Decimal(snapshot.cost_usd) > 0
    assert forwarded.records[0].cost_usd == Decimal(snapshot.cost_usd)
    with pytest.raises(BudgetExceededError):
        await llm.complete([ChatMessage(Role.USER, "otra")])


async def test_cost_meter_refuses_to_continue_with_unpriced_models() -> None:
    meter = CostMeter(budget_usd=Decimal(1))
    llm = ObservedLLM(FakeLLM(["x"]), tracer=meter, prices=PriceTable(), name="eval")
    await llm.complete([ChatMessage(Role.USER, "hola")])
    assert meter.snapshot().unpriced_calls == 1
    with pytest.raises(BudgetExceededError, match="sin precio"):
        await llm.complete([ChatMessage(Role.USER, "hola")])
    with pytest.raises(ValueError):
        CostMeter(budget_usd=Decimal(0))
