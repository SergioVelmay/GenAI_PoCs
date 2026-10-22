"""Del catálogo de métricas a Prometheus y Grafana (fase 11.5): nombres, consultas y dashboard.

El collector traduce los nombres de OTel a los de Prometheus (`gen_ai.client.operation.duration`
en segundos → `gen_ai_client_operation_duration_seconds_bucket`). Aquí se hace la misma
traducción para generar el dashboard (y las reglas de alerta de la 11.6) desde `CATALOG`: si
alguien renombra una métrica, falla un test en vez de quedarse un panel vacío.

    uv run python scripts/build_observability.py          # regenera dashboard y reglas
    uv run python scripts/build_observability.py --check  # un test comprueba que están al día
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from headache_core.application.telemetry import genai, metrics
from headache_core.application.telemetry.metrics import CATALOG, InstrumentKind
from headache_core.domain.slo import BURN_RATE_ALERTS, COST_BUDGET, SLOS, Severity

# Unidades UCUM que el collector convierte en sufijo; las anotaciones `{token}` desaparecen.
_UNITS = {"s": "seconds", "ms": "milliseconds", "By": "bytes", "1": "ratio"}


def label(attribute: str) -> str:
    """Nombre de etiqueta en Prometheus para un atributo de OTel (`gen_ai.agent.name`)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", attribute)


def metric(name: str, suffix: str = "") -> str:
    """Nombre en Prometheus de una métrica del catálogo. `suffix`: `bucket`, `count`, `sum`."""
    spec = CATALOG[name]
    base = label(name)
    unit = re.sub(r"\{.*?\}", "", spec.unit)
    # La unidad adimensional "1" solo se traduce a `_ratio` en los gauges; en el resto se
    # omite (comprobado contra el collector: `headache_eval_faithfulness_bucket`).
    if unit == "1" and spec.kind is not InstrumentKind.GAUGE:
        unit = ""
    unit = _UNITS.get(unit, unit)
    if unit and not base.endswith(f"_{unit}"):
        base = f"{base}_{unit}"
    if spec.kind is InstrumentKind.COUNTER:
        return f"{base}_total"
    if spec.kind is InstrumentKind.HISTOGRAM:
        if suffix not in ("bucket", "count", "sum"):
            raise ValueError("Un histograma se consulta por _bucket, _count o _sum.")
        return f"{base}_{suffix}"
    return base


RATE = "$__rate_interval"


def rate(
    name: str,
    *,
    by: tuple[str, ...] = (),
    where: str = "",
    suffix: str = "",
    window: str = RATE,
) -> str:
    selector = f"{metric(name, suffix)}{{{where}}}" if where else metric(name, suffix)
    grouping = f" by ({', '.join(label(b) for b in by)})" if by else ""
    return f"sum{grouping}(rate({selector}[{window}]))"


def quantile(q: float, name: str, *, by: tuple[str, ...] = (), where: str = "") -> str:
    keys = ", ".join(("le", *(label(b) for b in by)))
    selector = metric(name, "bucket") + (f"{{{where}}}" if where else "")
    return f"histogram_quantile({q}, sum by ({keys})(rate({selector}[{RATE}])))"


def ratio(numerator: str, denominator: str) -> str:
    # `or vector(0)`: sin eventos malos el numerador no tiene series y el panel saldría vacío
    # en vez de 0 %. `clamp_min`: sin tráfico, 0/0 daría NaN.
    return f"({numerator} or vector(0)) / clamp_min({denominator}, 1e-9)"


# --- Dashboard ----------------------------------------------------------------------------


