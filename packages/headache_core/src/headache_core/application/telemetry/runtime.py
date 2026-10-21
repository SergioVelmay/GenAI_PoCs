"""Telemetría del proceso: una instancia global, como el `logging` de la librería estándar.

Se fija una vez al arrancar el servicio (`set_telemetry`) y los casos de uso la piden con
`get_telemetry()`. Así no hay que pasarla por todos los constructores, y por defecto (tests,
scripts) no se traza nada ni hace falta el SDK de OpenTelemetry instalado.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar

from headache_core.ports.telemetry import (
    Attributes,
    AttributeValue,
    CounterPort,
    GaugePort,
    HistogramPort,
    SpanKind,
    SpanPort,
    TelemetryPort,
    TraceCarrier,
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

    def mark_error(self, error_type: str) -> None:
        pass

    def end(self) -> None:
        pass


_NULL_SPAN = NullSpan()


class NullInstrument:
    def add(self, amount: float, attributes: Attributes | None = None) -> None:
        pass

    def record(self, value: float, attributes: Attributes | None = None) -> None:
        pass

    def set(self, value: float, attributes: Attributes | None = None) -> None:
        pass


_NULL_INSTRUMENT = NullInstrument()


class NullTelemetry:
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
        parent: TraceCarrier | None = None,
        links: Sequence[TraceCarrier] = (),
    ) -> AbstractContextManager[SpanPort]:
        return _null_span()

    def inject(self) -> dict[str, str]:
        return {}

    def counter(self, name: str, *, unit: str, description: str) -> CounterPort:
        return _NULL_INSTRUMENT

    def histogram(
        self,
        name: str,
        *,
        unit: str,
        description: str,
        boundaries: Sequence[float] | None = None,
    ) -> HistogramPort:
        return _NULL_INSTRUMENT

    def gauge(self, name: str, *, unit: str, description: str) -> GaugePort:
        return _NULL_INSTRUMENT

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

    def current_span(self) -> SpanPort:
        return _NULL_SPAN

    def content(self, text: str) -> str | None:
        return None


@contextmanager
def _null_span() -> Iterator[SpanPort]:
    yield _NULL_SPAN


_telemetry: TelemetryPort = NullTelemetry()

# Agente que está ejecutando (lo fija `TracedAgent`): las métricas de modelo y tools lo usan
# para repartir el coste por agente sin pasarlo por todas las capas del gateway.
_CURRENT_AGENT: ContextVar[str | None] = ContextVar("current_agent", default=None)


def current_agent() -> str | None:
    return _CURRENT_AGENT.get()


@contextmanager
def agent_scope(name: str) -> Iterator[None]:
    token = _CURRENT_AGENT.set(name)
    try:
        yield
    finally:
        _CURRENT_AGENT.reset(token)


def set_telemetry(telemetry: TelemetryPort) -> None:
    global _telemetry
    _telemetry = telemetry


def get_telemetry() -> TelemetryPort:
    return _telemetry
