"""Informe de una o varias pruebas de carga de Locust (fase 10.7). Sin coste.

    uv run python scripts/load_report.py evals/reports/load/ingenuo evals/reports/load/protegido \
        --out evals/reports/load/README.md

Cada prefijo es el `--csv` de Locust: lee `<prefijo>_stats.csv`, `_failures.csv` y el
`_ready.json` que guarda el locustfile (contadores del edge y del proveedor simulado).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _ms(value: str) -> str:
    try:
        return f"{float(value) / 1000:.2f} s" if float(value) >= 1000 else f"{float(value):.0f} ms"
    except ValueError:
        return "-"


def render_run(prefix: Path) -> list[str]:
    stats = _rows(Path(f"{prefix}_stats.csv"))
    failures = _rows(Path(f"{prefix}_failures.csv"))
    ready_path = Path(f"{prefix}_ready.json")
    ready: dict[str, Any] = json.loads(ready_path.read_text("utf-8")) if ready_path.exists() else {}
    lines = [
        f"## {prefix.name}",
        "",
        "| Petición | Total | Fallos | p50 | p95 | p99 | Máx | req/s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in stats:
        name = row["Name"] if row["Type"] else "**Total**"
        total = int(row["Request Count"])
        failed = int(row["Failure Count"])
        share = f"{failed} ({failed / total:.0%})" if total else "0"
        lines.append(
            f"| {name} | {total} | {share} | {_ms(row['50%'])} | {_ms(row['95%'])} "
            f"| {_ms(row['99%'])} | {_ms(row['Max Response Time'])} "
            f"| {float(row['Requests/s']):.1f} |"
        )
    if failures:
        lines += ["", "| Petición | Motivo | Veces |", "|---|---|---:|"]
        for row in sorted(failures, key=lambda r: -int(r["Occurrences"])):
            error = row["Error"].replace("CatchResponseError(", "").rstrip(")").strip("'\"")
            if error == "0":
                error = "0 (conexión cortada en el cliente)"
            lines.append(f"| {row['Name']} | {error} | {row['Occurrences']} |")
    if ready:
        cache = ready.get("cache", {})
        counters = {
            "Admisión (preguntas)": ready.get("outcomes", {}),
            "Admisión (/cases)": ready.get("cases", {}).get("outcomes", {}),
            "Respuestas por origen": cache.get("answers", {}),
            "Respuestas degradadas": ready.get("assistant", {}),
            "Decisiones de la caché": cache.get("cache", {}),
            "Proveedor (simulado)": ready.get("llm", {}),
        }
        lines.append("")
        lines += [f"- {label}: `{json.dumps(v, sort_keys=True)}`" for label, v in counters.items()]
    return [*lines, ""]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    lines = [
        "# Pruebas de carga del edge (fase 10.7)",
        "",
        "Generado con `scripts/load_report.py` a partir de los CSV de Locust "
        "(`loadtests/locustfile.py`, forma `FestivalOnSale`). Análisis y conclusiones: "
        "[fase-10-escala.md](../../../docs/fase-10-escala.md#pruebas-de-carga).",
        "",
    ]
    for prefix in args.prefixes:
        lines += render_run(prefix)
    args.out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"Informe: {args.out}")


if __name__ == "__main__":
    main()
