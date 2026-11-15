"""Índices de rendimiento (B-tree compuestos, parciales, de expresión y FK).

Se crean con CREATE INDEX CONCURRENTLY: no bloquean las escrituras sobre tablas grandes en
producción. A cambio, no pueden ejecutarse dentro de una transacción (autocommit_block).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


@dataclass(frozen=True)
class _Index:
    name: str
    table: str
    columns: list[str | sa.TextClause]
    unique: bool = False
    where: str | None = None


INDEXES = [
    # Historial de un cliente: igualdad en customer_id + orden por created_at (sirve ASC y DESC).
    _Index("ix_orders_customer_id_created_at", "orders", ["customer_id", "created_at"]),
    # PostgreSQL NO indexa las FK automáticamente: sin esto, los JOIN por sesión son seq scans.
    _Index("ix_orders_session_id", "orders", ["session_id"]),
    _Index("ix_sessions_event_id_starts_at", "sessions", ["event_id", "starts_at"]),
    # Parcial: solo sesiones con plazas. Más pequeño y exactamente lo que busca el catálogo.
    _Index(
        "ix_sessions_starts_at_with_availability",
        "sessions",
        ["starts_at"],
        where="available > 0",
    ),
    _Index("ix_events_city_category", "events", ["city", "category"]),
    # De expresión: búsqueda por email sin distinguir mayúsculas + unicidad real.
    _Index("uq_customers_lower_email", "customers", [sa.text("lower(email)")], unique=True),
    # Parcial: la cola de reembolsos pendientes es diminuta frente al histórico.
    _Index(
        "ix_refunds_requested_at_pending",
        "refunds",
        ["requested_at"],
        where="status = 'requested'",
    ),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for ix in INDEXES:
            op.create_index(
                ix.name,
                ix.table,
                ix.columns,
                unique=ix.unique,
                postgresql_where=sa.text(ix.where) if ix.where else None,
                postgresql_concurrently=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for ix in reversed(INDEXES):
            op.drop_index(ix.name, table_name=ix.table, postgresql_concurrently=True)