def _panel(
    title: str,
    targets: list[tuple[str, str]],
    *,
    x: int,
    y: int,
    w: int = 8,
    h: int = 8,
    unit: str = "short",
    kind: str = "timeseries",
    description: str = "",
) -> dict[str, Any]:
    return {
        "type": kind,
        "title": title,
        "description": description,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        # Las proporciones van de 0 a 1: sin límites, Grafana escala el eje hasta 10 000 %.
        "fieldConfig": {
            "defaults": {"unit": unit, **({"min": 0, "max": 1} if unit == "percentunit" else {})},
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
        "targets": [
            {"refId": chr(ord("A") + i), "expr": expr, "legendFormat": legend}
            for i, (expr, legend) in enumerate(targets)
        ],
    }


def _row(title: str, y: int) -> dict[str, Any]:
    return {
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def build_dashboard() -> dict[str, Any]:
    """Coste, latencia, errores y capacidad, y calidad: los KPI de la fase 11."""
    agent = genai.AGENT_NAME
    cache = genai.HEADACHE_CACHE
    plan = genai.HEADACHE_PLAN
    m = metrics
    fresh = f'{label(cache)}!="hit"'
    panels: list[dict[str, Any]] = [
        _row("Coste", 0),
        _panel(
            "Coste por agente (USD/h)",
            [(f"3600 * {rate(m.LLM_COST, by=(agent,))}", "{{" + label(agent) + "}}")],
            x=0,
            y=1,
            unit="currencyUSD",
            description="Coste estimado con la tabla de precios; `none` = asistente del edge.",
        ),
        _panel(
            "Coste por modelo (USD/h)",
            [
                (
                    f"3600 * {rate(m.LLM_COST, by=(genai.RESPONSE_MODEL,))}",
                    "{{" + label(genai.RESPONSE_MODEL) + "}}",
                )
            ],
            x=8,
            y=1,
            unit="currencyUSD",
        ),
        _panel(
            "Tokens cacheados (prompt caching)",
            [(ratio(rate(m.LLM_CACHE_READ_TOKENS), rate(m.LLM_INPUT_TOKENS)), "cacheados")],
            x=16,
            y=1,
            unit="percentunit",
            description="Proporción de tokens de entrada servidos desde la caché del proveedor.",
        ),
        _row("Latencia", 9),
        _panel(
            "TTFT p50 / p95 por caché",
            [
                (quantile(0.5, m.ANSWER_TTFT, by=(cache,)), "p50 {{" + label(cache) + "}}"),
                (quantile(0.95, m.ANSWER_TTFT, by=(cache,)), "p95 {{" + label(cache) + "}}"),
            ],
            x=0,
            y=10,
            unit="s",
            description="Primer trozo que ve el usuario. SLO: 95 % por debajo de 1,5 s sin caché.",
        ),
        _panel(
            "Modelo p95 por despliegue",
            [
                (
                    quantile(0.95, m.LLM_DURATION, by=(genai.HEADACHE_DEPLOYMENT,)),
                    "{{" + label(genai.HEADACHE_DEPLOYMENT) + "}}",
                )
            ],
            x=8,
            y=10,
            unit="s",
        ),
        _panel(
            "Turno de agente p95",
            [(quantile(0.95, m.AGENT_DURATION, by=(agent,)), "{{" + label(agent) + "}}")],
            x=16,
            y=10,
            unit="s",
        ),
        _row("Errores y capacidad", 18),
        _panel(
            "Errores del modelo por tipo (/s)",
            [
                (
                    rate(
                        m.LLM_DURATION,
                        by=(genai.ERROR_TYPE,),
                        where=f'{label(genai.ERROR_TYPE)}!=""',
                        suffix="count",
                    ),
                    "{{" + label(genai.ERROR_TYPE) + "}}",
                ),
                (
                    rate(m.LLM_REJECTIONS, by=("headache.llm.reject_reason",)),
                    "rechazo {{headache_llm_reject_reason}}",
                ),
            ],
            x=0,
            y=19,
            unit="reqps",
            description="429 del proveedor, cuota agotada en el cliente, circuito abierto...",
        ),
        _panel(
            "Admisión por plan (/s)",
            [
                (
                    rate(m.ADMISSION_DECISIONS, by=(plan, genai.HEADACHE_ADMISSION)),
                    "{{" + label(plan) + "}} {{" + label(genai.HEADACHE_ADMISSION) + "}}",
                )
            ],
            x=8,
            y=19,
            unit="reqps",
            description="admitted, shed (503 por saturación) y rate_limited (429) por plan.",
        ),
        _panel(
            "Colas: procesado p95 y atraso del outbox",
            [
                (
                    quantile(0.95, m.MESSAGE_PROCESS_DURATION, by=("messaging.destination.name",)),
                    "p95 {{messaging_destination_name}}",
                ),
                (f"sum({metric(m.OUTBOX_BACKLOG)})", "atraso outbox"),
            ],
            x=16,
            y=19,
            description="Un atraso que crece es un relay caído o un broker rechazando.",
        ),
        _row("Calidad", 27),
        _panel(
            "Aciertos de la caché semántica",
            [
                (
                    ratio(
                        rate(m.ANSWERS, where=f'{label(cache)}=~"hit|coalesced"'), rate(m.ANSWERS)
                    ),
                    "aciertos",
                )
            ],
            x=0,
            y=28,
            unit="percentunit",
        ),
        _panel(
            "Respuestas degradadas y abstenciones",
            [
                (
                    ratio(
                        rate(m.ANSWERS, where=f'{label(genai.HEADACHE_DEGRADED)}="true"'),
                        rate(m.ANSWERS),
                    ),
                    "degradadas",
                ),
                (
                    ratio(
                        rate(m.ANSWERS, where=f'{label(genai.HEADACHE_ABSTAINED)}="true"'),
                        rate(m.ANSWERS),
                    ),
                    "sin evidencia",
                ),
                (
                    ratio(
                        rate(m.ANSWER_TTFT, where=f'{fresh},le="1.5"', suffix="bucket"),
                        rate(m.ANSWER_TTFT, where=fresh, suffix="count"),
                    ),
                    "TTFT < 1,5 s (SLO 95 %)",
                ),
            ],
            x=8,
            y=28,
            unit="percentunit",
        ),
        _panel(
            "Reglas del harness disparadas (/s)",
            [
                (
                    rate(m.GUARDRAIL_TRIGGERS, by=("headache.guardrail.code",)),
                    "{{headache_guardrail_code}}",
                )
            ],
            x=16,
            y=28,
            unit="reqps",
            description="Un pico de prompt_injection o pii_redacted es un incidente que mirar.",
        ),
        _panel(
            "Faithfulness online (LLM-juez, media 1 h)",
            [
                (
                    ratio(
                        rate(m.EVAL_FAITHFULNESS, suffix="sum", window="1h"),
                        rate(m.EVAL_FAITHFULNESS, suffix="count", window="1h"),
                    ),
                    "faithfulness",
                ),
            ],
            x=0,
            y=36,
            w=12,
            unit="percentunit",
            description="Alerta FaithfulnessDrop por debajo del 80 % con al menos 20 muestras.",
        ),
        _panel(
            "Evaluación online: muestras por resultado (/min)",
            [
                (
                    f"60 * {rate(m.EVAL_SAMPLES, by=('headache.eval.outcome',))}",
                    "{{headache_eval_outcome}}",
                )
            ],
            x=12,
            y=36,
            w=12,
            description="scored, skipped (fuera de muestra), dropped (cola o presupuesto), failed.",
        ),
    ]
    for index, panel in enumerate(panels, start=1):
        panel["id"] = index
    return {
        "uid": "headache-genai",
        "title": "Headache · GenAI",
        "tags": ["headache", "genai", "fase-11"],
        "timezone": "browser",
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "schemaVersion": 41,
        "editable": False,
        "panels": panels,
        "templating": {"list": []},
        "annotations": {"list": []},
        "links": [
            {
                "title": "Jaeger",
                "type": "link",
                "url": "http://127.0.0.1:16686",
                "targetBlank": True,
            }
        ],
    }


# --- Reglas de alerta (fase 11.6) ---------------------------------------------------------

RUNBOOK = "docs/runbook-incidentes.md"


@dataclass(frozen=True, slots=True)
class Sli:
    """Tasa de eventos malos y de eventos totales en una ventana (PromQL)."""

    bad: Callable[[str], str]
    total: Callable[[str], str]


def _latency_sli(name: str, threshold: float, where: str) -> Sli:
    bucket = f'{where},le="{threshold:g}"'
    return Sli(
        bad=lambda w: (
            f"({rate(name, where=where, suffix='count', window=w)}"
            f" - {rate(name, where=bucket, suffix='bucket', window=w)})"
        ),
        total=lambda w: rate(name, where=where, suffix="count", window=w),
    )


def _slis() -> dict[str, Sli]:
    m = metrics
    errors = f'{label(genai.ERROR_TYPE)}!="",{label(genai.ERROR_TYPE)}!="CancelledError"'
    ask = f'{label("headache.route")}="ask"'
    public = f'{label(genai.HEADACHE_PLAN)}="public"'
    shed = f'{public},{label(genai.HEADACHE_ADMISSION)}="shed"'
    return {
        # Admitidas en /ask y /ask/stream que no acaban en respuesta: 5xx, 504 o stream roto.
        # Un 429 o un 503 por saturación no se admiten: no cuentan (tienen su propio SLO).
        "assistant-availability": Sli(
            bad=lambda w: (
                f"clamp_min({rate(m.ADMISSION_DECISIONS, where=ask, window=w)}"
                f" - {rate(m.ANSWERS, window=w)}, 0)"
            ),
            total=lambda w: rate(m.ADMISSION_DECISIONS, where=ask, window=w),
        ),
        "assistant-ttft": _latency_sli(m.ANSWER_TTFT, 1.5, f'{label(genai.HEADACHE_CACHE)}!="hit"'),
        "public-admission": Sli(
            bad=lambda w: rate(m.ADMISSION_DECISIONS, where=shed, window=w),
            total=lambda w: rate(m.ADMISSION_DECISIONS, where=public, window=w),
        ),
        "assistant-not-degraded": Sli(
            bad=lambda w: rate(
                m.ANSWERS, where=f'{label(genai.HEADACHE_DEGRADED)}="true"', window=w
            ),
            total=lambda w: rate(m.ANSWERS, window=w),
        ),
        "case-processing": Sli(
            bad=lambda w: rate(m.MESSAGE_PROCESS_DURATION, where=errors, suffix="count", window=w),
            total=lambda w: rate(m.MESSAGE_PROCESS_DURATION, suffix="count", window=w),
        ),
        # Cancelaciones (el usuario cerró el stream) no son culpa del proveedor.
        "llm-calls": Sli(
            bad=lambda w: (
                f"({rate(m.LLM_DURATION, where=errors, suffix='count', window=w)} or vector(0))"
                f" + ({rate(m.LLM_REJECTIONS, window=w)} or vector(0))"
            ),
            total=lambda w: (
                f"{rate(m.LLM_DURATION, suffix='count', window=w)}"
                f" + ({rate(m.LLM_REJECTIONS, window=w)} or vector(0))"
            ),
        ),
    }


def _window_seconds(window: str) -> int:
    units = {"m": 60, "h": 3600, "d": 86400}
    return int(window[:-1]) * units[window[-1]]


def _record(window: str) -> str:
    return f"slo:error_ratio:rate{window}"


def build_rules() -> dict[str, Any]:
    """Reglas de Prometheus: SLI por ventana (recording) y alertas de *burn rate* y de coste."""
    slis = _slis()
    missing = {slo.name for slo in SLOS} - slis.keys()
    if missing:
        raise ValueError(f"SLO sin SLI en Prometheus: {sorted(missing)}")
    windows = sorted(
        {w for a in BURN_RATE_ALERTS for w in (a.long_window, a.short_window)},
        key=_window_seconds,
    )
    recording = [
        {
            "record": _record(window),
            "expr": ratio(slis[slo.name].bad(window), slis[slo.name].total(window)),
            "labels": {"slo": slo.name},
        }
        for slo in SLOS
        for window in windows
    ]
    alerts = []
    for slo in SLOS:
        for alert in BURN_RATE_ALERTS:
            threshold = round(alert.threshold(slo), 6)
            selector = f'{{slo="{slo.name}"}}'
            alerts.append(
                {
                    "alert": "ErrorBudgetBurn",
                    "expr": (
                        f"{_record(alert.long_window)}{selector} > {threshold}"
                        f" and {_record(alert.short_window)}{selector} > {threshold}"
                    ),
                    "for": "2m" if alert.severity is Severity.PAGE else "15m",
                    "labels": {
                        "severity": alert.severity.value,
                        "slo": slo.name,
                        "owner": slo.owner,
                        "window": alert.long_window,
                    },
                    "annotations": {
                        "summary": (
                            f"{slo.name}: presupuesto de error quemándose a {alert.burn_rate:g}x"
                        ),
                        "description": (
                            f"{slo.description} Objetivo {slo.objective:.1%} en "
                            f"{slo.window_days} días. Error en {alert.long_window}: "
                            "{{ $value | humanizePercentage }}."
                        ),
                        "runbook_url": f"{RUNBOOK}#{slo.name}",
                    },
                }
            )
    return {
        "groups": [
            {"name": "slo-sli", "interval": "30s", "rules": recording},
            {"name": "slo-burn-rate", "rules": alerts},
            {"name": "operacion", "rules": _operational_alerts()},
        ]
    }


def _operational_alerts() -> list[dict[str, Any]]:
    m = metrics
    budget = COST_BUDGET
    cost_rate_per_day = f"86400 * {rate(m.LLM_COST, window='1h')}"
    runaway_per_day = f"86400 * {rate(m.LLM_COST, window='15m')}"
    faithfulness = ratio(
        rate(m.EVAL_FAITHFULNESS, suffix="sum", window="1h"),
        rate(m.EVAL_FAITHFULNESS, suffix="count", window="1h"),
    )
    injections = f'{label("headache.guardrail.code")}=~"prompt_injection|indirect_injection"'
    return [
        {
            "alert": "LlmCostBudget",
            "expr": f"{cost_rate_per_day} > {budget.daily_usd * budget.warn_at:g}",
            "for": "15m",
            "labels": {"severity": "ticket", "owner": "finops"},
            "annotations": {
                "summary": "El gasto en modelos va camino de pasar el presupuesto diario",
                "description": (
                    f'Al ritmo de la última hora se gastarían {{{{ $value | printf "%.2f" }}}} $'
                    f" al día (presupuesto {budget.daily_usd:g} $, aviso al {budget.warn_at:.0%})."
                ),
                "runbook_url": f"{RUNBOOK}#coste",
            },
        },
        {
            "alert": "LlmCostRunaway",
            "expr": f"{runaway_per_day} > {budget.daily_usd * 3:g}",
            "for": "5m",
            "labels": {"severity": "page", "owner": "plataforma-ia"},
            "annotations": {
                "summary": "Gasto en modelos disparado (posible bucle de agente o abuso)",
                "description": (
                    "En los últimos 15 min se gasta a un ritmo de "
                    '{{ $value | printf "%.2f" }} $/día, el triple del presupuesto.'
                ),
                "runbook_url": f"{RUNBOOK}#coste",
            },
        },
        {
            "alert": "OutboxBacklogGrowing",
            "expr": f"sum({metric(m.OUTBOX_BACKLOG)}) > 100",
            "for": "10m",
            "labels": {"severity": "page", "owner": "soporte-ia"},
            "annotations": {
                "summary": "Eventos atascados en el outbox: los casos no avanzan",
                "description": "{{ $value }} eventos sin publicar desde hace 10 min.",
                "runbook_url": f"{RUNBOOK}#outbox",
            },
        },
        {
            "alert": "FaithfulnessDrop",
            "expr": (
                f"{faithfulness} < 0.8"
                f" and sum(increase({metric(m.EVAL_FAITHFULNESS, 'count')}[1h])) >= 20"
            ),
            "for": "15m",
            "labels": {"severity": "ticket", "owner": "plataforma-ia"},
            "annotations": {
                "summary": "Las respuestas se apoyan menos en las fuentes (evaluación online)",
                "description": (
                    "Faithfulness media de la última hora: {{ $value | humanizePercentage }} "
                    "(umbral 80 %, mínimo 20 muestras)."
                ),
                "runbook_url": f"{RUNBOOK}#faithfulness",
            },
        },
        {
            "alert": "PromptInjectionSpike",
            "expr": f"{rate(m.GUARDRAIL_TRIGGERS, where=injections, window='10m')} > 0.05",
            "for": "10m",
            "labels": {"severity": "ticket", "owner": "seguridad"},
            "annotations": {
                "summary": "Pico de intentos de inyección bloqueados por el harness",
                "description": "{{ $value | humanize }} bloqueos/s en 10 min.",
                "runbook_url": f"{RUNBOOK}#inyeccion",
            },
        },
    ]
