"""Fase 11.G2: alertas de Cloud Monitoring desde las mismas reglas que Prometheus."""

from __future__ import annotations

import re

import pytest

from genaipocs_core.adapters.cloud_monitoring import (
    PROJECT,
    RUNBOOK_BASE,
    build_alert_policies,
    build_gcp_rules,
    inline,
    inline_tests,
    lookback_seconds,
    recording_rules,
)
from genaipocs_core.adapters.prometheus import build_rules
from genaipocs_core.adapters.queue_metrics import pubsub_backlog_promql
from genaipocs_core.domain.slo import BURN_RATE_ALERTS, SLOS

RULES = build_rules()
POLICIES = {policy.key: policy for policy, _ in build_alert_policies(RULES)}


def test_every_prometheus_alert_becomes_a_policy() -> None:
    alerts = [r for g in RULES["groups"] for r in g["rules"] if "alert" in r]

    assert len(alerts) == len(POLICIES) - 3  # Más backlog, plazos de ack e ingesta de logs.
    assert sum(p.alert_rule == "ErrorBudgetBurn" for p in POLICIES.values()) == len(SLOS) * len(
        BURN_RATE_ALERTS
    )


def test_no_policy_references_a_recording_rule() -> None:
    records = {name for name, _, _ in recording_rules(RULES)}

    for policy in POLICIES.values():
        assert not any(name in policy.query for name in records), policy.key


def test_inline_replaces_the_reference_with_its_expression() -> None:
    records = {("slo:error_ratio:rate1h", "slo", "x"): "a / b"}

    assert inline('slo:error_ratio:rate1h{slo="x"} > 0.1', records) == "(a / b) > 0.1"
    # Una métrica de Google Cloud también lleva `:`: no es una regla y se queda como está.
    gcp = 'pubsub_googleapis_com:subscription_num_unacked_messages_by_region{project_id="p"}'
    assert inline(gcp, records) == gcp
    with pytest.raises(ValueError, match="inexistente"):
        inline('slo:error_ratio:rate1h{slo="y"}', records)


def test_lookback_adds_the_offset() -> None:
    assert lookback_seconds("rate(x[5m])") == 300
    assert lookback_seconds("increase(x[7d] offset 1d) / increase(x[1d])") == 8 * 86400
    assert lookback_seconds("sum(x)") == 0


def test_long_queries_lose_the_retest_window_and_evaluate_every_five_minutes() -> None:
    """Cloud Monitoring desactiva la ventana de reintento para consultas de más de 25 h."""
    page = POLICIES["ErrorBudgetBurn-assistant-availability-1h"]
    long = POLICIES["ErrorBudgetBurn-assistant-availability-3d"]
    drift = POLICIES["DistributionDrift-case-intents"]

    assert (page.severity, page.duration_s, page.evaluation_interval_s) == ("CRITICAL", 120, 60)
    assert (long.severity, long.duration_s, long.evaluation_interval_s) == ("WARNING", 0, 300)
    assert (drift.duration_s, drift.evaluation_interval_s) == (0, 300)
    assert "sin ventana de reintento" in long.documentation


def test_policies_respect_cloud_monitoring_limits() -> None:
    for policy in POLICIES.values():
        assert policy.evaluation_interval_s % 30 == 0
        assert len(policy.documentation) <= 8192
        assert len(policy.subject.encode()) <= 255
        assert "{{" not in policy.documentation  # Plantillas de Prometheus: no las entiende.
        assert f"{RUNBOOK_BASE}/docs/runbook-incidentes.md#" in policy.documentation
        assert all(re.fullmatch(r"[a-z_][a-z0-9_]*", key) for key in policy.labels)


def test_pubsub_backlog_alert_shares_the_autoscaler_query() -> None:
    """Heredado de la 10.G: misma métrica y mismo selector que el autoescalador y CREMA."""
    query = POLICIES["PubSubBacklogGrowing"].query
    single = pubsub_backlog_promql(PROJECT, "support.emails.worker")
    metric, selector = re.fullmatch(r"sum\((\S+?)\{(.+)\}\)", single).groups()  # type: ignore[union-attr]
    shared = selector.replace('subscription_id="support.emails.worker"', "")

    assert query.startswith(f"sum by (subscription_id)({metric}{{{shared}")
    assert 'subscription_id=~".+\\\\.worker"' in query


def test_gcp_rules_keep_the_groups_and_drop_the_recording_rules() -> None:
    groups = {g["name"]: g["rules"] for g in build_gcp_rules(RULES)["groups"]}

    assert "slo-sli" not in groups
    assert {"slo-burn-rate", "operacion", "drift", "gcp-plataforma"} <= groups.keys()
    assert all("alert" in rule for rules in groups.values() for rule in rules)


def test_promtool_tests_are_redirected_and_inlined() -> None:
    tests = {
        "rule_files": ["../genaipocs.rules.yml"],
        "tests": [
            {"promql_expr_test": [{"expr": 'count(drift:psi{signal="case-intents"} > 0.25)'}]}
        ],
    }

    copied = inline_tests(tests, RULES, "../genaipocs.alerts.yml")

    assert copied["rule_files"] == ["../genaipocs.alerts.yml"]
    assert "drift:psi" not in copied["tests"][0]["promql_expr_test"][0]["expr"]
    assert tests["rule_files"] == ["../genaipocs.rules.yml"]  # El original no se toca.
