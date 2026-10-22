"""Catálogo de métricas (fase 11.4): nombres, unidades, cubos y atributos en un solo sitio.

RED por pieza (*Rate*, *Errors*, *Duration*) más lo propio de GenAI: tokens, coste, tiempo
hasta el primer token, caché, degradación y rechazos por cuota. Los nombres `gen_ai.*` y
`messaging.*` son los de la convención de OTel; los `headache.*`, nuestros.

Regla de cardinalidad: solo atributos con pocos valores posibles (modelo, nivel, plan, ruta,
agente, tool, resultado, tipo de error). Nunca un ID de caso, de usuario o de hilo: cada
combinación de atributos es una serie temporal que se paga en Prometheus, App Insights o
CloudWatch (en CloudWatch, cada combinación es una métrica facturada).

Las trazas responden "¿qué pasó en ESTA petición?"; las métricas, "¿cuántas van mal y desde
cuándo?". Las alertas y los SLO (11.6) se calculan sobre estas métricas, no sobre trazas.

`CATALOG` es la fuente de verdad: el dashboard y las reglas de alerta (11.5 y 11.6) se generan
a partir de él, así que renombrar una métrica rompe un test y no un panel en silencio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from headache_core.application.telemetry import genai
from headache_core.application.telemetry.runtime import get_telemetry
from headache_core.ports.llm import TokenUsage
from headache_core.ports.telemetry import (
    Attributes,
    AttributeValue,
    CounterPort,
    GaugePort,
    HistogramPort,
)

# Latencias en segundos: de una caché (ms) a un agente con varias tools (decenas de s).
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)
# El TTFT es la experiencia del usuario: más resolución por debajo de 2 s.
TTFT_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10)
# Puntuaciones de 0 a 1 (faithfulness): 0,8 es el umbral de "respuesta fiel".
SCORE_BUCKETS = (0.2, 0.4, 0.6, 0.8, 0.9, 1.0)


class InstrumentKind(StrEnum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"


@dataclass(frozen=True, slots=True)
class Instrument:
    name: str
    kind: InstrumentKind
    unit: str
    description: str
    boundaries: tuple[float, ...] | None = None


def _counter(name: str, unit: str, description: str) -> Instrument:
    return Instrument(name, InstrumentKind.COUNTER, unit, description)


def _histogram(name: str, description: str, boundaries: tuple[float, ...]) -> Instrument:
    return Instrument(name, InstrumentKind.HISTOGRAM, "s", description, boundaries)


CATALOG: dict[str, Instrument] = {
    i.name: i
    for i in (
        _histogram(
            "gen_ai.client.operation.duration",
            "Duración de las llamadas al modelo.",
            LATENCY_BUCKETS,
        ),
        _counter(
            "gen_ai.client.inference.usage.input_tokens",
            "{token}",
            "Tokens de entrada (incluye los cacheados).",
        ),
        _counter("gen_ai.client.inference.usage.output_tokens", "{token}", "Tokens de salida."),
        _counter(
            "gen_ai.client.inference.usage.cache_read.input_tokens",
            "{token}",
            "Tokens de entrada leídos de la caché del proveedor.",
        ),
        _counter("headache.llm.cost", "USD", "Coste estimado del modelo."),
        _histogram(
            "headache.llm.time_to_first_token",
            "Tiempo hasta el primer token de una respuesta en streaming.",
            TTFT_BUCKETS,
        ),
        _counter(
            "headache.llm.rejections", "{request}", "Llamadas al modelo rechazadas en el cliente."
        ),
        _histogram("gen_ai.execute_tool.duration", "Duración de las tools.", LATENCY_BUCKETS),
        _histogram(
            "gen_ai.invoke_agent.duration", "Duración de un turno de agente.", LATENCY_BUCKETS
        ),
        _counter("headache.guardrail.triggers", "{trigger}", "Reglas del harness disparadas."),
        _counter(
            "headache.admission.decisions",
            "{request}",
            "Decisiones de admisión del edge (admitted, shed, rate_limited).",
        ),
        _counter("headache.answers", "{answer}", "Respuestas del asistente."),
        _histogram(
            "headache.answer.duration",
            "Tiempo hasta la respuesta completa en el edge.",
            LATENCY_BUCKETS,
        ),
        _histogram(
            "headache.answer.time_to_first_chunk",
            "Tiempo hasta el primer trozo SSE que ve el usuario.",
            TTFT_BUCKETS,
        ),
        _counter("headache.stream.errors", "{stream}", "Streams SSE rotos tras empezar."),
        _counter(
            "headache.cache.lookups", "{lookup}", "Consultas a la caché semántica por motivo."
        ),
        _counter("messaging.client.sent.messages", "{message}", "Mensajes publicados."),
        _histogram(
            "messaging.process.duration",
            "Duración del procesado de un mensaje (cada entrega).",
            LATENCY_BUCKETS,
        ),
        Instrument(
            "headache.outbox.backlog",
            InstrumentKind.GAUGE,
            "{event}",
            "Eventos del outbox sin publicar.",
        ),
        # Evaluación online (fase 11.6): proporción de afirmaciones respaldadas por las fuentes.
        Instrument(
            "headache.eval.faithfulness",
            InstrumentKind.HISTOGRAM,
            "1",
            "Faithfulness de las respuestas muestreadas (LLM-juez).",
            SCORE_BUCKETS,
        ),
        _counter(
            "headache.eval.samples",
            "{sample}",
            "Respuestas muestreadas para evaluar (scored, skipped, dropped, failed).",
        ),
    )
}

LLM_DURATION = "gen_ai.client.operation.duration"
LLM_INPUT_TOKENS = "gen_ai.client.inference.usage.input_tokens"
LLM_OUTPUT_TOKENS = "gen_ai.client.inference.usage.output_tokens"
LLM_CACHE_READ_TOKENS = "gen_ai.client.inference.usage.cache_read.input_tokens"
LLM_COST = "headache.llm.cost"
LLM_TTFT = "headache.llm.time_to_first_token"
LLM_REJECTIONS = "headache.llm.rejections"
TOOL_DURATION = "gen_ai.execute_tool.duration"
AGENT_DURATION = "gen_ai.invoke_agent.duration"
GUARDRAIL_TRIGGERS = "headache.guardrail.triggers"
ADMISSION_DECISIONS = "headache.admission.decisions"
ANSWERS = "headache.answers"
ANSWER_DURATION = "headache.answer.duration"
ANSWER_TTFT = "headache.answer.time_to_first_chunk"
STREAM_ERRORS = "headache.stream.errors"
CACHE_LOOKUPS = "headache.cache.lookups"
MESSAGES_SENT = "messaging.client.sent.messages"
MESSAGE_PROCESS_DURATION = "messaging.process.duration"
OUTBOX_BACKLOG = "headache.outbox.backlog"
EVAL_FAITHFULNESS = "headache.eval.faithfulness"
EVAL_SAMPLES = "headache.eval.samples"

# Sin agente (el asistente RAG del edge, un script): el coste se atribuye a esto.
NO_AGENT = "none"


def counter(name: str) -> CounterPort:
    spec = CATALOG[name]
    return get_telemetry().counter(name, unit=spec.unit, description=spec.description)


def histogram(name: str) -> HistogramPort:
    spec = CATALOG[name]
    return get_telemetry().histogram(
        name, unit=spec.unit, description=spec.description, boundaries=spec.boundaries
    )


def gauge(name: str) -> GaugePort:
    spec = CATALOG[name]
    return get_telemetry().gauge(name, unit=spec.unit, description=spec.description)


def _attributes(**values: AttributeValue | None) -> dict[str, AttributeValue]:
    return {key: value for key, value in values.items() if value is not None}


def llm_call(
    *,
    seconds: float,
    attributes: Attributes,
    usage: TokenUsage | None = None,
    cost_usd: Decimal | None = None,
) -> None:
    """Una llamada al modelo: duración (con `error.type` si falló), tokens y coste."""
    histogram(LLM_DURATION).record(seconds, attributes)
    if usage is not None:
        counter(LLM_INPUT_TOKENS).add(usage.input_tokens, attributes)
        counter(LLM_OUTPUT_TOKENS).add(usage.output_tokens, attributes)
        counter(LLM_CACHE_READ_TOKENS).add(usage.cached_input_tokens, attributes)
    if cost_usd is not None:
        counter(LLM_COST).add(float(cost_usd), attributes)


def llm_first_token(seconds: float, attributes: Attributes) -> None:
    histogram(LLM_TTFT).record(seconds, attributes)


def llm_rejected(*, deployment: str, reason: str) -> None:
    """El gateway rechazó sin llamar al proveedor (circuito abierto, sin hueco, sin cuota)."""
    counter(LLM_REJECTIONS).add(
        1, {genai.HEADACHE_DEPLOYMENT: deployment, "headache.llm.reject_reason": reason}
    )


def tool_call(*, tool: str, seconds: float, error: str | None, agent: str | None) -> None:
    histogram(TOOL_DURATION).record(
        seconds,
        _attributes(
            **{genai.TOOL_NAME: tool, genai.ERROR_TYPE: error, genai.AGENT_NAME: agent or NO_AGENT}
        ),
    )


def agent_turn(
    *, agent: str, seconds: float, stop_reason: str, guardrails: tuple[str, ...]
) -> None:
    histogram(AGENT_DURATION).record(
        seconds, {genai.AGENT_NAME: agent, genai.HEADACHE_STOP_REASON: stop_reason}
    )
    for code in guardrails:
        counter(GUARDRAIL_TRIGGERS).add(
            1, {genai.AGENT_NAME: agent, "headache.guardrail.code": code}
        )


def admission(*, route: str, plan: str, outcome: str) -> None:
    counter(ADMISSION_DECISIONS).add(
        1, {"headache.route": route, genai.HEADACHE_PLAN: plan, genai.HEADACHE_ADMISSION: outcome}
    )


def answer(*, seconds: float, attributes: Mapping[str, AttributeValue]) -> None:
    """Una respuesta del asistente entregada: base de la tasa de aciertos de caché, de la
    degradación y de la latencia de punta a punta por plan."""
    counter(ANSWERS).add(1, attributes)
    histogram(ANSWER_DURATION).record(seconds, attributes)


def answer_first_chunk(seconds: float, attributes: Attributes) -> None:
    histogram(ANSWER_TTFT).record(seconds, attributes)


def stream_error(reason: str) -> None:
    """Un SSE que se rompe con el 200 ya enviado: no aparece como 5xx en las métricas HTTP."""
    counter(STREAM_ERRORS).add(1, {genai.ERROR_TYPE: reason})


def cache_lookup(reason: str) -> None:
    counter(CACHE_LOOKUPS).add(1, {genai.HEADACHE_CACHE_REASON: reason})


def message_sent(*, system: str, destination: str) -> None:
    counter(MESSAGES_SENT).add(
        1, {"messaging.system": system, "messaging.destination.name": destination}
    )


def message_processed(*, system: str, destination: str, seconds: float, error: str | None) -> None:
    histogram(MESSAGE_PROCESS_DURATION).record(
        seconds,
        _attributes(
            **{
                "messaging.system": system,
                "messaging.destination.name": destination,
                genai.ERROR_TYPE: error,
            }
        ),
    )


def eval_sample(outcome: str, *, evaluator: str) -> None:
    counter(EVAL_SAMPLES).add(
        1, {"headache.eval.outcome": outcome, "headache.eval.name": evaluator}
    )


def eval_score(score: float, *, evaluator: str, attributes: Mapping[str, AttributeValue]) -> None:
    histogram(EVAL_FAITHFULNESS).record(score, {**attributes, "headache.eval.name": evaluator})


def outbox_backlog(count: int, *, topic_prefix: str) -> None:
    gauge(OUTBOX_BACKLOG).set(count, {"headache.outbox.topic_prefix": topic_prefix or "*"})
