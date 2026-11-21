"""Restaura los informes de evals regenerados en los que solo cambian latencias.

Los gates reescriben `evals/reports/*.json` y `*.md`, y las latencias (`*_ms`: p50, p95, por
escenario) son ruido entre máquinas. La convención (`AGENTS.md`, 20-11-2026) es restaurarlos
siempre sin preguntar. Este script lo hace con la comprobación dentro: compara el informe de
trabajo con el de `HEAD` ignorando las latencias (claves `*_ms` del JSON; columnas con `ms` en la
cabecera de las tablas Markdown) y solo restaura si no queda ninguna otra diferencia. Si cambia
cualquier otro campo, no toca el fichero y lo dice: ese cambio entra en el commit.

Es la forma autónoma de hacerlo: `git restore` sigue pidiendo confirmación (regla `ask` en
`.claude/settings.json`) y este script va por `uv run`, ya permitido.

    uv run python scripts/restore_latency_reports.py            # todos los informes modificados
    uv run python scripts/restore_latency_reports.py evals/reports/attacks.json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPORTS = PurePosixPath("evals/reports")
SUFFIXES = (".json", ".md")
LATENCY_SUFFIX = "_ms"
# Cabecera de una columna de latencia en las tablas Markdown: `p50 ms`, `p95 ms`, `Latencia (ms)`.
LATENCY_HEADER = re.compile(r"\bms\b")


def without_latencies(value: Any) -> Any:
    """El mismo JSON sin las claves de latencia (`latency_ms`, `latency_p95_ms`...)."""
    if isinstance(value, dict):
        return {
            key: without_latencies(item)
            for key, item in value.items()
            if not key.endswith(LATENCY_SUFFIX)
        }
    if isinstance(value, list):
        return [without_latencies(item) for item in value]
    return value


def markdown_without_latencies(text: str) -> list[str]:
    """Las líneas del informe con las celdas de las columnas de latencia (`p50 ms`...) vacías.

    En cada tabla, la primera fila es la cabecera: decide qué columnas son de latencia.
    """
    masked: list[str] = []
    latency_columns: set[int] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            latency_columns = None
            masked.append(line)
            continue
        cells = line.split("|")
        if latency_columns is None:
            latency_columns = {i for i, cell in enumerate(cells) if LATENCY_HEADER.search(cell)}
        masked.append("|".join("" if i in latency_columns else c for i, c in enumerate(cells)))
    return masked


def only_latencies_changed(head: str, current: str, *, markdown: bool = False) -> bool:
    if markdown:
        return markdown_without_latencies(head) == markdown_without_latencies(current)
    return bool(without_latencies(json.loads(head)) == without_latencies(json.loads(current)))


def _git(*args: str) -> bytes:
    """Bytes tal cual: se restaura el fichero exacto de `HEAD`, sin traducir finales de línea."""
    result = subprocess.run(["git", *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def modified_reports() -> list[str]:
    return [
        line
        for line in _git("diff", "--name-only", "HEAD", "--", str(REPORTS)).decode().splitlines()
        if PurePosixPath(line).suffix in SUFFIXES
    ]


def restore(path: str) -> str:
    """Restaura `path` si solo cambian latencias; devuelve qué ha hecho."""
    posix = PurePosixPath(path.replace("\\", "/"))
    if REPORTS not in posix.parents or posix.suffix not in SUFFIXES:
        return f"{path}: no es un informe JSON ni Markdown de {REPORTS}; no se toca"
    head = _git("show", f"HEAD:{posix}")
    current = Path(posix).read_bytes()
    # `.gitattributes` fija `eol=lf`: los bytes de `HEAD` y los del disco son comparables.
    if head == current:
        return f"{path}: sin cambios"
    markdown = posix.suffix == ".md"
    if not only_latencies_changed(head.decode("utf-8"), current.decode("utf-8"), markdown=markdown):
        return f"{path}: cambian campos que no son latencias; NO se restaura (entra en el commit)"
    Path(posix).write_bytes(head)
    return f"{path}: solo cambiaban latencias; restaurado"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Por defecto, los informes modificados")
    args = parser.parse_args()
    paths = args.paths or modified_reports()
    messages = [restore(path) for path in paths]
    print("\n".join(messages) or "Ningún informe modificado")
    return 1 if any("NO se restaura" in message for message in messages) else 0


if __name__ == "__main__":
    sys.exit(main())
