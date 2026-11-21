"""Calibración de la caché semántica (fase 10.3): el arnés y sus invariantes."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from genaipocs_core.adapters.memory import HashingEmbedding
from rag_evals.semantic_cache import (
    DATASET_PATH,
    SweepPoint,
    check,
    estimated_cost,
    load_cases,
    lowest_safe,
    recommend,
    report_dir,
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


@pytest.mark.parametrize("backend", ["hashing", "azure", "bedrock", "gcp"])
def test_every_backend_has_a_floor(backend: str) -> None:
    from rag_evals.semantic_cache import MIN_HIT_RATE

    assert 0 < MIN_HIT_RATE[backend] <= 1


class QueryOnlyEmbedding(HashingEmbedding):
    """Como Gemini: documento y consulta dan vectores distintos. La calibración debe usar la
    consulta, que es lo que embebe la caché en ejecución (10.G3)."""

    def __init__(self) -> None:
        super().__init__(1024)
        self.queries = 0

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("la caché no embebe documentos: falsearía el umbral")

    async def embed_query(self, text: str) -> list[float]:
        self.queries += 1
        return await super().embed_query(text)


async def test_calibration_embeds_like_the_cache_at_runtime() -> None:
    cases = load_cases(DATASET_PATH)
    embeddings = QueryOnlyEmbedding()

    report = await run_eval(cases, embeddings, backend="hashing", dataset="v1")

    texts = {text for case in cases for text in (case.seed, case.probe)}
    assert embeddings.queries == len(texts)  # Una vez por texto, aunque se barran 11 umbrales.
    assert report.recommended is not None


@pytest.mark.parametrize(
    ("backend", "folder"),
    [("hashing", ""), ("azure", "azure"), ("bedrock", "aws"), ("gcp", "gcp")],
)
def test_reports_with_cost_go_to_the_folder_of_their_cloud(backend: str, folder: str) -> None:
    """Heredado de la 8.G4: una calibración con Gemini no sobrescribe la de Azure."""
    base = Path("evals/reports")

    assert report_dir(base, backend) == (base / folder if folder else base)  # type: ignore[arg-type]


def test_gemini_embeddings_cost_more_but_still_a_fraction_of_a_cent() -> None:
    cases = load_cases(DATASET_PATH)

    assert estimated_cost(cases, "gcp") > estimated_cost(cases, "azure")
    assert estimated_cost(cases, "gcp") < Decimal("0.001")
