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

    def end(self) -> None: ...


class TelemetryPort(Protocol):
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Attributes | None = None,
    ) -> AbstractContextManager[SpanPort]:
        """Span hijo del actual que pasa a ser el actual dentro del `with`.

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
