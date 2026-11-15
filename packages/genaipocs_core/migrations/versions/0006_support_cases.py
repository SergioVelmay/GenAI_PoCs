"""Estado de los casos de soporte del flujo asíncrono multi-agente (fase 9.6).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("received", "triaged", "pending_approval", "resolved", "escalated")
SPECIALISTS = ("refunds", "creative", "knowledge")


def _tstz() -> sa.DateTime:
    return sa.DateTime(timezone=True)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "support_cases",
        sa.Column("id", sa.Text()),
        sa.Column("email_id", sa.Text(), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("specialist", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=True),
        sa.Column("outcome", postgresql.JSONB(), nullable=True),
        sa.Column("approval_id", sa.Text(), nullable=True),
        sa.Column("guardrails", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_at", _tstz(), nullable=False),
        sa.Column("updated_at", _tstz(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_support_cases"),
        sa.CheckConstraint(f"status IN ({_in(STATUSES)})", name="ck_support_cases_case_status"),
        sa.CheckConstraint(
            f"specialist IN ({_in(SPECIALISTS)})", name="ck_support_cases_case_specialist"
        ),
    )
    op.create_index("uq_support_cases_email_id", "support_cases", ["email_id"], unique=True)
    op.create_index("ix_support_cases_status_created_at", "support_cases", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("support_cases")
