"""Entidades del dominio Headache. Python puro: sin frameworks, sin nube, sin base de datos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class EventCategory(StrEnum):
    CANDLELIT = "candlelit"
    EXHIBITION = "exhibition"
    THEATRE = "theatre"
    FOOD_DRINK = "food_drink"
    TOURS = "tours"
    NIGHTLIFE = "nightlife"
    SPORTS = "sports"


class OrderStatus(StrEnum):
    PAID = "paid"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class DocumentType(StrEnum):
    POLICY = "policy"
    FAQ = "faq"
    VENUE_GUIDE = "venue_guide"


class SupportIntent(StrEnum):
    REFUND_REQUEST = "refund_request"
    DATE_CHANGE = "date_change"
    EVENT_INFO = "event_info"
    LOST_TICKETS = "lost_tickets"
    COMPLAINT = "complaint"
    PARTNERSHIP = "partnership"


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("El importe no puede ser negativo.")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"Moneda ISO 4217 no válida: {self.currency!r}")

    def __mul__(self, factor: int) -> Money:
        return Money(self.amount * factor, self.currency)


@dataclass(frozen=True, slots=True)
class Venue:
    id: str
    name: str
    city: str
    country: str
    address: str
    capacity: int


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    title: str
    category: EventCategory
    city: str
    venue_id: str
    description: str
    price: Money
    refundable: bool
    refund_window_hours: int


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    event_id: str
    starts_at: datetime
    capacity: int
    available: int

    def __post_init__(self) -> None:
        if not 0 <= self.available <= self.capacity:
            raise ValueError("Las plazas disponibles deben estar entre 0 y la capacidad.")


@dataclass(frozen=True, slots=True)
class Customer:
    id: str
    full_name: str
    email: str
    country: str
    language: str


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    customer_id: str
    session_id: str
    quantity: int
    total: Money
    status: OrderStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Refund:
    id: str
    order_id: str
    amount: Money
    reason: str
    status: RefundStatus
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class SupportEmail:
    id: str
    customer_id: str
    language: str
    subject: str
    body: str
    received_at: datetime
    order_id: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    id: str
    title: str
    doc_type: DocumentType
    language: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)
