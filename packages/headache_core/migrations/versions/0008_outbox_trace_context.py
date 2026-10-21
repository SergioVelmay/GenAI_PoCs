"""Contexto de traza en el outbox (fase 11.3).

El relay publica después y en otro proceso: sin el `traceparent` guardado con el evento, la
traza de un caso se corta en el outbox. Columna con valor por defecto: las filas anteriores
quedan con `{}` (empiezan traza nueva) y no hay que reescribir la tabla.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "headers",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "headers")
