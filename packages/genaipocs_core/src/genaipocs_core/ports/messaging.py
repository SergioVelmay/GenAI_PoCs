"""Puerto de mensajería asíncrona (RabbitMQ en local, SQS en AWS, Service Bus en Azure)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Message:
    topic: str
    payload: Mapping[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # 1 en la primera entrega; > 1 si es un reintento (entrega at-least-once).
    delivery_count: int = 1
    # Metadatos fuera del cuerpo (contexto de traza W3C): cabeceras en RabbitMQ, atributos en
    # SQS, propiedades de aplicación en Service Bus.
    headers: Mapping[str, str] = field(default_factory=dict)


# Lo único que los adaptadores copian de las cabeceras del broker a `Message.headers`: el
# contexto W3C. El resto (x-delivery-count, atributos de sistema) es cosa del broker.
PROPAGATED_HEADERS = ("traceparent", "tracestate", "baggage")


def propagated_headers(raw: Mapping[str, object] | None) -> dict[str, str]:
    return {
        key: value
        for key, value in (raw or {}).items()
        if key in PROPAGATED_HEADERS and isinstance(value, str)
    }


class PermanentMessageError(Exception):
    """Mensaje imposible de procesar (mal formado, inválido): va directo a la DLQ, sin reintentos.

    Cualquier otra excepción del handler se considera transitoria y el mensaje se reintenta
    hasta agotar el máximo de entregas; después también acaba en la DLQ.
    """


type MessageHandler = Callable[[Message], Awaitable[None]]


class MessageBusPort(Protocol):
    """Cola con semántica at-least-once: los handlers deben ser idempotentes."""

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Message: ...

    async def subscribe(self, topic: str, handler: MessageHandler) -> None: ...

    async def close(self) -> None: ...
