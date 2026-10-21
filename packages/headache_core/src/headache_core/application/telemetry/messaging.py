"""La traza cruza las colas (fase 11.3): decorador de `MessageBusPort` con la convención de
mensajería de OTel.

- `publish`: span PRODUCER `send {cola}` y su contexto viaja en las cabeceras del mensaje.
- `subscribe`: cada entrega abre un span CONSUMER `process {cola}` cuyo padre es ese contexto.

Así, lo que hace el worker (modelo, tools, el siguiente evento) cuelga de la misma traza que
la petición que lo originó, aunque pasen minutos y procesos por medio. Cada reentrega es otro
`process` bajo el mismo `send`: los reintentos se ven en la traza.

Padre frente a *link*: con padre, un caso es una sola traza de la entrada al resultado (lo que
se quiere para depurar). Un consumidor por lotes (un mensaje que agrega muchos) usaría links.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from headache_core.application.telemetry import genai
from headache_core.application.telemetry.runtime import get_telemetry
from headache_core.ports.messaging import (
    Message,
    MessageBusPort,
    MessageHandler,
    PermanentMessageError,
)
from headache_core.ports.telemetry import AttributeValue, SpanKind

MESSAGING_SYSTEM = "messaging.system"
DESTINATION = "messaging.destination.name"
OPERATION_TYPE = "messaging.operation.type"
MESSAGE_ID = "messaging.message.id"
DELIVERY_COUNT = "headache.messaging.delivery_count"
CORRELATION_ID = "headache.correlation_id"


def _correlation(payload: Mapping[str, Any]) -> dict[str, AttributeValue]:
    value = payload.get("correlation_id")
    return {CORRELATION_ID: value} if isinstance(value, str) else {}


class TracedMessageBus:
    def __init__(self, inner: MessageBusPort, *, system: str) -> None:
        self._inner = inner
        self._system = system

    @property
    def inner(self) -> MessageBusPort:
        return self._inner

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Message:
        telemetry = get_telemetry()
        with telemetry.span(
            f"send {topic}",
            kind=SpanKind.PRODUCER,
            attributes={
                MESSAGING_SYSTEM: self._system,
                DESTINATION: topic,
                OPERATION_TYPE: "send",
                **_correlation(payload),
            },
        ) as span:
            # El contexto de este span (no el que traía `headers`): el consumidor cuelga de él.
            carrier = {**(headers or {}), **telemetry.inject()}
            message = await self._inner.publish(topic, payload, headers=carrier)
            span.set_attribute(MESSAGE_ID, message.id)
            return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        async def traced(message: Message) -> None:
            telemetry = get_telemetry()
            with telemetry.span(
                f"process {topic}",
                kind=SpanKind.CONSUMER,
                parent=message.headers,
                attributes={
                    MESSAGING_SYSTEM: self._system,
                    DESTINATION: topic,
                    OPERATION_TYPE: "process",
                    MESSAGE_ID: message.id,
                    DELIVERY_COUNT: message.delivery_count,
                    **_correlation(message.payload),
                },
            ) as span:
                try:
                    await handler(message)
                except PermanentMessageError:
                    # A la DLQ sin reintentos: queda marcado para buscarlo en la traza.
                    span.set_attribute(genai.ERROR_TYPE, "dead_letter")
                    raise

        await self._inner.subscribe(topic, traced)

    async def close(self) -> None:
        await self._inner.close()
