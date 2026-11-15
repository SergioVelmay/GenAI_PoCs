"""Almacén vectorial de conocimiento (pgvector) y registro de ingesta.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fijado en la migración: cambiar de dimensión exige una nueva migración y reindexar.
EMBEDDING_DIMENSIONS = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_chunks",
        sa.Column("index_name", sa.Text()),
        sa.Column("id", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("index_name", "id", name="pk_knowledge_chunks"),
    )
    # Tabla nueva y vacía: no hace falta CONCURRENTLY.
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_knowledge_chunks_attributes",
        "knowledge_chunks",
        ["attributes"],
        postgresql_using="gin",
        postgresql_ops={"attributes": "jsonb_path_ops"},
    )
    op.create_table(
        "ingestion_ledger",
        sa.Column("index_name", sa.Text()),
        sa.Column("document_id", sa.Text()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("chunk_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("index_name", "document_id", name="pk_ingestion_ledger"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_ledger")
    op.drop_index("ix_knowledge_chunks_attributes", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
