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
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from headache_core.application.telemetry import genai
from headache_core.application.telemetry.runtime import get_telemetry
from headache_core.ports.llm import TokenUsage
from headache_core.ports.telemetry import Attributes, AttributeValue

# Latencias en segundos: de una caché (ms) a un agente con varias tools (decenas de s).
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)
# El TTFT es la experiencia del usuario: más resolución por debajo de 2 s.
TTFT_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10)

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

# Sin agente (el asistente RAG del edge, un script): el coste se atribuye a esto.
NO_AGENT = "none"


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
    telemetry = get_telemetry()
    telemetry.histogram(
        LLM_DURATION,
        unit="s",
        description="Duración de las llamadas al modelo.",
        boundaries=LATENCY_BUCKETS,
    ).record(seconds, attributes)
    if usage is not None:
        tokens = (
            (LLM_INPUT_TOKENS, usage.input_tokens, "Tokens de entrada (incluye los cacheados)."),
            (LLM_OUTPUT_TOKENS, usage.output_tokens, "Tokens de salida."),
            (LLM_CACHE_READ_TOKENS, usage.cached_input_tokens, "Tokens leídos de la caché."),
        )
        for name, amount, description in tokens:
            telemetry.counter(name, unit="{token}", description=description).add(amount, attributes)
    if cost_usd is not None:
        telemetry.counter(LLM_COST, unit="USD", description="Coste estimado del modelo.").add(
            float(cost_usd), attributes
        )


def llm_first_token(seconds: float, attributes: Attributes) -> None:
    get_telemetry().histogram(
        LLM_TTFT,
        unit="s",
        description="Tiempo hasta el primer token de una respuesta en streaming.",
        boundaries=TTFT_BUCKETS,
    ).record(seconds, attributes)


def llm_rejected(*, deployment: str, reason: str) -> None:
    """El gateway rechazó sin llamar al proveedor (circuito abierto, sin hueco, sin cuota)."""
    get_telemetry().counter(
        LLM_REJECTIONS,
        unit="{request}",
        description="Llamadas al modelo rechazadas en el cliente.",
    ).add(1, {genai.HEADACHE_DEPLOYMENT: deployment, "headache.llm.reject_reason": reason})


def tool_call(*, tool: str, seconds: float, error: str | None, agent: str | None) -> None:
    get_telemetry().histogram(
        TOOL_DURATION, unit="s", description="Duración de las tools.", boundaries=LATENCY_BUCKETS
    ).record(
        seconds,
        _attributes(
            **{genai.TOOL_NAME: tool, genai.ERROR_TYPE: error, genai.AGENT_NAME: agent or NO_AGENT}
        ),
    )


def agent_turn(
    *, agent: str, seconds: float, stop_reason: str, guardrails: tuple[str, ...]
) -> None:
    telemetry = get_telemetry()
    telemetry.histogram(
        AGENT_DURATION,
        unit="s",
        description="Duración de un turno de agente.",
        boundaries=LATENCY_BUCKETS,
    ).record(seconds, {genai.AGENT_NAME: agent, genai.HEADACHE_STOP_REASON: stop_reason})
    triggers = telemetry.counter(
        GUARDRAIL_TRIGGERS, unit="{trigger}", description="Reglas del harness disparadas."
    )
    for code in guardrails:
        triggers.add(1, {genai.AGENT_NAME: agent, "headache.guardrail.code": code})


def admission(*, route: str, plan: str, outcome: str) -> None:
    get_telemetry().counter(
        ADMISSION_DECISIONS,
        unit="{request}",
        description="Decisiones de admisión del edge (admitted, shed, rate_limited).",
    ).add(
        1, {"headache.route": route, genai.HEADACHE_PLAN: plan, genai.HEADACHE_ADMISSION: outcome}
    )


def answer(*, seconds: float, attributes: Mapping[str, AttributeValue]) -> None:
    """Una respuesta del asistente entregada: base de la tasa de aciertos de caché, de la
    degradación y de la latencia de punta a punta por plan."""
    telemetry = get_telemetry()
    telemetry.counter(ANSWERS, unit="{answer}", description="Respuestas del asistente.").add(
        1, attributes
    )
    telemetry.histogram(
        ANSWER_DURATION,
        unit="s",
        description="Tiempo hasta la respuesta completa en el edge.",
        boundaries=LATENCY_BUCKETS,
    ).record(seconds, attributes)


def answer_first_chunk(seconds: float, attributes: Attributes) -> None:
    get_telemetry().histogram(
        ANSWER_TTFT,
        unit="s",
        description="Tiempo hasta el primer trozo SSE que ve el usuario.",
        boundaries=TTFT_BUCKETS,
    ).record(seconds, attributes)


def stream_error(reason: str) -> None:
    """Un SSE que se rompe con el 200 ya enviado: no aparece como 5xx en las métricas HTTP."""
    get_telemetry().counter(
        STREAM_ERRORS, unit="{stream}", description="Streams SSE rotos tras empezar."
    ).add(1, {genai.ERROR_TYPE: reason})


def cache_lookup(reason: str) -> None:
    get_telemetry().counter(
        CACHE_LOOKUPS, unit="{lookup}", description="Consultas a la caché semántica por motivo."
    ).add(1, {genai.HEADACHE_CACHE_REASON: reason})


def message_sent(*, system: str, destination: str) -> None:
    get_telemetry().counter(
        MESSAGES_SENT, unit="{message}", description="Mensajes publicados."
    ).add(1, {"messaging.system": system, "messaging.destination.name": destination})


def message_processed(*, system: str, destination: str, seconds: float, error: str | None) -> None:
    get_telemetry().histogram(
        MESSAGE_PROCESS_DURATION,
        unit="s",
        description="Duración del procesado de un mensaje (cada entrega).",
        boundaries=LATENCY_BUCKETS,
    ).record(
        seconds,
        _attributes(
            **{
                "messaging.system": system,
                "messaging.destination.name": destination,
                genai.ERROR_TYPE: error,
            }
        ),
    )


def outbox_backlog(count: int, *, topic_prefix: str) -> None:
    get_telemetry().gauge(
        OUTBOX_BACKLOG, unit="{event}", description="Eventos del outbox sin publicar."
    ).set(count, {"headache.outbox.topic_prefix": topic_prefix or "*"})
