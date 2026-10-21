"""Modelo relacional (SQLAlchemy 2.0). Es un detalle de persistencia: el dominio no lo conoce.

Los índices secundarios se declaran aquí para que `alembic check` detecte desvíos, pero se
crean en la migración 0002 (así se puede medir el antes y el después).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from headache_core.domain import (
    CaseStatus,
    EventCategory,
    OrderStatus,
    RefundStatus,
    Specialist,
)
from headache_core.ports.approvals import ApprovalStatus

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    # VARCHAR + CHECK en lugar de ENUM nativo: añadir valores no exige ALTER TYPE.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [m.value for m in members],
    )


MONEY = Numeric(12, 2)


class VenueRow(Base):
    __tablename__ = "venues"
    __table_args__ = (CheckConstraint("capacity > 0", name="capacity_positive"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2))
    address: Mapped[str] = mapped_column(Text)
    capacity: Mapped[int] = mapped_column(Integer)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("price_amount >= 0", name="price_non_negative"),
        CheckConstraint("refund_window_hours >= 0", name="refund_window_non_negative"),
        Index("ix_events_city_category", "city", "category"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[EventCategory] = mapped_column(_str_enum(EventCategory, "event_category"))
    city: Mapped[str] = mapped_column(Text)
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id"))
    description: Mapped[str] = mapped_column(Text)
    price_amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    refundable: Mapped[bool]
    refund_window_hours: Mapped[int] = mapped_column(Integer)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # Última línea de defensa contra la sobreventa: la BD rechaza un inventario imposible.
        CheckConstraint("available BETWEEN 0 AND capacity", name="available_within_capacity"),
        Index("ix_sessions_event_id_starts_at", "event_id", "starts_at"),
        Index(
            "ix_sessions_starts_at_with_availability",
            "starts_at",
            postgresql_where=text("available > 0"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(Integer)
    available: Mapped[int] = mapped_column(Integer)


class CustomerRow(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2))
    language: Mapped[str] = mapped_column(String(5))


# Índice de expresión: sirve a `WHERE lower(email) = ...` y garantiza unicidad sin mayúsculas.
Index("uq_customers_lower_email", func.lower(CustomerRow.email), unique=True)


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity BETWEEN 1 AND 10", name="quantity_range"),
        CheckConstraint("total_amount >= 0", name="total_non_negative"),
        # Índice único parcial: los NULL (pedidos sin clave) no ocupan espacio en el índice.
        Index(
            "uq_orders_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_orders_customer_id_created_at", "customer_id", "created_at"),
        Index("ix_orders_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[OrderStatus] = mapped_column(_str_enum(OrderStatus, "order_status"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(Text)


class RefundRow(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        # Regla de negocio en la BD: como mucho un reembolso abierto o aprobado por pedido.
        Index(
            "uq_refunds_order_id_open",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('requested', 'approved')"),
        ),
        Index(
            "ix_refunds_requested_at_pending",
            "requested_at",
            postgresql_where=text("status = 'requested'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[RefundStatus] = mapped_column(_str_enum(RefundStatus, "refund_status"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# text-embedding-3-small (con `dimensions`) y Titan Embed v2 admiten 1024: misma tabla en ambas.
EMBEDDING_DIMENSIONS = 1024


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        # HNSW: mejor recall/latencia que IVFFlat y no necesita datos previos para construirse.
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # GIN jsonb_path_ops: filtros por metadatos con `attributes @> '{...}'`.
        Index(
            "ix_knowledge_chunks_attributes",
            "attributes",
            postgresql_using="gin",
            postgresql_ops={"attributes": "jsonb_path_ops"},
        ),
    )

    # Varios índices lógicos (p. ej. una estrategia de chunking cada uno) en la misma tabla.
    index_name: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionLedgerRow(Base):
    __tablename__ = "ingestion_ledger"

    index_name: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    pipeline_version: Mapped[str] = mapped_column(Text)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalRow(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        # Una sola solicitud abierta por acción, aunque dos réplicas la pidan a la vez.
        Index(
            "uq_approval_requests_open",
            "fingerprint",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
        ),
        # Cola del revisor: pendientes por antigüedad.
        Index("ix_approval_requests_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[ApprovalStatus] = mapped_column(_str_enum(ApprovalStatus, "approval_status"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)


class SupportCaseRow(Base):
    """Estado de cada caso del flujo asíncrono multi-agente (fase 9.6)."""

    __tablename__ = "support_cases"
    __table_args__ = (
        # Un caso por email: un email repetido en la cola cae en el mismo caso.
        Index("uq_support_cases_email_id", "email_id", unique=True),
        # Cola del revisor y paneles de operaciones: casos por estado y antigüedad.
        Index("ix_support_cases_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email_id: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str] = mapped_column(Text)
    specialist: Mapped[Specialist | None] = mapped_column(_str_enum(Specialist, "case_specialist"))
    status: Mapped[CaseStatus] = mapped_column(_str_enum(CaseStatus, "case_status"))
    envelope: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approval_id: Mapped[str | None] = mapped_column(Text)
    guardrails: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxEventRow(Base):
    """Transactional outbox (fase 10.5): eventos escritos con el cambio que los origina."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        # Solo lo pendiente, en orden: el relay lee siempre del principio de este índice.
        Index(
            "ix_outbox_events_pending",
            "available_at",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    topic: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Disponible para un relay a partir de aquí (reserva vencida o reintento con backoff).
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseSubmissionRow(Base):
    """Altas de casos por la web con `Idempotency-Key` (fase 10.5)."""

    __tablename__ = "case_submissions"
    __table_args__ = (Index("uq_case_submissions_case_id", "case_id", unique=True),)

    customer_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    case_id: Mapped[str] = mapped_column(Text)
    email_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
