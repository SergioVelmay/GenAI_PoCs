"""Logs estructurados (JSON por línea) correlacionados con la traza y sin PII.

Cada línea lleva `trace_id` y `span_id` del span activo: desde un log de error se salta a la
traza completa en Jaeger, App Insights o X-Ray, y al revés. El mensaje, los campos extra y la
traza de la excepción pasan por el redactor antes de salir del proceso.

En GCP (`gcp`, fase 11.G3) Cloud Run manda cada línea de stdout a Cloud Logging, que solo entiende
sus propios campos: `severity` (si no, todo sale como DEFAULT) y `logging.googleapis.com/trace`
con la ruta completa (`projects/<p>/traces/<id>`), que es lo que pinta el log dentro de la traza
en Cloud Trace. Es lo que en Azure hace el exporter de App Insights y en AWS el `trace_id` de
X-Ray: aquí no hace falta exporter de logs, basta el formato.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from genaipocs_core.application.telemetry.redaction import Redactor, redact_text
from genaipocs_core.application.telemetry.runtime import get_telemetry
from genaipocs_core.ports.telemetry import TelemetryPort

GCP_LOGGING = "logging.googleapis.com"

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
        gcp_project: str | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._redact = redact
        self._telemetry = telemetry
        self._gcp_project = gcp_project

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
        if self._gcp_project is not None:
            payload["severity"] = record.levelname
            if ids is not None:
                payload[f"{GCP_LOGGING}/trace"] = (
                    f"projects/{self._gcp_project}/traces/{ids.trace_id}"
                )
                payload[f"{GCP_LOGGING}/spanId"] = ids.span_id
                payload[f"{GCP_LOGGING}/trace_sampled"] = ids.sampled
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
    log_format: Literal["text", "json", "gcp"] = "text",
    level: str = "INFO",
    redact: Redactor = redact_text,
    gcp_project: str = "",
) -> None:
    """Configura el logger raíz del proceso: texto para leer en local, JSON para la nube."""
    handler = logging.StreamHandler()
    if log_format == "gcp" and not gcp_project:
        raise ValueError("TELEMETRY__LOG_FORMAT=gcp necesita GCP__PROJECT (ruta de la traza).")
    if log_format != "text":
        handler.setFormatter(
            JsonLogFormatter(
                service=service,
                redact=redact,
                gcp_project=gcp_project if log_format == "gcp" else None,
            )
        )
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
