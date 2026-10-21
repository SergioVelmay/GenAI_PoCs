"""Calibración de la caché semántica (fase 10.3): el arnés y sus invariantes."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from headache_core.adapters.memory import HashingEmbedding
from rag_evals.semantic_cache import (
    DATASET_PATH,
    SweepPoint,
    check,
    estimated_cost,
    load_cases,
    lowest_safe,
    recommend,
    run_eval,
    write_report,
)


def point(threshold: float, hits: int, false_hits: int) -> SweepPoint:
    return SweepPoint(threshold, True, hits, 10, false_hits, 0, 0, ())


def test_dataset_covers_every_kind_of_risk() -> None:
    cases = load_cases(DATASET_PATH)
    kinds = {case.kind for case in cases}
    ids = {case.id for case in cases}

    assert kinds == {"paraphrase", "trap", "personal"}
    assert len(ids) == len(cases)
    for risk in ("city", "category", "operation", "number", "language", "acl", "event-id"):
        assert any(risk in case_id for case_id in ids), risk


def test_recommend_maximises_hits_without_false_hits() -> None:
    points = [point(0.5, 9, 2), point(0.6, 7, 0), point(0.7, 7, 0), point(0.8, 3, 0)]

    recommended = recommend(points)

    assert recommended is not None
    assert (recommended.threshold, recommended.paraphrase_hits) == (0.7, 7)  # Empate: el alto.


def test_lowest_safe_is_where_false_hits_stop() -> None:
    points = [point(0.5, 9, 2), point(0.6, 8, 1), point(0.7, 5, 0), point(0.8, 3, 0)]

    safe = lowest_safe(points)

    assert safe is not None and safe.threshold == 0.7


async def test_hashing_run_passes_the_gate_and_shows_why_the_guard_exists(
    tmp_path: Path,
) -> None:
    cases = load_cases(DATASET_PATH)

    report = await run_eval(cases, HashingEmbedding(1024), backend="hashing", dataset="v1")
    md = write_report(report, tmp_path)

    assert check(report) == []
    assert report.recommended is not None and report.recommended.false_hits == 0
    without_guard = [p for p in report.sweep if not p.guard]
    assert any(p.false_hits > 0 for p in without_guard)
    assert all(p.personal_stored == 0 for p in report.sweep)
    assert "Umbral recomendado" in md.read_text(encoding="utf-8")


async def test_check_fails_on_a_false_hit(tmp_path: Path) -> None:
    report = await run_eval(
        load_cases(DATASET_PATH), HashingEmbedding(1024), backend="hashing", dataset="v1"
    )
    report.sweep[0] = SweepPoint(0.5, True, 12, 17, 1, 0, 0, ("trap-city-es",))

    assert any("trap-city-es" in problem for problem in check(report))


def test_real_embeddings_cost_is_a_fraction_of_a_cent() -> None:
    assert estimated_cost(load_cases(DATASET_PATH)) < Decimal("0.0001")


@pytest.mark.parametrize("backend", ["hashing", "azure"])
def test_every_backend_has_a_floor(backend: str) -> None:
    from rag_evals.semantic_cache import MIN_HIT_RATE

    assert 0 < MIN_HIT_RATE[backend] <= 1
