"""Telemetría del proceso: una instancia global, como el `logging` de la librería estándar.

Se fija una vez al arrancar el servicio (`set_telemetry`) y los casos de uso la piden con
`get_telemetry()`. Así no hay que pasarla por todos los constructores, y por defecto (tests,
scripts) no se traza nada ni hace falta el SDK de OpenTelemetry instalado.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from headache_core.ports.telemetry import (
    Attributes,
    AttributeValue,
    SpanKind,
    SpanPort,
    TelemetryPort,
    TraceIds,
)


class NullSpan:
    def set_attribute(self, key: str, value: AttributeValue) -> None:
        pass

    def set_attributes(self, attributes: Attributes) -> None:
        pass

    def update_name(self, name: str) -> None:
        pass

    def add_event(self, name: str, attributes: Attributes | None = None) -> None:
        pass

    def record_error(self, error: BaseException) -> None:
        pass

    def end(self) -> None:
        pass


_NULL_SPAN = NullSpan()


class NullTelemetry:
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
    ) -> AbstractContextManager[SpanPort]:
        return _null_span()

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
    ) -> SpanPort:
        return _NULL_SPAN

    def current_ids(self) -> TraceIds | None:
        return None


@contextmanager
def _null_span() -> Iterator[SpanPort]:
    yield _NULL_SPAN


_telemetry: TelemetryPort = NullTelemetry()


def set_telemetry(telemetry: TelemetryPort) -> None:
    global _telemetry
    _telemetry = telemetry


def get_telemetry() -> TelemetryPort:
    return _telemetry
