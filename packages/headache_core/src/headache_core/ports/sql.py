"""Puerto de consultas SQL de solo lectura (analítica de operaciones, fase 8.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    # Había más filas que el máximo del runner.
    truncated: bool = False


class SqlExecutionError(Exception):
    """La base de datos rechazó la consulta. `message` es seguro para devolverlo al modelo."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReadOnlySqlPort(Protocol):
    async def explain(self, sql: str) -> None:
        """Planifica sin ejecutar: valida tablas, columnas y tipos sin coste de lectura."""
        ...

    async def run(self, sql: str) -> QueryResult: ...
