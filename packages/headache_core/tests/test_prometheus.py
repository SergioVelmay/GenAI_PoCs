"""Fase 11.5: nombres de Prometheus y dashboard generados desde el catálogo de métricas."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from headache_core.adapters.prometheus import (
    build_dashboard,
    build_rules,
    label,
    metric,
    quantile,
    ratio,
)
from headache_core.application.telemetry import metrics
from headache_core.domain.slo import BURN_RATE_ALERTS, SLOS

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("name", "suffix", "expected"),
    [
        # Nombres observados en el Prometheus local (collector 0.163, misma traducción).
        (metrics.LLM_COST, "", "headache_llm_cost_USD_total"),
        (metrics.LLM_INPUT_TOKENS, "", "gen_ai_client_inference_usage_input_tokens_total"),
        (metrics.LLM_DURATION, "bucket", "gen_ai_client_operation_duration_seconds_bucket"),
        (metrics.ANSWERS, "", "headache_answers_total"),
        (metrics.OUTBOX_BACKLOG, "", "headache_outbox_backlog"),
        # Unidad "1" en un histograma: sin sufijo (el collector solo pone `_ratio` a gauges).
        (metrics.EVAL_FAITHFULNESS, "bucket", "headache_eval_faithfulness_bucket"),
    ],
)
def test_names_match_the_collector_translation(name: str, suffix: str, expected: str) -> None:
    assert metric(name, suffix) == expected


def test_histograms_need_a_suffix_and_unknown_metrics_fail() -> None:
    with pytest.raises(ValueError, match="histograma"):
        metric(metrics.LLM_DURATION)
    with pytest.raises(KeyError):
        metric("headache.no.existe")


def test_queries() -> None:
    assert label("gen_ai.agent.name") == "gen_ai_agent_name"
    assert quantile(0.95, metrics.ANSWER_TTFT, by=("headache.cache.outcome",)) == (
        "histogram_quantile(0.95, sum by (le, headache_cache_outcome)"
        "(rate(headache_answer_time_to_first_chunk_seconds_bucket[$__rate_interval])))"
    )
    assert ratio("a", "b") == "(a or vector(0)) / clamp_min(b, 1e-9)"


def test_every_metric_in_the_dashboard_exists_in_the_catalog() -> None:
    known = {
        metric(name, suffix)
        for name, spec in metrics.CATALOG.items()
        for suffix in (("bucket", "count", "sum") if spec.kind == "histogram" else ("",))
    }
    exprs = [t["expr"] for p in build_dashboard()["panels"] for t in p.get("targets", [])]
    used = {
        m
        for expr in exprs
        for m in re.findall(r"[a-z_]+_(?:total|bucket|count|sum|backlog)\b", expr)
    }

    assert used
    assert used <= known


def test_every_slo_gets_its_sli_records_and_burn_rate_alerts() -> None:
    groups = {g["name"]: g["rules"] for g in build_rules()["groups"]}

    recorded = {(r["record"], r["labels"]["slo"]) for r in groups["slo-sli"]}
    assert len(recorded) == len(SLOS) * len(
        {w for a in BURN_RATE_ALERTS for w in (a.long_window, a.short_window)}
    )
    burns = groups["slo-burn-rate"]
    assert len(burns) == len(SLOS) * len(BURN_RATE_ALERTS)
    page = next(
        a
        for a in burns
        if a["labels"]
        == {
            "severity": "page",
            "slo": "assistant-availability",
            "owner": "plataforma-ia",
            "window": "1h",
        }
    )
    # 14,4 x (1 - 0,995): la tasa de error que gasta el 2 % del presupuesto en una hora.
    assert "> 0.072" in page["expr"]


def test_alerts_only_use_catalog_metrics_and_point_to_the_runbook() -> None:
    known = {
        metric(name, suffix)
        for name, spec in metrics.CATALOG.items()
        for suffix in (("bucket", "count", "sum") if spec.kind == "histogram" else ("",))
    }
    rules = [r for g in build_rules()["groups"] for r in g["rules"]]
    used = {
        m
        for r in rules
        for m in re.findall(r"\b[a-z_]+_(?:total|bucket|count|sum|backlog)\b", r["expr"])
    }
    assert used <= known
    runbook = (ROOT / "docs/runbook-incidentes.md").read_text(encoding="utf-8")
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', runbook))
    for alert in (r for r in rules if "alert" in r):
        assert alert["annotations"]["runbook_url"].split("#")[1] in anchors, alert["alert"]


def test_committed_dashboard_and_rules_are_up_to_date() -> None:
    committed = ROOT / "infra/local/telemetry/grafana/dashboards/headache-genai.json"
    assert json.loads(committed.read_text(encoding="utf-8")) == build_dashboard()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_observability.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
