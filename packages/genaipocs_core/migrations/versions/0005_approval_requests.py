"""Solicitudes de aprobación humana para acciones irreversibles de los agentes (fase 8.5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "approved", "rejected", "expired", "executed", "failed")


def _tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    allowed = ", ".join(f"'{s}'" for s in STATUSES)
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Text()),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", _tstz(), nullable=False),
        sa.Column("expires_at", _tstz(), nullable=False),
        sa.Column("decided_at", _tstz(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approval_requests"),
        sa.CheckConstraint(f"status IN ({allowed})", name="ck_approval_requests_approval_status"),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_approval_requests_expires_after_creation"
        ),
    )
    op.create_index(
        "uq_approval_requests_open",
        "approval_requests",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'approved')"),
    )
    op.create_index(
        "ix_approval_requests_status_created_at", "approval_requests", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("approval_requests")
