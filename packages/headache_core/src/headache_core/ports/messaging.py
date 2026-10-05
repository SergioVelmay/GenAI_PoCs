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


type MessageHandler = Callable[[Message], Awaitable[None]]


class MessageBusPort(Protocol):
    async def publish(self, topic: str, payload: Mapping[str, Any]) -> Message: ...

    async def subscribe(self, topic: str, handler: MessageHandler) -> None: ...
