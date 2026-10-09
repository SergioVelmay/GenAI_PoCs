from typing import Any

import pytest

from rag_evals.gate import build_baseline, evaluate_gate, render_markdown

SHA = "abc"


def _retrieval(mrr: float = 0.9, leaks: int = 0) -> dict[str, Any]:
    overall = {
        "hit_at_1": 0.8,
        "hit_at_k": 1.0,
        "recall_at_k": 0.7,
        "mrr": mrr,
        "ndcg_at_k": 0.85,
        "evidence_at_k": 1.0,
    }
    return {
        "dataset": {"version": 1, "corpus_sha256": SHA},
        "runs": [{"engine": "native", "strategy": "bm25", "overall": overall, "acl_leaks": leaks}],
    }


def _generation(false_abstention: float = 0.0, **run: Any) -> dict[str, Any]:
    base = {
        "engine": "native",
        "strategy": "hybrid",
        "mode": "extractive",
        "judge": "none",
        "answer_evidence": 1.0,
        "false_abstention": false_abstention,
        "correct_abstention": 0.1,
        "citation_precision": None,
        "citation_validity": 1.0,
        "rejected": 0,
        "acl_leaks": 0,
    }
    return {"dataset": {"version": 1, "corpus_sha256": SHA}, "runs": [{**base, **run}]}


def test_identical_reports_pass_in_enforce_mode() -> None:
    baseline = build_baseline(_retrieval(), _generation(), tolerance=0.01)
    assert "citation_precision" not in baseline["generation"]["native/hybrid/extractive"]
    result = evaluate_gate(baseline, _retrieval(), _generation())
    assert result.passed("enforce")
    assert {f.status for f in result.findings} == {"ok"}


def test_regressions_only_block_in_enforce_mode() -> None:
    baseline = build_baseline(_retrieval(), _generation(), tolerance=0.01)
    result = evaluate_gate(baseline, _retrieval(mrr=0.85), _generation(false_abstention=0.05))
    assert {(f.run, f.metric) for f in result.regressions} == {
        ("native/bm25", "mrr"),
        ("native/hybrid/extractive", "false_abstention"),
    }
    assert result.passed("report") and not result.passed("enforce")
    assert "regression" in render_markdown(result, "report")


def test_small_noise_is_tolerated_and_improvements_are_reported() -> None:
    baseline = build_baseline(_retrieval(), _generation(), tolerance=0.01)
    result = evaluate_gate(baseline, _retrieval(mrr=0.895), _generation())
    assert result.passed("enforce")
    improved = evaluate_gate(baseline, _retrieval(mrr=0.95), _generation())
    assert [f.metric for f in improved.improvements] == ["mrr"]


def test_invariants_block_even_in_report_mode() -> None:
    baseline = build_baseline(_retrieval(), _generation(), tolerance=0.01)
    leaky = evaluate_gate(baseline, _retrieval(leaks=1), _generation(citation_validity=0.9))
    assert len(leaky.violations) == 2
    assert not leaky.passed("report")
    stale = _generation()
    stale["dataset"]["corpus_sha256"] = "other"
    assert not evaluate_gate(baseline, _retrieval(), stale).passed("report")


def test_missing_runs_count_as_regressions_and_new_runs_are_listed() -> None:
    baseline = build_baseline(_retrieval(), _generation(), tolerance=0.01)
    renamed = _generation(strategy="bm25")
    result = evaluate_gate(baseline, _retrieval(), renamed)
    assert [(f.run, f.status) for f in result.findings if f.suite == "generation"] == [
        ("native/hybrid/extractive", "missing"),
        ("native/bm25/extractive", "new"),
    ]
    assert not result.passed("enforce")


def test_baseline_refuses_reports_that_violate_invariants() -> None:
    with pytest.raises(ValueError, match="invariantes"):
        build_baseline(_retrieval(leaks=1), _generation(), tolerance=0.01)
