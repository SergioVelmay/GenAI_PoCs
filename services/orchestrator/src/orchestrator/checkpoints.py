"""Checkpointer de LangGraph en PostgreSQL: los casos pausados sobreviven a reinicios y réplicas.

Equivalentes gestionados: Aurora PostgreSQL / DynamoDB (`langgraph-checkpoint-aws`) en AWS y
Azure Database for PostgreSQL / Cosmos DB en Azure. LangGraph crea y migra sus propias tablas
(`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) con `setup()`.
Van en un esquema propio (`langgraph`): son de la librería, no de Alembic, y en `public`
`alembic check` las vería como tablas sobrantes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from headache_core.config import PostgresSettings

SCHEMA = "langgraph"


@asynccontextmanager
async def postgres_checkpointer(settings: PostgresSettings) -> AsyncIterator[AsyncPostgresSaver]:
    """psycopg 3 asíncrono en Windows necesita el bucle Selector (no el Proactor)."""
    dsn = settings.dsn(driver="postgresql")
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    conninfo = f"{dsn}?options=-csearch_path%3D{SCHEMA}"
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
        yield saver
