"""Carga masiva de los datos sintéticos en PostgreSQL con COPY (asyncpg).

Flujo recomendado para el ejercicio de índices:
    uv run alembic -c packages/headache_core/alembic.ini upgrade 0001   # tablas sin índices
    uv run python scripts/load_seed.py --truncate                       # carga rápida
    uv run python scripts/sql_benchmark.py --label before
    uv run alembic -c packages/headache_core/alembic.ini upgrade head   # crea los índices
    uv run python scripts/sql_benchmark.py --label after
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from headache_core.config import get_settings

Record = tuple[Any, ...]
BATCH_SIZE = 50_000


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _venue(r: dict[str, Any]) -> Record:
    return (r["id"], r["name"], r["city"], r["country"], r["address"], r["capacity"])


def _event(r: dict[str, Any]) -> Record:
    price = r["price"]
    return (
        r["id"],
        r["title"],
        r["category"],
        r["city"],
        r["venue_id"],
        r["description"],
        Decimal(price["amount"]),
        price["currency"],
        r["refundable"],
        r["refund_window_hours"],
    )


def _session(r: dict[str, Any]) -> Record:
    return (r["id"], r["event_id"], _ts(r["starts_at"]), r["capacity"], r["available"])


def _customer(r: dict[str, Any]) -> Record:
    return (r["id"], r["full_name"], r["email"], r["country"], r["language"])


def _order(r: dict[str, Any]) -> Record:
    total = r["total"]
    return (
        r["id"],
        r["customer_id"],
        r["session_id"],
        r["quantity"],
        Decimal(total["amount"]),
        total["currency"],
        r["status"],
        _ts(r["created_at"]),
    )


def _refund(r: dict[str, Any]) -> Record:
    amount = r["amount"]
    return (
        r["id"],
        r["order_id"],
        Decimal(amount["amount"]),
        amount["currency"],
        r["reason"],
        r["status"],
        _ts(r["requested_at"]),
    )


# Orden de carga = orden de dependencias de las FK.
TABLES: list[tuple[str, str, list[str], Callable[[dict[str, Any]], Record]]] = [
    ("venues", "venues.jsonl", ["id", "name", "city", "country", "address", "capacity"], _venue),
    (
        "events",
        "events.jsonl",
        [
            "id",
            "title",
            "category",
            "city",
            "venue_id",
            "description",
            "price_amount",
            "currency",
            "refundable",
            "refund_window_hours",
        ],
        _event,
    ),
    (
        "sessions",
        "sessions.jsonl",
        ["id", "event_id", "starts_at", "capacity", "available"],
        _session,
    ),
    (
        "customers",
        "customers.jsonl",
        ["id", "full_name", "email", "country", "language"],
        _customer,
    ),
    (
        "orders",
        "orders.jsonl",
        [
            "id",
            "customer_id",
            "session_id",
            "quantity",
            "total_amount",
            "currency",
            "status",
            "created_at",
        ],
        _order,
    ),
    (
        "refunds",
        "refunds.jsonl",
        ["id", "order_id", "amount", "currency", "reason", "status", "requested_at"],
        _refund,
    ),
]


def _read(path: Path, convert: Callable[[dict[str, Any]], Record]) -> Iterator[Record]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            yield convert(json.loads(line))


async def load(data_dir: Path, *, truncate: bool) -> None:
    conn = await asyncpg.connect(get_settings().postgres.dsn(driver="postgresql"))
    try:
        names = ", ".join(table for table, *_ in TABLES)
        if truncate:
            await conn.execute(f"TRUNCATE {names} CASCADE")
        elif await conn.fetchval("SELECT EXISTS (SELECT 1 FROM venues)"):
            raise SystemExit("Las tablas ya tienen datos. Usa --truncate para recargarlas.")

        for table, filename, columns, convert in TABLES:
            started = time.perf_counter()
            rows = _read(data_dir / filename, convert)
            total = 0
            while batch := list(itertools.islice(rows, BATCH_SIZE)):
                await conn.copy_records_to_table(table, records=batch, columns=columns)
                total += len(batch)
            elapsed = time.perf_counter() - started
            print(f"{table:>10}: {total:>10,} filas en {elapsed:6.1f} s")

        # Estadísticas frescas: sin ANALYZE el planificador estima a ciegas tras una carga masiva.
        await conn.execute(f"ANALYZE {names}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--truncate", action="store_true", help="Vacía las tablas antes de cargar")
    args = parser.parse_args()
    asyncio.run(load(args.data, truncate=args.truncate))


if __name__ == "__main__":
    main()
