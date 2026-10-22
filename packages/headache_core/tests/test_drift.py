"""Fase 12.8: PSI, niveles, diagnóstico y reglas de Prometheus de drift."""

from __future__ import annotations

import math

import pytest

from headache_core.adapters.prometheus import DRIFT_RECORD, build_rules, drift_psi
from headache_core.application.telemetry import metrics
from headache_core.domain.drift import (
    DRIFT_SIGNALS,
    EPSILON,
    SIGNALS_BY_NAME,
    DriftFinding,
    DriftLevel,
    diagnose,
    evaluate,
    level,
    psi,
)


def test_psi_is_zero_for_the_same_mix_whatever_the_volume() -> None:
    assert psi({"a": 10, "b": 30}, {"a": 100, "b": 300}) == pytest.approx(0.0)


def test_psi_matches_the_hand_calculation() -> None:
    # 50/50 -> 80/20: el mismo caso que el test de promtool (drift-intents-shift).
    expected = 0.3 * math.log(0.8 / 0.5) + (-0.3) * math.log(0.2 / 0.5)
    assert psi({"refund": 50, "info": 50}, {"refund": 80, "info": 20}) == pytest.approx(expected)
    assert expected == pytest.approx(0.416, abs=1e-3)


def test_psi_is_symmetric() -> None:
    a, b = {"x": 70, "y": 30}, {"x": 40, "y": 60}
    assert psi(a, b) == pytest.approx(psi(b, a))


def test_a_new_category_does_not_divide_by_zero() -> None:
    value = psi({"get_order": 100}, {"get_order": 50, "issue_refund": 50})
    assert math.isfinite(value)
    assert value > 1
    # La categoría nueva entra con EPSILON en la referencia.
    assert value == pytest.approx(
        (0.5 - 1) * math.log(0.5 / 1) + (0.5 - EPSILON) * math.log(0.5 / EPSILON)
    )


def test_psi_needs_observations() -> None:
    with pytest.raises(ValueError, match="observaciones"):
        psi({}, {"a": 1})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, DriftLevel.STABLE),
        (0.099, DriftLevel.STABLE),
        (0.1, DriftLevel.MODERATE),
        (0.249, DriftLevel.MODERATE),
        (0.25, DriftLevel.SIGNIFICANT),
        (3.0, DriftLevel.SIGNIFICANT),
    ],
)
def test_levels_follow_the_usual_convention(value: float, expected: DriftLevel) -> None:
    assert level(value) is expected


def test_alert_needs_significant_drift_and_enough_samples() -> None:
    signal = SIGNALS_BY_NAME["case-intents"]
    shifted = evaluate(signal, {"a": 500, "b": 500}, {"a": 400, "b": 100})
    assert shifted.alert
    few = evaluate(signal, {"a": 500, "b": 500}, {"a": 8, "b": 2})
    assert few.level is DriftLevel.SIGNIFICANT
    assert not few.enough
    assert not few.alert


def _finding(name: str, drift: DriftLevel, *, enough: bool = True) -> DriftFinding:
    return DriftFinding(name, 0.0, drift, 1000, enough)


@pytest.mark.parametrize(
    ("levels", "prefix"),
    [
        ({}, "estable"),
        ({"case-intents": DriftLevel.SIGNIFICANT}, "externo:"),
        (
            {"case-intents": DriftLevel.SIGNIFICANT, "agent-resolution": DriftLevel.MODERATE},
            "externo con impacto",
        ),
        ({"agent-resolution": DriftLevel.SIGNIFICANT}, "interno"),
        ({"agent-tools": DriftLevel.MODERATE}, "interno"),
    ],
)
def test_diagnosis_separates_external_from_internal(
    levels: dict[str, DriftLevel], prefix: str
) -> None:
    findings = {
        s.name: _finding(s.name, levels.get(s.name, DriftLevel.STABLE)) for s in DRIFT_SIGNALS
    }
    assert diagnose(findings).startswith(prefix)


def test_diagnosis_ignores_changes_without_enough_samples() -> None:
    findings = {s.name: _finding(s.name, DriftLevel.STABLE) for s in DRIFT_SIGNALS}
    findings["case-intents"] = _finding("case-intents", DriftLevel.SIGNIFICANT, enough=False)
    assert diagnose(findings).startswith("ruido")


def test_every_signal_watches_a_catalogued_metric() -> None:
    for signal in DRIFT_SIGNALS:
        assert signal.metric in metrics.CATALOG


def test_rules_record_psi_and_alert_per_signal() -> None:
    group = next(g for g in build_rules()["groups"] if g["name"] == "drift")
    records = [r for r in group["rules"] if "record" in r]
    alerts = [r for r in group["rules"] if "alert" in r]
    assert {r["labels"]["signal"] for r in records} == set(SIGNALS_BY_NAME)
    assert all(r["record"] == DRIFT_RECORD for r in records)
    assert {a["labels"]["signal"] for a in alerts} == set(SIGNALS_BY_NAME)
    for alert in alerts:
        assert alert["annotations"]["runbook_url"].endswith("#drift")
        assert alert["labels"]["severity"] == "ticket"


def test_psi_expression_compares_today_with_the_previous_week() -> None:
    expr = drift_psi(SIGNALS_BY_NAME["agent-tools"])
    # Histograma: se cuentan ejecuciones con `_count`; la referencia, justo antes del día actual.
    assert "gen_ai_execute_tool_duration_seconds_count[1d]" in expr
    assert "gen_ai_execute_tool_duration_seconds_count[7d] offset 1d" in expr
    assert "by (gen_ai_tool_name)" in expr
    assert "clamp_min" in expr and str(EPSILON) in expr
