"""SQL generado por LLM: se parsea (sqlglot) y se valida como código, nunca como texto.

Capas de defensa (ninguna basta sola):
1. Parser: una sola sentencia, solo SELECT/UNION, sin DML/DDL ni en CTEs, sin INTO ni bloqueos.
2. Allow-list de tablas y columnas (las vistas `analytics`) y de funciones no estándar.
3. Reescritura: tablas cualificadas con el esquema y LIMIT forzado.
4. Base de datos: rol de solo lectura, transacción READ ONLY y timeout (ver `PostgresReadOnlySql`).

Lo que delata un ataque (escrituras, sentencias apiladas, catálogos del sistema, funciones de
administración) es no corregible: se escala en vez de enseñar al modelo a esquivar la regla.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from harness.validation import Violation

# Funciones que sqlglot no tipa (exp.Anonymous) y aun así son seguras en analítica.
SAFE_UNTYPED_FUNCTIONS = frozenset({"date_part", "age", "to_char", "make_date", "make_interval"})
_SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})
_ADMIN_PREFIXES = ("pg_", "lo_", "dblink", "set_config", "current_setting", "query_to_xml")
_WRITES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Copy,
    exp.Into,
)


@dataclass(frozen=True, slots=True)
class SqlPolicy:
    # Tabla -> columna -> tipo: SOLO lo que se expone (lo que no está aquí no existe).
    tables: Mapping[str, Mapping[str, str]]
    schema: str = "analytics"
    max_rows: int = 200
    dialect: str = "postgres"
    untyped_functions: frozenset[str] = SAFE_UNTYPED_FUNCTIONS

    def describe(self) -> str:
        return "\n".join(
            f"- {self.schema}.{table}({', '.join(f'{c} {t}' for c, t in columns.items())})"
            for table, columns in self.tables.items()
        )


@dataclass(frozen=True, slots=True)
class SqlCheck:
    # SQL reescrita y segura para ejecutar; None si hay violaciones.
    sql: str | None
    violations: list[Violation] = field(default_factory=list)


def _attack(code: str, message: str) -> Violation:
    return Violation(code, message, "sql", correctable=False)


def _fix(code: str, message: str) -> Violation:
    return Violation(code, message, "sql")


def check_sql(sql: str, policy: SqlPolicy) -> SqlCheck:
    try:
        statements = [s for s in sqlglot.parse(sql, read=policy.dialect) if s is not None]
    except SqlglotError as exc:
        return SqlCheck(None, [_fix("sql_parse", f"SQL no válido: {_first_line(exc)}")])
    if len(statements) != 1:
        return SqlCheck(None, [_attack("multiple_statements", "Solo se admite una sentencia.")])
    tree = statements[0]
    violations = _structure(tree, policy)
    if violations:
        return SqlCheck(None, violations)

    for table in tree.find_all(exp.Table):
        table.set("db", None)
    try:
        tree = qualify(
            tree,
            schema={name: dict(columns) for name, columns in policy.tables.items()},
            dialect=policy.dialect,
            validate_qualify_columns=True,
        )
    except OptimizeError as exc:
        return SqlCheck(None, [_fix("column_not_allowed", _first_line(exc))])
    except SqlglotError as exc:
        return SqlCheck(None, [_fix("sql_parse", _first_line(exc))])
    ctes = _cte_names(tree)
    for table in tree.find_all(exp.Table):
        if table.name.lower() not in ctes:
            table.set("db", exp.to_identifier(policy.schema, quoted=True))
    return SqlCheck(_with_limit(tree, policy.max_rows).sql(dialect=policy.dialect, comments=False))


def _structure(tree: exp.Expr, policy: SqlPolicy) -> list[Violation]:
    if not isinstance(tree, exp.Select | exp.SetOperation):
        return [_attack("read_only", "Solo se admiten consultas SELECT.")]
    if any(tree.find_all(*_WRITES)):
        return [_attack("read_only", "La consulta contiene escrituras o DDL (también en CTEs).")]
    violations: list[Violation] = []
    if any(tree.find_all(exp.Lock)):
        violations.append(_fix("locking_clause", "Quita FOR UPDATE/FOR SHARE: es solo lectura."))
    ctes = _cte_names(tree)
    if shadowed := ctes & set(policy.tables):
        violations.append(
            _fix("cte_shadows_table", f"Renombra las CTE {sorted(shadowed)}: ocultan vistas.")
        )
    allowed = ", ".join(policy.tables)
    for table in tree.find_all(exp.Table):
        name, db = table.name.lower(), table.db.lower()
        if not name:
            violations.append(_fix("table_not_allowed", "Solo se puede leer de tablas/vistas."))
        elif db in _SYSTEM_SCHEMAS or name.startswith("pg_") or table.catalog:
            violations.append(_attack("system_catalog", "No se pueden consultar catálogos."))
        elif db and db != policy.schema:
            violations.append(_fix("table_not_allowed", f"Solo el esquema {policy.schema}."))
        elif not db and name in ctes:
            continue
        elif name not in policy.tables:
            violations.append(
                _fix("table_not_allowed", f"La tabla '{name}' no existe. Disponibles: {allowed}.")
            )
    for function in tree.find_all(exp.Anonymous):
        name = function.name.lower()
        if name.startswith(_ADMIN_PREFIXES):
            violations.append(_attack("function_not_allowed", "Funciones de administración."))
        elif name not in policy.untyped_functions:
            violations.append(_fix("function_not_allowed", f"Función no permitida: {name}."))
    return violations


def _cte_names(tree: exp.Expr) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def _with_limit(tree: exp.Expr, max_rows: int) -> exp.Expr:
    limit = tree.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Literal) and value.is_int and int(value.name) <= max_rows:
        return tree
    if isinstance(tree, exp.Query):
        return tree.limit(max_rows, copy=False)
    return tree


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
