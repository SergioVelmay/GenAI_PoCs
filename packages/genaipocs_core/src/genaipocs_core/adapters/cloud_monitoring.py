"""De las reglas de Prometheus a políticas de alerta de Cloud Monitoring (fase 11.G2).

Cloud Monitoring evalúa PromQL sobre Managed Prometheus, y el collector de GCP nombra las
métricas igual que el Prometheus local (11.G1). Así que las alertas se portan **todas**, desde la
misma fuente (`build_rules()`, que sale de `domain/slo.py`), con tres diferencias:

1. **Sin recording rules.** Cada referencia a una (`slo:error_ratio:rate1h{slo="..."}`,
   `drift:psi{signal="..."}`) se sustituye por su expresión: la alerta calcula el SLI en cada
   evaluación. Una política lleva una sola condición PromQL.
2. **Intervalo y ventana de reintento según lo que mire atrás la consulta.** Hasta 25 h se
   evalúa cada 60 s; más allá, como mínimo cada 5 min y **sin ventana de reintento** (el `for`
   de Prometheus): Cloud Monitoring la desactiva para consultas de más de 25 h.
3. **Alertas de plataforma que solo existen en GCP** (`platform_alerts()`): backlog y plazos de
   ack vencidos de Pub/Sub (la misma consulta que el autoescalador y CREMA) y el ritmo de
   ingesta de Cloud Logging.

Salen dos ficheros (`scripts/build_observability.py`): las alertas en formato de Prometheus,
para pasarles los mismos tests de `promtool` que a las locales, y el JSON que recorre Terraform
(`infra/terraform/gcp/observability.tf`). `__PROJECT_ID__` y `__RUNBOOK_BASE__` los sustituye
Terraform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from genaipocs_core.adapters.prometheus import RUNBOOK
from genaipocs_core.adapters.queue_metrics import (
    pubsub_backlog_by_subscription_promql,
    pubsub_subscription_selector,
)

PROJECT = "__PROJECT_ID__"
RUNBOOK_BASE = "__RUNBOOK_BASE__"

# Límites de Cloud Monitoring para PromQL (docs: "PromQL-based alerting policies").
RETEST_MAX_LOOKBACK_S = 25 * 3600
FAST_EVALUATION_S = (
    60  # Mínimo 30 s; 60 s reduce a la mitad los puntos devueltos (coste desde 2027).
)
SLOW_EVALUATION_S = 300  # Consultas de 25 h a 8 días.
DOCUMENTATION_MAX_CHARS = 8192

SEVERITY = {"page": "CRITICAL", "ticket": "WARNING"}

# Ritmo que agota en 30 días los 50 GiB/mes de ingesta gratis de Cloud Logging.
LOG_FREE_GIB_PER_DAY = 50 / 30
PUBSUB_EXPIRED_ACK_METRIC = "pubsub_googleapis_com:subscription_expired_ack_deadlines_count"
LOG_BYTES_METRIC = "logging_googleapis_com:billing_bytes_ingested"
WORKER_SUBSCRIPTIONS = r".+\\.worker"  # Cadena PromQL: el `\\.` llega a la regex como `\.`.

_RECORD_REF = re.compile(r'(?P<name>[a-z_]+:[a-z_:0-9]+)\{(?P<key>[a-z_]+)="(?P<value>[^"]+)"\}')
_RANGE = re.compile(r"\[(?P<range>\d+[smhd])\](?:\s+offset\s+(?P<offset>\d+[smhd]))?")
_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def seconds(duration: str) -> int:
    """`5m` → 300 (formato de duración de Prometheus con una sola unidad)."""
    return int(duration[:-1]) * _UNITS[duration[-1]]


def lookback_seconds(query: str) -> int:
    """Lo más antiguo que lee la consulta: rango del selector más su `offset`."""
    spans = [
        seconds(m["range"]) + (seconds(m["offset"]) if m["offset"] else 0)
        for m in _RANGE.finditer(query)
    ]
    return max(spans, default=0)


def recording_rules(rules: dict[str, Any]) -> dict[tuple[str, str, str], str]:
    """(nombre, etiqueta, valor) → expresión, de las recording rules con una etiqueta."""
    records: dict[tuple[str, str, str], str] = {}
    for group in rules["groups"]:
        for rule in group["rules"]:
            if "record" in rule:
                ((key, value),) = rule["labels"].items()
                records[(rule["record"], key, value)] = rule["expr"]
    return records


def inline(expr: str, records: dict[tuple[str, str, str], str]) -> str:
    """Sustituye cada referencia a una recording rule por su expresión entre paréntesis.

    El resultado pierde la etiqueta de la recording rule (`slo`, `signal`): la expresión de un
    SLI es un cociente de sumas sin etiquetas. Por eso las alertas la llevan en `labels`, y
    `a > x and b > x` sigue casando (los dos lados con el mismo conjunto vacío de etiquetas).
    """
    names = {name for name, _, _ in records}

    def replace(match: re.Match[str]) -> str:
        name = match["name"]
        if name not in names:
            return match[0]  # Una métrica con `:` (las de Google Cloud), no una regla.
        key = (name, match["key"], match["value"])
        if key not in records:
            raise ValueError(f"Recording rule inexistente: {match[0]}")
        return f"({records[key]})"

    return _RECORD_REF.sub(replace, expr)


def platform_alerts() -> list[dict[str, Any]]:
    """Alertas sobre métricas de Google Cloud (sin equivalente local): solo en Cloud Monitoring."""
    expired = pubsub_subscription_selector(PROJECT, WORKER_SUBSCRIPTIONS, regex=True)
    log_bytes_per_day = (
        f'sum(increase({LOG_BYTES_METRIC}{{project_id="{PROJECT}"}}[6h])) * 4 / 1073741824'
    )
    return [
        {
            # Misma consulta que el autoescalador local y CREMA (queue_metrics.py, 10.G1).
            "alert": "PubSubBacklogGrowing",
            "expr": f"{pubsub_backlog_by_subscription_promql(PROJECT, WORKER_SUBSCRIPTIONS)} > 100",
            "for": "10m",
            "labels": {"severity": "page", "owner": "soporte-ia"},
            "annotations": {
                "summary": "Mensajes sin confirmar acumulándose en una cola de Pub/Sub",
                "description": (
                    "Más de 100 mensajes sin confirmar durante 10 min en una suscripción .worker."
                ),
                "runbook_url": f"{RUNBOOK}#backlog-colas",
            },
        },
        {
            # Heredado de la 10.G1: el latido evita que un turno largo deje caducar el plazo.
            "alert": "PubSubAckDeadlinesExpiring",
            "expr": (
                f"sum by (subscription_id)(rate({PUBSUB_EXPIRED_ACK_METRIC}{{{expired}}}[10m]))"
                " > 0.01"
            ),
            "for": "10m",
            "labels": {"severity": "ticket", "owner": "soporte-ia"},
            "annotations": {
                "summary": "Plazos de ack de Pub/Sub vencidos: reentregas y modelo pagado otra vez",
                "description": (
                    "Más de 0,01 plazos vencidos por segundo durante 10 min en una suscripción "
                    ".worker (latido caído o turnos más largos que PUBSUB__MAX_LEASE_S)."
                ),
                "runbook_url": f"{RUNBOOK}#backlog-colas",
            },
        },
        {
            "alert": "LogIngestionPace",
            "expr": f"{log_bytes_per_day} > {LOG_FREE_GIB_PER_DAY:.3f}",
            "for": "1h",
            "labels": {"severity": "ticket", "owner": "finops"},
            "annotations": {
                "summary": "Los logs van camino de salir del tramo gratuito de Cloud Logging",
                "description": (
                    "La ingesta de las últimas 6 h, proyectada a un día, supera "
                    f"{LOG_FREE_GIB_PER_DAY:.2f} GiB (50 GiB al mes gratis)."
                ),
                "runbook_url": f"{RUNBOOK}#ingesta-logs",
            },
        },
    ]


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    """Una política de Cloud Monitoring con una sola condición PromQL."""

    key: str
    rule_group: str
    alert_rule: str
    query: str
    duration_s: int
    evaluation_interval_s: int
    severity: str
    labels: dict[str, str]
    subject: str
    documentation: str

    def as_rule(self, source: dict[str, Any]) -> dict[str, Any]:
        """La misma alerta en formato de Prometheus (para `promtool test rules`)."""
        rule: dict[str, Any] = {"alert": self.alert_rule, "expr": self.query}
        if self.duration_s:
            rule["for"] = f"{self.duration_s}s"
        return rule | {"labels": self.labels, "annotations": source["annotations"]}

    def as_terraform(self) -> dict[str, Any]:
        return {
            "display_name": self.key,
            "rule_group": self.rule_group,
            "alert_rule": self.alert_rule,
            "query": self.query,
            "duration": f"{self.duration_s}s",
            "evaluation_interval": f"{self.evaluation_interval_s}s",
            "severity": self.severity,
            "labels": self.labels,
            "subject": self.subject,
            "documentation": self.documentation,
        }


def _key(rule: dict[str, Any]) -> str:
    labels = rule["labels"]
    parts = [rule["alert"], *(labels[k] for k in ("slo", "signal", "window") if k in labels)]
    return "-".join(parts)


def _documentation(rule: dict[str, Any], *, retest_disabled: bool) -> str:
    annotations = rule["annotations"]
    # Cloud Monitoring no entiende las plantillas de Prometheus (`{{ $value }}`): el valor se ve
    # en la incidencia, junto a la gráfica de la condición.
    description = _TEMPLATE.sub("(valor en la incidencia)", annotations["description"])
    runbook = annotations["runbook_url"].replace(RUNBOOK, f"{RUNBOOK_BASE}/{RUNBOOK}")
    lines = [description, "", f"**Runbook:** {runbook}"]
    if retest_disabled and "for" in rule:
        lines += [
            "",
            f"En Prometheus espera {rule['for']} antes de saltar; aquí salta en la primera "
            "evaluación (sin ventana de reintento para consultas de más de 25 h).",
        ]
    return "\n".join(lines)


def build_alert_policies(rules: dict[str, Any]) -> list[tuple[AlertPolicy, dict[str, Any]]]:
    """Una política por alerta de `rules` (las de `build_rules()`) más las de plataforma."""
    records = recording_rules(rules)
    sourced = [
        (group["name"], rule)
        for group in rules["groups"]
        for rule in group["rules"]
        if "alert" in rule
    ]
    sourced += [("gcp-plataforma", rule) for rule in platform_alerts()]
    policies: list[tuple[AlertPolicy, dict[str, Any]]] = []
    for group, rule in sourced:
        query = inline(rule["expr"], records)
        lookback = lookback_seconds(query)
        long_query = lookback > RETEST_MAX_LOOKBACK_S
        documentation = _documentation(rule, retest_disabled=long_query)
        if len(documentation) > DOCUMENTATION_MAX_CHARS:
            raise ValueError(f"Documentación demasiado larga en {_key(rule)}")
        policy = AlertPolicy(
            key=_key(rule),
            rule_group=group,
            alert_rule=rule["alert"],
            query=query,
            duration_s=0 if long_query else seconds(rule.get("for", "0s")),
            evaluation_interval_s=SLOW_EVALUATION_S if long_query else FAST_EVALUATION_S,
            severity=SEVERITY[rule["labels"]["severity"]],
            labels=dict(rule["labels"]),
            subject=rule["annotations"]["summary"],
            documentation=documentation,
        )
        policies.append((policy, rule))
    keys = [p.key for p, _ in policies]
    if len(keys) != len(set(keys)):
        raise ValueError("Dos alertas con la misma clave de política.")
    return policies


def build_gcp_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Las políticas como fichero de reglas de Prometheus, agrupadas como en el original."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for policy, source in build_alert_policies(rules):
        groups.setdefault(policy.rule_group, []).append(policy.as_rule(source))
    return {"groups": [{"name": name, "rules": items} for name, items in groups.items()]}


def build_terraform_policies(rules: dict[str, Any]) -> dict[str, Any]:
    """JSON que recorre `observability.tf` (`for_each` por clave de política)."""
    return {
        "policies": {policy.key: policy.as_terraform() for policy, _ in build_alert_policies(rules)}
    }


def inline_tests(tests: dict[str, Any], rules: dict[str, Any], rule_file: str) -> dict[str, Any]:
    """Los tests de `promtool` de las reglas locales, apuntados a las alertas de GCP.

    Las expresiones que consultan una recording rule (`drift:psi{...}`) se expanden igual que
    las alertas: en Cloud Monitoring esa serie no existe.
    """
    records = recording_rules(rules)
    copied: dict[str, Any] = {**tests, "rule_files": [rule_file]}
    copied["tests"] = [
        {
            **test,
            "promql_expr_test": [
                {**check, "expr": inline(check["expr"], records)}
                for check in test.get("promql_expr_test", [])
            ],
        }
        for test in tests["tests"]
    ]
    return copied
