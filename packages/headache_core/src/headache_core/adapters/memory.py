"""Adaptadores en memoria: implementan los puertos sin red ni coste. Base de todos los tests."""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self

from headache_core.application.telemetry import get_telemetry
from headache_core.domain import (
    CaseStatus,
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
    LedgerEntry,
    LLMResponse,
    LLMStreamEvent,
    Message,
    MessageBusPort,
    MessageHandler,
    MetadataValue,
    PermanentMessageError,
    RateLimitDecision,
    RateLimitRequest,
    ResponseSchema,
    SearchResult,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolConfig,
    VectorRecord,
)
from headache_core.ports.approvals import (
    OPEN_STATUSES,
    ApprovalConflictError,
    ApprovalRequest,
    ApprovalStatus,
)
from headache_core.ports.cases import CaseRecord
from headache_core.ports.intake import CaseSubmission
from headache_core.ports.memory import CheckpointConflictError, ThreadState
from headache_core.ports.outbox import OutboxEvent, PendingEvent
from headache_core.ports.semantic_cache import CacheCandidate, CacheEntry


class FakeLLM:
    """Devuelve respuestas guionizadas (o hace eco) y registra cada llamada.

    Un elemento del guion que sea una excepción se lanza en vez de responder; una lista de
    `ToolCall` simula un turno en el que el modelo pide tools.
    """

    def __init__(
        self,
        responses: Sequence[str | list[ToolCall] | Exception] = (),
        model: str = "fake-llm",
    ) -> None:
        self._responses = list(responses)
        self._model = model
        self.calls: list[list[ChatMessage]] = []
        self.schemas: list[ResponseSchema | None] = []
        self.tools: list[ToolConfig | None] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.schemas.append(response_schema)
        self.tools.append(tools)
        scripted = self._responses.pop(0) if self._responses else messages[-1].content
        if isinstance(scripted, Exception):
            raise scripted
        tool_calls = tuple(scripted) if isinstance(scripted, list) else ()
        content = scripted if isinstance(scripted, str) else ""
        usage = TokenUsage(
            input_tokens=sum(len(m.content.split()) for m in messages),
            output_tokens=len(content.split()) + sum(len(c.arguments.split()) for c in tool_calls),
        )
        return LLMResponse(
            content=content,
            model=self._model,
            usage=usage,
            provider="fake",
            finish_reason="tool_calls" if tool_calls else "stop",
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """El mismo guion, palabra a palabra (un error guionizado salta antes del primer trozo)."""
        response = await self.complete(messages, temperature=temperature, max_tokens=max_tokens)
        for word in re.findall(r"\S+\s*", response.content):
            yield TextDelta(word)
        yield response


class RecordingTracer:
    """Tracer en memoria: guarda cada *generation* para inspeccionarla en tests."""

    def __init__(self) -> None:
        self.records: list[TraceRecord] = []

    def start_generation(
        self,
        *,
        name: str,
        messages: Sequence[ChatMessage],
        metadata: Mapping[str, str],
    ) -> TraceRecord:
        record = TraceRecord(name=name, messages=list(messages), metadata=dict(metadata))
        self.records.append(record)
        return record


@dataclass(slots=True)
class TraceRecord:
    name: str
    messages: list[ChatMessage]
    metadata: dict[str, str]
    response: LLMResponse | None = None
    cost_usd: Decimal | None = None
    error: BaseException | None = None
    first_token_seen: bool = False

    def first_token(self) -> None:
        self.first_token_seen = True

    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None:
        self.response = response
        self.cost_usd = cost_usd

    def fail(self, error: BaseException) -> None:
        self.error = error


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

    def __len__(self) -> int:
        return len(self._records)

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
    """Entrega síncrona con la misma semántica que RabbitMQ/SQS/Service Bus.

    Un fallo transitorio se reintenta hasta `max_deliveries` entregas; un `PermanentMessageError`
    o el agotamiento de reintentos mandan el mensaje a `dead_letters` (la DLQ).
    """

    def __init__(self, *, max_deliveries: int = 3) -> None:
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        self._max_deliveries = max_deliveries
        self.published: list[Message] = []
        self.dead_letters: list[Message] = []

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Message:
        message = Message(topic=topic, payload=payload, headers=dict(headers or {}))
        self.published.append(message)
        for handler in self._handlers[topic]:
            await self._deliver(handler, message)
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._handlers[topic].append(handler)

    async def close(self) -> None:
        return None

    async def _deliver(self, handler: MessageHandler, message: Message) -> None:
        for attempt in range(1, self._max_deliveries + 1):
            try:
                await handler(replace(message, delivery_count=attempt))
            except PermanentMessageError:
                break
            except Exception:
                continue
            else:
                return
        self.dead_letters.append(message)


class InMemoryIngestionLedger:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], LedgerEntry] = {}

    async def get(self, index_name: str, document_id: str) -> LedgerEntry | None:
        return self._entries.get((index_name, document_id))

    async def save(self, entry: LedgerEntry) -> None:
        self._entries[(entry.index_name, entry.document_id)] = entry

    async def delete(self, index_name: str, document_id: str) -> None:
        self._entries.pop((index_name, document_id), None)

    async def forget_index(self, index_name: str) -> int:
        keys = [key for key in self._entries if key[0] == index_name]
        for key in keys:
            del self._entries[key]
        return len(keys)


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


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self.threads: dict[str, ThreadState] = {}

    async def load(self, thread_id: str) -> ThreadState | None:
        return self.threads.get(thread_id)

    async def save(self, state: ThreadState, *, expected_version: int) -> ThreadState:
        current = self.threads.get(state.thread_id)
        if (current.version if current else 0) != expected_version:
            raise CheckpointConflictError(f"El hilo {state.thread_id} ha cambiado.")
        saved = replace(state, version=expected_version + 1)
        self.threads[state.thread_id] = saved
        return saved


