"""Contratos HTTP (DTOs). Son también el esquema que verán los agentes al usar la API como tool."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from headache_core.domain import (
    MAX_TICKETS_PER_ORDER,
    EventCategory,
    OrderStatus,
    RefundStatus,
)

ID_PATTERN = r"^[a-z]{3}_[A-Za-z0-9]{1,40}$"
ResourceId = Annotated[str, Field(pattern=ID_PATTERN, examples=["ses_0000001"])]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MoneyOut(_Out):
    amount: Decimal
    currency: str


class EventOut(_Out):
    id: str
    title: str
    category: EventCategory
    city: str
    venue_id: str
    description: str
    price: MoneyOut
    refundable: bool
    refund_window_hours: int


class SessionOut(_Out):
    id: str
    event_id: str
    starts_at: datetime
    capacity: int
    available: int


class OrderOut(_Out):
    id: str
    customer_id: str
    session_id: str
    quantity: int
    total: MoneyOut
    status: OrderStatus
    created_at: datetime


class PurchaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: ResourceId
    session_id: ResourceId
    quantity: int = Field(ge=1, le=MAX_TICKETS_PER_ORDER)


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class RefundOut(BaseModel):
    refund_id: str
    order_id: str
    status: RefundStatus
    approved: bool
    amount: MoneyOut
    decision_reason: str
    requested_at: datetime


class ErrorOut(BaseModel):
    detail: str
