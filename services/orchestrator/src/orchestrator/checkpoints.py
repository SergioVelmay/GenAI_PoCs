"""Estado de los motores en PostgreSQL: los casos pausados sobreviven a reinicios y réplicas.

- LangGraph: checkpointer `AsyncPostgresSaver`. Crea y migra sus tablas (`checkpoints`,
  `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) con `setup()`.
- ADK (9.G2): `DatabaseSessionService`. Crea sus tablas (`sessions`, `events`, `app_states`,
  `user_states`, `adk_internal_metadata`) al primer uso con `create_all`.

Cada librería en su esquema (`langgraph`, `adk`): son suyas, no de Alembic, y en `public`
`alembic check` las vería como tablas sobrantes (y `sessions` chocaría con nombres genéricos).

Equivalentes gestionados: Aurora PostgreSQL / DynamoDB (`langgraph-checkpoint-aws`) en AWS,
Azure Database for PostgreSQL / Cosmos DB en Azure, y Cloud SQL / AlloyDB o las **Sessions de
Agent Platform** (`VertexAiSessionService`, sin tablas propias) en GCP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from google.adk.sessions import DatabaseSessionService
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from genaipocs_core.config import PostgresSettings

SCHEMA = "langgraph"
ADK_SCHEMA = "adk"


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


@asynccontextmanager
async def postgres_session_service(
    settings: PostgresSettings, *, application_name: str = "orchestrator-adk"
) -> AsyncIterator[DatabaseSessionService]:
    """Sesiones de ADK en PostgreSQL (asyncpg), con `search_path` en el esquema `adk`."""
    engine = create_async_engine(
        settings.dsn(),
        pool_pre_ping=True,
        connect_args={
            "server_settings": {"search_path": ADK_SCHEMA, "application_name": application_name}
        },
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {ADK_SCHEMA}"))
        # Con `db_engine` el servicio no es dueño del engine: lo cierra este gestor.
        yield DatabaseSessionService(db_engine=engine)
    finally:
        await engine.dispose()
