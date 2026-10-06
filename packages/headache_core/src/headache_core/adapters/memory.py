"""Adaptadores en memoria: implementan los puertos sin red ni coste. Base de todos los tests."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Any, Self

from headache_core.domain import (
    Customer,
    Event,
    EventCategory,
    Order,
    OrderStatus,
    Refund,
    RefundStatus,
    Session,
)
from headache_core.ports import (
    ChatMessage,
    ConflictError,
    LLMResponse,
    Message,
    MessageHandler,
    MetadataValue,
    SearchResult,
    TokenUsage,
    VectorRecord,
)


class FakeLLM:
    """Devuelve respuestas guionizadas (o hace eco) y registra cada llamada."""

    def __init__(self, responses: Sequence[str] = (), model: str = "fake-llm") -> None:
        self._responses = list(responses)
        self._model = model
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        content = self._responses.pop(0) if self._responses else messages[-1].content
        usage = TokenUsage(
            input_tokens=sum(len(m.content.split()) for m in messages),
            output_tokens=len(content.split()),
        )
        return LLMResponse(content=content, model=self._model, usage=usage)


class HashingEmbedding:
    """Embedding determinista (bolsa de palabras con hashing). Útil para tests, no semántico."""

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
            vector[int.from_bytes(digest, "big") % self._dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            self._records[record.id] = record

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, MetadataValue] | None = None,
    ) -> list[SearchResult]:
        candidates = [
            r
            for r in self._records.values()
            if not filters or all(r.metadata.get(k) == v for k, v in filters.items())
        ]
        scored = [SearchResult(r, _cosine(query_embedding, r.embedding)) for r in candidates]
        return sorted(scored, key=lambda s: s.score, reverse=True)[:top_k]

    async def delete(self, ids: Sequence[str]) -> None:
        for record_id in ids:
            self._records.pop(record_id, None)


class InMemoryMessageBus:
    """Entrega síncrona a los suscriptores. Reintentos y DLQ reales llegan en Fase 3."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        self.published: list[Message] = []

    async def publish(self, topic: str, payload: Mapping[str, Any]) -> Message:
        message = Message(topic=topic, payload=payload)
        self.published.append(message)
        for handler in self._handlers[topic]:
            await handler(message)
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._handlers[topic].append(handler)


class InMemoryCustomerRepository:
    def __init__(self, customers: Sequence[Customer] = ()) -> None:
        self._items = {c.id: c for c in customers}

    async def get(self, customer_id: str) -> Customer | None:
        return self._items.get(customer_id)


class InMemoryEventRepository:
    def __init__(self, events: Sequence[Event] = ()) -> None:
        self._items = {e.id: e for e in events}

    async def get(self, event_id: str) -> Event | None:
        return self._items.get(event_id)

    async def find(
        self,
        *,
        city: str | None = None,
        category: EventCategory | None = None,
        limit: int = 20,
    ) -> list[Event]:
        matches = [
            e
            for e in self._items.values()
            if (city is None or e.city == city) and (category is None or e.category == category)
        ]
        return matches[:limit]


class InMemorySessionRepository:
    def __init__(self, sessions: Sequence[Session] = ()) -> None:
        self._items = {s.id: s for s in sessions}

    async def get(self, session_id: str) -> Session | None:
        return self._items.get(session_id)

    async def list_for_event(
        self,
        event_id: str,
        *,
        starts_after: datetime | None = None,
        only_available: bool = False,
    ) -> list[Session]:
        return sorted(
            (
                s
                for s in self._items.values()
                if s.event_id == event_id
                and (starts_after is None or s.starts_at > starts_after)
                and (not only_available or s.available > 0)
            ),
            key=lambda s: s.starts_at,
        )

    async def try_reserve(self, session_id: str, quantity: int) -> bool:
        session = self._items.get(session_id)
        if session is None or session.available < quantity:
            return False
        self._items[session_id] = replace(session, available=session.available - quantity)
        return True

    async def release(self, session_id: str, quantity: int) -> None:
        session = self._items[session_id]
        self._items[session_id] = replace(session, available=session.available + quantity)


class InMemoryOrderRepository:
    def __init__(self, orders: Sequence[Order] = ()) -> None:
        self._items = {o.id: o for o in orders}
        self._by_key: dict[str, str] = {}

    async def get(self, order_id: str, *, for_update: bool = False) -> Order | None:
        return self._items.get(order_id)

    async def get_by_idempotency_key(self, key: str) -> Order | None:
        order_id = self._by_key.get(key)
        return self._items[order_id] if order_id else None

    async def add(self, order: Order, *, idempotency_key: str | None = None) -> None:
        if order.id in self._items or (idempotency_key and idempotency_key in self._by_key):
            raise ConflictError("Pedido duplicado.")
        self._items[order.id] = order
        if idempotency_key:
            self._by_key[idempotency_key] = order.id

    async def update_status(self, order_id: str, status: OrderStatus) -> None:
        self._items[order_id] = replace(self._items[order_id], status=status)

    async def list_for_customer(self, customer_id: str, *, limit: int = 20) -> list[Order]:
        orders = [o for o in self._items.values() if o.customer_id == customer_id]
        return sorted(orders, key=lambda o: o.created_at, reverse=True)[:limit]


class InMemoryRefundRepository:
    def __init__(self, refunds: Sequence[Refund] = ()) -> None:
        self.items = list(refunds)

    async def add(self, refund: Refund) -> None:
        open_statuses = (RefundStatus.REQUESTED, RefundStatus.APPROVED)
        if refund.status in open_statuses and any(
            r.order_id == refund.order_id and r.status in open_statuses for r in self.items
        ):
            raise ConflictError(f"El pedido {refund.order_id} ya tiene un reembolso abierto.")
        self.items.append(refund)


class InMemoryUnitOfWork:
    """Sin rollback real: los cambios se aplican al momento. Basta para tests de casos de uso."""

    def __init__(
        self,
        *,
        customers: Sequence[Customer] = (),
        events: Sequence[Event] = (),
        sessions: Sequence[Session] = (),
        orders: Sequence[Order] = (),
        refunds: Sequence[Refund] = (),
    ) -> None:
        self.customers = InMemoryCustomerRepository(customers)
        self.events = InMemoryEventRepository(events)
        self.sessions = InMemorySessionRepository(sessions)
        self.orders = InMemoryOrderRepository(orders)
        self.refunds = InMemoryRefundRepository(refunds)
        self.commits = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
