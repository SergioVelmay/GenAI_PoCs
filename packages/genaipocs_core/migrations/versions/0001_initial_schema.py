"""Esquema inicial de ticketing: tablas, claves y restricciones (sin índices secundarios).

Revision ID: 0001
Revises:
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)


def _tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _enum_check(column: str, values: Sequence[str], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def upgrade() -> None:
    op.create_table(
        "venues",
        sa.Column("id", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.CheckConstraint("capacity > 0", name="ck_venues_capacity_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_venues"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("venue_id", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("refundable", sa.Boolean(), nullable=False),
        sa.Column("refund_window_hours", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], name="fk_events_venue_id_venues"),
        _enum_check(
            "category",
            ("candlelit", "exhibition", "theatre", "food_drink", "tours", "nightlife", "sports"),
            "ck_events_event_category",
        ),
        sa.CheckConstraint("price_amount >= 0", name="ck_events_price_non_negative"),
        sa.CheckConstraint("refund_window_hours >= 0", name="ck_events_refund_window_non_negative"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text()),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("starts_at", _tstz(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_sessions_event_id_events"),
        sa.CheckConstraint(
            "available BETWEEN 0 AND capacity", name="ck_sessions_available_within_capacity"
        ),
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.Text()),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Text()),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("total_amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", _tstz(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_orders_customer_id_customers"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name="fk_orders_session_id_sessions"
        ),
        _enum_check("status", ("paid", "refunded", "cancelled"), "ck_orders_order_status"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 10", name="ck_orders_quantity_range"),
        sa.CheckConstraint("total_amount >= 0", name="ck_orders_total_non_negative"),
    )
    op.create_index(
        "uq_orders_idempotency_key",
        "orders",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "refunds",
        sa.Column("id", sa.Text()),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_at", _tstz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_refunds"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_refunds_order_id_orders"),
        _enum_check("status", ("requested", "approved", "rejected"), "ck_refunds_refund_status"),
        sa.CheckConstraint("amount >= 0", name="ck_refunds_amount_non_negative"),
    )
    op.create_index(
        "uq_refunds_order_id_open",
        "refunds",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'approved')"),
    )


def downgrade() -> None:
    for table in ("refunds", "orders", "customers", "sessions", "events", "venues"):
        op.drop_table(table)
