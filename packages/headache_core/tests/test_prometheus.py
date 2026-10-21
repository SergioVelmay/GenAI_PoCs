"""Fase 11.5: nombres de Prometheus y dashboard generados desde el catálogo de métricas."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from headache_core.adapters.prometheus import build_dashboard, label, metric, quantile, ratio
from headache_core.application.telemetry import metrics

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


def test_committed_dashboard_is_up_to_date() -> None:
    committed = ROOT / "infra/local/telemetry/grafana/dashboards/headache-genai.json"
    assert json.loads(committed.read_text(encoding="utf-8")) == build_dashboard()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_observability.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