class InMemoryPreferenceStore:
    def __init__(self, initial: Mapping[str, Mapping[str, str]] | None = None) -> None:
        self.values: dict[str, dict[str, str]] = {
            customer: dict(prefs) for customer, prefs in (initial or {}).items()
        }

    async def get(self, customer_id: str) -> dict[str, str]:
        return dict(self.values.get(customer_id, {}))

    async def put(self, customer_id: str, key: str, value: str) -> None:
        self.values.setdefault(customer_id, {})[key] = value


class InMemoryApprovalStore:
    """Mismas garantías que PostgreSQL: una sola solicitud abierta por acción y transiciones
    atómicas (sin `await` entre leer y escribir, nadie se cuela en el bucle de eventos)."""

    def __init__(self) -> None:
        self.items: dict[str, ApprovalRequest] = {}

    async def add(self, request: ApprovalRequest) -> None:
        if request.id in self.items or await self.find_open(request.fingerprint):
            raise ApprovalConflictError(f"Ya hay una solicitud abierta para {request.tool}.")
        self.items[request.id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.items.get(approval_id)

    async def find_open(self, fingerprint: str) -> ApprovalRequest | None:
        return next(
            (
                r
                for r in self.items.values()
                if r.fingerprint == fingerprint and r.status in OPEN_STATUSES
            ),
            None,
        )

    async def list_requests(
        self,
        *,
        status: ApprovalStatus | None = None,
        customer_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRequest]:
        found = [
            r
            for r in self.items.values()
            if (status is None or r.status is status)
            and (customer_id is None or r.customer_id == customer_id)
        ]
        return sorted(found, key=lambda r: r.created_at)[:limit]

    async def transition(
        self, approval_id: str, expected: ApprovalStatus, updated: ApprovalRequest
    ) -> bool:
        current = self.items.get(approval_id)
        if current is None or current.status is not expected:
            return False
        self.items[approval_id] = updated
        return True


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class InMemoryOutbox:
    """Outbox en memoria. Con `deliver_to`, cada evento se publica nada más guardarse (un relay
    instantáneo): los tests síncronos del flujo no tienen que bombear el relay a mano."""

    def __init__(
        self,
        *,
        deliver_to: MessageBusPort | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._deliver_to = deliver_to
        self._clock = clock
        self._next_id = 1
        # id -> (evento, intentos, disponible a partir de, publicado)
        self.events: dict[int, tuple[OutboxEvent, int, float, bool]] = {}
        self.errors: dict[int, str] = {}

    async def append(self, events: Sequence[OutboxEvent]) -> None:
        current = get_telemetry().inject()
        for event in events:
            if not event.headers:
                event = replace(event, headers=current)
            event_id = self._next_id
            self._next_id += 1
            self.events[event_id] = (event, 0, 0.0, False)
            if self._deliver_to is not None:
                self.events[event_id] = (event, 1, 0.0, True)
                await self._deliver_to.publish(event.topic, event.payload, headers=event.headers)

    @property
    def published(self) -> list[OutboxEvent]:
        return [event for event, _, _, done in self.events.values() if done]

    async def claim(
        self, *, limit: int, lease_seconds: float, topic_prefix: str = ""
    ) -> list[PendingEvent]:
        now = self._clock()
        claimed = []
        for event_id, (event, attempts, available_at, done) in sorted(self.events.items()):
            if done or available_at > now or not event.topic.startswith(topic_prefix):
                continue
            self.events[event_id] = (event, attempts + 1, now + lease_seconds, False)
            claimed.append(
                PendingEvent(event_id, event.topic, event.payload, attempts + 1, event.headers)
            )
            if len(claimed) == limit:
                break
        return claimed

    async def mark_published(self, ids: Sequence[int]) -> None:
        for event_id in ids:
            event, attempts, available_at, _ = self.events[event_id]
            self.events[event_id] = (event, attempts, available_at, True)

    async def release(self, event_id: int, *, error: str, retry_in_seconds: float) -> None:
        event, attempts, _, _ = self.events[event_id]
        self.events[event_id] = (event, attempts, self._clock() + retry_in_seconds, False)
        self.errors[event_id] = error

    async def backlog(self, *, topic_prefix: str = "") -> int:
        return sum(
            not done and event.topic.startswith(topic_prefix)
            for event, _, _, done in self.events.values()
        )


class InMemoryCaseStore:
    """Mismas garantías que PostgreSQL: alta única por caso, transiciones compare-and-set y
    eventos al outbox solo si el cambio se aplica."""

    def __init__(self, outbox: InMemoryOutbox | None = None) -> None:
        self.items: dict[str, CaseRecord] = {}
        self.outbox = outbox or InMemoryOutbox()

    async def get(self, case_id: str) -> CaseRecord | None:
        return self.items.get(case_id)

    async def create(self, record: CaseRecord, *, events: Sequence[OutboxEvent] = ()) -> bool:
        if record.case_id in self.items:
            return False
        self.items[record.case_id] = record
        await self.outbox.append(events)
        return True

    async def transition(
        self,
        case_id: str,
        expected: CaseStatus,
        updated: CaseRecord,
        *,
        events: Sequence[OutboxEvent] = (),
    ) -> bool:
        current = self.items.get(case_id)
        if current is None or current.status is not expected:
            return False
        self.items[case_id] = updated
        await self.outbox.append(events)
        return True

    async def list_cases(
        self, *, status: CaseStatus | None = None, limit: int = 50
    ) -> list[CaseRecord]:
        found = [r for r in self.items.values() if status is None or r.status is status]
        return sorted(found, key=lambda r: r.created_at)[:limit]


class InMemoryRateLimiter:
    """GCRA (*Generic Cell Rate Algorithm*) en memoria: misma semántica que el script Lua de
    `RedisRateLimiter`, pero por proceso. Sirve para tests y como plan B si Redis cae.

    Por clave solo se guarda el TAT (*theoretical arrival time*): el instante en que el cubo
    estaría lleno otra vez. Coste n con límite L por periodo P (intervalo T = P / L):
    `nuevo_tat = max(tat, ahora) + n·T`; se concede si `nuevo_tat - P <= ahora`.
    """

    MAX_KEYS = 100_000

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tat: dict[str, float] = {}

    async def acquire(self, requests: Sequence[RateLimitRequest]) -> RateLimitDecision:
        now = self._clock()
        new_tats: dict[str, float] = {}
        retry_after, remaining, limited_by = 0.0, math.inf, None
        for request in requests:
            rule = request.rule
            interval = rule.period_seconds / rule.limit
            cost = min(request.cost, rule.limit)
            new_tat = max(self._tat.get(request.key, now), now) + cost * interval
            allow_at = new_tat - rule.period_seconds
            if allow_at > now:
                limited_by = limited_by or rule.name
                retry_after = max(retry_after, allow_at - now)
            else:
                new_tats[request.key] = new_tat
                # El épsilon evita que (P - T) / T = 18,999... cuente una unidad de menos.
                remaining = min(remaining, math.floor((now - allow_at) / interval + 1e-9))
        if limited_by is not None:
            return RateLimitDecision(False, retry_after=retry_after, limited_by=limited_by)
        self._tat.update(new_tats)
        if len(self._tat) > self.MAX_KEYS:
            # Una clave con TAT pasado tiene el cubo lleno: equivale a no existir (como el TTL).
            self._tat = {key: tat for key, tat in self._tat.items() if tat > now}
        return RateLimitDecision(True, remaining=0 if math.isinf(remaining) else int(remaining))


class InMemorySemanticCache:
    """Caché semántica por fuerza bruta (coseno) con caducidad. Tests y una sola réplica."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[CacheEntry, float]] = {}

    def __len__(self) -> int:
        return sum(expires > self._clock() for _, expires in self._entries.values())

    async def nearest(
        self, scope: str, embedding: Sequence[float], *, limit: int
    ) -> list[CacheCandidate]:
        now = self._clock()
        self._entries = {k: v for k, v in self._entries.items() if v[1] > now}
        candidates = [
            CacheCandidate(entry, _cosine(embedding, entry.embedding))
            for entry, _ in self._entries.values()
            if entry.scope == scope
        ]
        candidates.sort(key=lambda candidate: candidate.similarity, reverse=True)
        return candidates[:limit]

    async def put(self, entry: CacheEntry, *, ttl_seconds: int) -> None:
        self._entries[entry.id] = (entry, self._clock() + ttl_seconds)


class InMemoryCaseIntakeStore:
    """Altas idempotentes en memoria; comparte el outbox con `InMemoryCaseStore` (como si
    fueran tablas de la misma base de datos)."""

    def __init__(self, outbox: InMemoryOutbox) -> None:
        self.outbox = outbox
        self.submissions: dict[tuple[str, str], CaseSubmission] = {}

    async def submit(
        self, submission: CaseSubmission, event: OutboxEvent
    ) -> tuple[CaseSubmission, bool]:
        key = (submission.customer_id, submission.idempotency_key)
        existing = self.submissions.get(key)
        if existing is not None:
            if existing.request_hash != submission.request_hash:
                raise ConflictError("Idempotency-Key reutilizada con otro cuerpo.")
            return existing, False
        self.submissions[key] = submission
        await self.outbox.append([event])
        return submission, True

    async def get_submission(self, case_id: str) -> CaseSubmission | None:
        return next((s for s in self.submissions.values() if s.case_id == case_id), None)
