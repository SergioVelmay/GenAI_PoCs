"""Observabilidad (fase 11): convención GenAI, telemetría global, trazas del modelo y logs."""

from headache_core.application.telemetry.llm_tracer import TelemetryLLMTracer
from headache_core.application.telemetry.logs import JsonLogFormatter, configure_logging
from headache_core.application.telemetry.redaction import Redactor, redact_text
from headache_core.application.telemetry.runtime import (
    NullSpan,
    NullTelemetry,
    agent_scope,
    current_agent,
    get_telemetry,
    set_telemetry,
)

__all__ = [
    "JsonLogFormatter",
    "NullSpan",
    "NullTelemetry",
    "Redactor",
    "TelemetryLLMTracer",
    "agent_scope",
    "configure_logging",
    "current_agent",
    "get_telemetry",
    "redact_text",
    "set_telemetry",
]
