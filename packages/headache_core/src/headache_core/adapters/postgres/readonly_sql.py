"""Runner de SQL de solo lectura: la última línea de defensa si el validador del harness falla.

Cada consulta corre en una transacción READ ONLY, con timeout propio, con el rol sin privilegios
de la migración 0004 y con `search_path` limitado a las vistas `analytics`. Siempre se deshace.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from headache_core.ports.sql import QueryResult, SqlExecutionError

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class PostgresReadOnlySql:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        role: str = "headache_analyst",
        schema: str = "analytics",
        statement_timeout_ms: int = 3_000,
        max_rows: int = 200,
    ) -> None:
        for identifier in (role, schema):
            if not _IDENTIFIER.match(identifier):
                raise ValueError(f"Identificador no válido: {identifier!r}")
        self._engine = engine
        self._role = role
        self._schema = schema
        self._timeout_ms = int(statement_timeout_ms)
        self._max_rows = max_rows

    @asynccontextmanager
    async def _readonly(self) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as conn:
            try:
                await conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                await conn.exec_driver_sql(f"SET LOCAL statement_timeout = {self._timeout_ms}")
                await conn.exec_driver_sql(f"SET LOCAL ROLE {self._role}")
                await conn.exec_driver_sql(f"SET LOCAL search_path = {self._schema}")
                yield conn
            except DBAPIError as exc:
                raise SqlExecutionError(_safe_message(exc)) from exc
            finally:
                await conn.rollback()

    async def explain(self, sql: str) -> None:
        async with self._readonly() as conn:
            await conn.exec_driver_sql(f"EXPLAIN {sql}")

    async def run(self, sql: str) -> QueryResult:
        async with self._readonly() as conn:
            result = await conn.exec_driver_sql(sql)
            rows = result.fetchmany(self._max_rows + 1)
            return QueryResult(
                columns=tuple(result.keys()),
                rows=tuple(tuple(row) for row in rows[: self._max_rows]),
                truncated=len(rows) > self._max_rows,
            )


def _safe_message(exc: DBAPIError) -> str:
    # Primera línea del error del servidor, sin la sentencia ni los parámetros de SQLAlchemy.
    original = (str(exc.orig) if exc.orig is not None else str(exc)).strip()
    if original.startswith("<class"):  # Envoltorio de asyncpg: "<class '...Error'>: mensaje".
        original = original.split(": ", 1)[-1]
    return original.splitlines()[0][:300] if original else "Error de SQL."
