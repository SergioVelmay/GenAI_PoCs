"""Puerto de telemetría (fase 11): spans de traza sin depender de un SDK concreto.

El adaptador real es OpenTelemetry (`adapters/otel.py`); por defecto no se traza nada
(`NullTelemetry`). Los casos de uso piden spans a este puerto igual que escriben con `logging`:
sin saber si acaban en Jaeger, Langfuse, App Insights o X-Ray. Eso lo decide el collector.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

type AttributeValue = str | bool | int | float | Sequence[str]
type Attributes = Mapping[str, AttributeValue]
# Contexto de traza serializado (W3C `traceparent`/`tracestate`): viaja en cabeceras HTTP, en
# las propiedades de un mensaje de cola o en una columna del outbox.
type TraceCarrier = Mapping[str, str]


class SpanKind(StrEnum):
    """Papel del span en la llamada (decide cómo lo dibujan los backends)."""

    INTERNAL = "internal"
    SERVER = "server"  # recibe una petición (edge, A2A)
    CLIENT = "client"  # llama a otro (modelo, MCP, A2A, base de datos)
    PRODUCER = "producer"  # publica en una cola (relay del outbox)
    CONSUMER = "consumer"  # procesa un mensaje (workers)


@dataclass(frozen=True, slots=True)
class TraceIds:
    """Identificadores W3C en hexadecimal: los que se escriben en los logs."""

    trace_id: str
    span_id: str


class SpanPort(Protocol):
    def set_attribute(self, key: str, value: AttributeValue) -> None: ...

    def set_attributes(self, attributes: Attributes) -> None: ...

    def update_name(self, name: str) -> None: ...

    def add_event(self, name: str, attributes: Attributes | None = None) -> None: ...

    def record_error(self, error: BaseException) -> None:
        """Marca el span como fallido y guarda el error, con el mensaje redactado."""
        ...

    def mark_error(self, error_type: str) -> None:
        """Marca el span como fallido sin excepción (una tool que devuelve un error tipado)."""
        ...

    def end(self) -> None: ...


class CounterPort(Protocol):
    """Solo sube (peticiones, tokens, dólares). En Prometheus, `rate()` sobre él."""

    def add(self, amount: float, attributes: Attributes | None = None) -> None: ...


class HistogramPort(Protocol):
    """Distribución (latencias): permite percentiles y contar lo que supera un umbral."""

    def record(self, value: float, attributes: Attributes | None = None) -> None: ...


class GaugePort(Protocol):
    """El último valor observado (atraso del outbox, longitud de cola)."""

    def set(self, value: float, attributes: Attributes | None = None) -> None: ...


class TelemetryPort(Protocol):
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
        parent: TraceCarrier | None = None,
        links: Sequence[TraceCarrier] = (),
    ) -> AbstractContextManager[SpanPort]:
        """Span hijo del actual que pasa a ser el actual dentro del `with`.

        `parent`: contexto que llegó de fuera (un mensaje, el outbox); el span continúa esa
        traza en vez de la actual. `links`: otras trazas relacionadas sin ser su padre.
        Una excepción que sale del `with` lo marca como fallido y se propaga.
        """
        ...

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
    ) -> SpanPort:
        """Span hijo del actual que **no** pasa a ser el actual: para operaciones que se abren y
        se cierran en sitios distintos (una llamada al modelo en streaming). Hay que cerrarlo."""
        ...

    def current_ids(self) -> TraceIds | None:
        """IDs del span actual, o `None` si no hay traza (para correlacionar los logs)."""
        ...

    def inject(self) -> dict[str, str]:
        """El contexto del span actual, listo para viajar (vacío si no hay traza)."""
        ...

    # Métricas (fase 11.4). Pedir el mismo nombre devuelve el mismo instrumento. Los
    # atributos tienen que ser de cardinalidad baja: cada combinación es una serie temporal.
    def counter(self, name: str, *, unit: str, description: str) -> CounterPort: ...

    def histogram(
        self,
        name: str,
        *,
        unit: str,
        description: str,
        boundaries: Sequence[float] | None = None,
    ) -> HistogramPort: ...

    def gauge(self, name: str, *, unit: str, description: str) -> GaugePort: ...

    def current_span(self) -> SpanPort:
        """El span activo, para enriquecerlo (plan del usuario, resultado de la caché...).

        Sin traza devuelve uno que no hace nada: quien lo usa no tiene que comprobarlo.
        """
        ...

    def content(self, text: str) -> str | None:
        """Texto apto para un atributo (prompt, argumentos, resultado): redactado si la captura
        de contenido está activa; `None` si no lo está (y entonces no se guarda)."""
        ...
