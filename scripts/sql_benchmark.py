"""Mide las consultas de infra/sql/queries con EXPLAIN (ANALYZE, BUFFERS) y compara resultados.

Uso:
    uv run python scripts/sql_benchmark.py --label before   # guarda data/benchmarks/before.json
    uv run python scripts/sql_benchmark.py --label after --compare before
    uv run python scripts/sql_benchmark.py --plan 01         # plan completo en texto
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg

from headache_core.config import get_settings

QUERIES_DIR = Path("infra/sql/queries")
RESULTS_DIR = Path("data/benchmarks")


def _scans(node: dict[str, Any]) -> list[str]:
    """Nodos de acceso a tablas del plan: Seq Scan, Index Scan, Bitmap..., Index Only Scan."""
    found: list[str] = []
    if "Relation Name" in node or "Index Name" in node:
        target = node.get("Index Name") or node["Relation Name"]
        found.append(f"{node['Node Type']}({target})")
    for child in node.get("Plans", []):
        found.extend(_scans(child))
    return found


async def _measure(conn: asyncpg.Connection, sql: str, repeat: int) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for _ in range(repeat):
        raw = await conn.fetchval(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)\n{sql}")
        result = json.loads(raw)[0]
        if best is None or result["Execution Time"] < best["Execution Time"]:
            best = result
    assert best is not None
    plan = best["Plan"]
    blocks = plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0)
    return {
        "ms": round(best["Execution Time"], 2),
        "blocks": blocks,
        "scans": sorted(set(_scans(plan))),
    }


async def explain(sql: str) -> str:
    conn = await asyncpg.connect(get_settings().postgres.dsn(driver="postgresql"))
    try:
        rows = await conn.fetch(f"EXPLAIN (ANALYZE, BUFFERS)\n{sql}")
    finally:
        await conn.close()
    return "\n".join(str(r[0]) for r in rows)


async def benchmark(
    queries: dict[str, str], previous: dict[str, Any], repeat: int
) -> dict[str, Any]:
    conn = await asyncpg.connect(get_settings().postgres.dsn(driver="postgresql"))
    results: dict[str, Any] = {}
    try:
        print(f"{'consulta':<34} {'ms':>9} {'antes':>9} {'bloques':>8}  accesos")
        for name, sql in queries.items():
            result = await _measure(conn, sql, repeat)
            results[name] = result
            before = previous.get(name, {}).get("ms")
            print(
                f"{name:<34} {result['ms']:>9.2f} "
                f"{before if before is not None else '-':>9} {result['blocks']:>8}  "
                + ", ".join(result["scans"])
            )
    finally:
        await conn.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", help="Guarda los resultados en data/benchmarks/<label>.json")
    parser.add_argument("--compare", help="Etiqueta de una ejecución anterior para comparar")
    parser.add_argument("--plan", help="Prefijo de una consulta (p. ej. 01) para ver su plan")
    parser.add_argument("--repeat", type=int, default=3, help="Ejecuciones; se queda la mejor")
    args = parser.parse_args()

    queries = {
        path.stem: path.read_text(encoding="utf-8").rstrip().rstrip(";")
        for path in sorted(QUERIES_DIR.glob("*.sql"))
    }
    if args.plan:
        [sql] = [sql for name, sql in queries.items() if name.startswith(args.plan)]
        print(asyncio.run(explain(sql)))
        return

    previous: dict[str, Any] = {}
    if args.compare:
        previous = json.loads((RESULTS_DIR / f"{args.compare}.json").read_text("utf-8"))
    results = asyncio.run(benchmark(queries, previous, args.repeat))
    if args.label:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / f"{args.label}.json").write_text(json.dumps(results, indent=2), "utf-8")


if __name__ == "__main__":
    main()
