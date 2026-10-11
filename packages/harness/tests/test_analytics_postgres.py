"""Defensa en profundidad contra PostgreSQL real: aunque el parser fallara, la BD no deja escribir,
leer PII ni bloquearse.

    docker compose up -d
    uv run alembic -c packages/headache_core/alembic.ini upgrade head
    uv run pytest -m integration packages/harness
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from harness import check_sql
from harness.rules.analytics import ANALYTICS_POLICY, ANALYTICS_TABLES
from headache_core.adapters.postgres import create_engine
from headache_core.adapters.postgres.readonly_sql import PostgresReadOnlySql
from headache_core.config import get_settings
from headache_core.ports.sql import SqlExecutionError

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(get_settings().postgres, application_name="integration-tests")
    yield engine
    await engine.dispose()


async def test_policy_mirrors_the_analytics_views(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'analytics'"
            )
        )
        columns: dict[str, set[str]] = {}
        for table, column in rows:
            columns.setdefault(table, set()).add(column)

    assert columns == {table: set(cols) for table, cols in ANALYTICS_TABLES.items()}


async def test_validated_query_runs_with_the_readonly_role(engine: AsyncEngine) -> None:
    runner = PostgresReadOnlySql(engine)
    safe = check_sql("select status, count(*) as n from orders group by status", ANALYTICS_POLICY)
    assert safe.sql is not None

    await runner.explain(safe.sql)
    result = await runner.run(safe.sql)

    assert result.columns == ("status", "n")


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("select email from public.customers", "permission denied"),
        ("select reason from public.refunds", "permission denied"),
        # Los permisos se comprueban antes que la transacción READ ONLY: dos capas independientes.
        ("delete from orders", "permission denied"),
        ("create table stolen (id int)", "read-only transaction"),
        ("select pg_sleep(2)", "statement timeout"),
        ("select 1; select 2", "multiple commands"),
    ],
)
async def test_database_blocks_what_the_parser_would_reject(
    engine: AsyncEngine, sql: str, error: str
) -> None:
    runner = PostgresReadOnlySql(engine, statement_timeout_ms=300)

    with pytest.raises(SqlExecutionError) as caught:
        await runner.run(sql)

    assert error in caught.value.message


async def test_row_cap_marks_truncation(engine: AsyncEngine) -> None:
    runner = PostgresReadOnlySql(engine, max_rows=2)

    result = await runner.run("select generate_series(1, 5) as n")

    assert result.rows == ((1,), (2,)) and result.truncated
