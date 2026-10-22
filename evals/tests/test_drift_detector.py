"""Fase 12.8: el detector de drift frente a escenarios sintéticos conocidos (coste 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from headache_core.domain.drift import DriftLevel
from rag_evals.llmops.drift import SCENARIOS, Scenario, main, render, run_scenario


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_detector_classifies_every_scenario(scenario: Scenario) -> None:
    result = run_scenario(scenario)
    assert result.failures == ()


def test_scenarios_are_deterministic() -> None:
    first = run_scenario(SCENARIOS[1])
    second = run_scenario(SCENARIOS[1])
    assert first.findings == second.findings


def test_a_wrong_expectation_fails_the_gate() -> None:
    lying = Scenario(
        "semana-normal",
        "Esperar drift donde no lo hay.",
        expected_levels={"case-intents": DriftLevel.SIGNIFICANT},
        expected_alerts=frozenset({"case-intents"}),
    )
    failures = run_scenario(lying).failures
    assert any("case-intents: nivel" in f for f in failures)
    assert any("alertas" in f for f in failures)


def test_check_mode_passes_and_report_is_written(tmp_path: Path) -> None:
    output = tmp_path / "drift.md"
    assert main(["--check", "--output", str(output)]) == 0
    text = output.read_text(encoding="utf-8")
    assert "ola-de-cancelaciones" in text
    assert "**Diagnóstico**: interno" in text


def test_report_marks_failures() -> None:
    lying = Scenario("x", "y", expected_alerts=frozenset({"agent-tools"}))
    assert "❌" in render([run_scenario(lying)])
