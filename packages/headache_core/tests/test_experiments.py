"""Fase 12.7: asignación A/B, estadística pareada y regla de decisión (dominio puro)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from headache_core.adapters.experiment_trackers import JsonlExperimentTracker
from headache_core.domain.experiments import (
    Assignment,
    Decision,
    McNemar,
    decide,
    discordant_needed,
    mcnemar_exact,
    paired_bootstrap,
)
from headache_core.ports.experiments import ExperimentRun

SPLIT = Assignment("prompt-v2", "sal-1", {"control": 90, "tratamiento": 10})


def test_assignment_is_sticky_and_follows_the_weights() -> None:
    units = [f"cus_{i:07d}" for i in range(20_000)]
    first = [SPLIT.variant(u) for u in units]

    assert first == [SPLIT.variant(u) for u in units]  # Sin estado: siempre la misma.
    share = Counter(first)["tratamiento"] / len(units)
    assert 0.09 < share < 0.11


def test_each_experiment_shuffles_independently() -> None:
    other = Assignment("otro-experimento", "sal-1", {"control": 50, "tratamiento": 50})
    units = [f"cus_{i:07d}" for i in range(4_000)]
    in_first = {u for u in units if SPLIT.variant(u) == "tratamiento"}
    overlap = sum(other.variant(u) == "tratamiento" for u in in_first) / len(in_first)
    # Estar en el tratamiento de uno no predice el del otro (~50 %).
    assert 0.4 < overlap < 0.6


@pytest.mark.parametrize("weights", [{}, {"a": -1, "b": 2}, {"a": 0, "b": 0}])
def test_invalid_weights_are_rejected(weights: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Assignment("x", "y", weights)


def test_mcnemar_only_counts_discordant_pairs() -> None:
    control = [True] * 50 + [False] * 7
    treatment = [True] * 50 + [True] * 7
    result = mcnemar_exact(control, treatment)
    assert (result.only_control, result.only_treatment) == (0, 7)
    assert result.p_value == pytest.approx(2 / 2**7)
    assert mcnemar_exact([True, False], [True, False]).p_value == 1.0
    assert mcnemar_exact([True, False], [False, True]).p_value == 1.0
    with pytest.raises(ValueError):
        mcnemar_exact([True], [True, False])


def test_paired_bootstrap_is_reproducible_and_brackets_the_difference() -> None:
    control = [0.0] * 30 + [1.0] * 70
    treatment = [1.0] * 30 + [1.0] * 70
    interval = paired_bootstrap(control, treatment)
    assert interval == paired_bootstrap(control, treatment)
    assert interval.estimate == pytest.approx(0.3)
    assert interval.low < 0.3 < interval.high and interval.excludes_zero
    flat = paired_bootstrap([1.0, 0.0], [1.0, 0.0])
    assert (flat.low, flat.high) == (0.0, 0.0) and not flat.excludes_zero


@pytest.mark.parametrize(
    ("test", "invariants", "decision"),
    [
        (McNemar(0, 7, 0.0156), 0, Decision.SHIP),
        (McNemar(7, 0, 0.0156), 0, Decision.REJECT),
        (McNemar(0, 7, 0.0156), 1, Decision.REJECT),  # Seguridad antes que métrica.
        (McNemar(0, 0, 1.0), 0, Decision.KEEP),
        (McNemar(1, 0, 1.0), 0, Decision.INCONCLUSIVE),
        (McNemar(10, 14, 0.54), 0, Decision.INCONCLUSIVE),
    ],
)
def test_the_domain_decides(test: McNemar, invariants: int, decision: Decision) -> None:
    assert decide(test=test, treatment_invariants=invariants).decision is decision


def test_small_samples_say_so_explicitly() -> None:
    verdict = decide(test=McNemar(1, 0, 1.0), treatment_invariants=0)
    assert "falta muestra" in verdict.reason and "1 caso discrepa" in verdict.reason
    assert discordant_needed(1.0) == 6
    assert discordant_needed(0.8) == 12
    with pytest.raises(ValueError):
        discordant_needed(0.5)


def test_jsonl_tracker_appends_one_line_per_run(tmp_path: Path) -> None:
    tracker = JsonlExperimentTracker(tmp_path)
    run = ExperimentRun("exp", "control", {"flavor": "native"}, {"pass_rate": 0.5}, {"t": "1"})

    ids = [tracker.log_run(run), tracker.log_run(run)]

    lines = (tmp_path / "exp.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["run_id"] for r in records] == ids and ids[0] != ids[1]
    assert (
        records[0]["metrics"] == {"pass_rate": 0.5} and records[0]["params"]["flavor"] == "native"
    )
