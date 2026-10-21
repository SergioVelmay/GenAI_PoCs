"""Del catálogo de métricas a Prometheus y Grafana (fase 11.5): nombres, consultas y dashboard.

El collector traduce los nombres de OTel a los de Prometheus (`gen_ai.client.operation.duration`
en segundos → `gen_ai_client_operation_duration_seconds_bucket`). Aquí se hace la misma
traducción para generar el dashboard (y las reglas de alerta de la 11.6) desde `CATALOG`: si
alguien renombra una métrica, falla un test en vez de quedarse un panel vacío.

    uv run python scripts/build_observability.py          # regenera el dashboard
    uv run python scripts/build_observability.py --check  # la CI comprueba que está al día
"""

from __future__ import annotations

import re
from typing import Any

from headache_core.application.telemetry import genai, metrics
from headache_core.application.telemetry.metrics import CATALOG, InstrumentKind

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


def rate(name: str, *, by: tuple[str, ...] = (), where: str = "", suffix: str = "") -> str:
    selector = f"{metric(name, suffix)}{{{where}}}" if where else metric(name, suffix)
    grouping = f" by ({', '.join(label(b) for b in by)})" if by else ""
    return f"sum{grouping}(rate({selector}[{RATE}]))"


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
