"""Logs estructurados (JSON por línea) correlacionados con la traza y sin PII.

Cada línea lleva `trace_id` y `span_id` del span activo: desde un log de error se salta a la
traza completa en Jaeger, App Insights o X-Ray, y al revés. El mensaje, los campos extra y la
traza de la excepción pasan por el redactor antes de salir del proceso.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from headache_core.application.telemetry.redaction import Redactor, redact_text
from headache_core.application.telemetry.runtime import get_telemetry
from headache_core.ports.telemetry import TelemetryPort

# Atributos que trae todo LogRecord: lo demás son campos `extra=` del que escribe el log.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def _clean(value: Any, redact: Redactor) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return redact(str(value))


class JsonLogFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        service: str,
        redact: Redactor = redact_text,
        telemetry: TelemetryPort | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._redact = redact
        self._telemetry = telemetry

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "message": self._redact(record.getMessage()),
        }
        ids = (self._telemetry or get_telemetry()).current_ids()
        if ids is not None:
            payload["trace_id"] = ids.trace_id
            payload["span_id"] = ids.span_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _clean(value, self._redact)
        if record.exc_info and record.exc_info[1] is not None:
            payload["error.type"] = type(record.exc_info[1]).__name__
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    service: str,
    log_format: Literal["text", "json"] = "text",
    level: str = "INFO",
    redact: Redactor = redact_text,
) -> None:
    """Configura el logger raíz del proceso: texto para leer en local, JSON para la nube."""
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter(service=service, redact=redact))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
