"""Transactional outbox y altas idempotentes de casos (fase 10.5).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", _tstz(), server_default=sa.func.now(), nullable=False),
        sa.Column("available_at", _tstz(), server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", _tstz(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    # Índice parcial: solo las filas pendientes. Lo publicado no pesa en la búsqueda del relay
    # (y se puede purgar por lotes sin tocar el índice caliente).
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["available_at", "id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "case_submissions",
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("email_id", sa.Text(), nullable=False),
        sa.Column("created_at", _tstz(), nullable=False),
        sa.PrimaryKeyConstraint("customer_id", "idempotency_key", name="pk_case_submissions"),
    )
    op.create_index("uq_case_submissions_case_id", "case_submissions", ["case_id"], unique=True)


def downgrade() -> None:
    op.drop_table("case_submissions")
    op.drop_table("outbox_events")
