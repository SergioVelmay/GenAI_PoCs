"""Restaura los informes de evals regenerados en los que solo cambian latencias.

Los gates reescriben `evals/reports/*.json` y las latencias (`*_ms`: p50, p95, por escenario) son
ruido entre máquinas. La convención (`AGENTS.md`, 20-11-2026) es restaurarlos siempre sin
preguntar. Este script lo hace con la comprobación dentro: compara el JSON de trabajo con el de
`HEAD` ignorando las claves de latencia y solo restaura si no queda ninguna otra diferencia. Si
cambia cualquier otro campo, no toca el fichero y lo dice: ese cambio entra en el commit.

Es la forma autónoma de hacerlo: `git restore` sigue pidiendo confirmación (regla `ask` en
`.claude/settings.json`) y este script va por `uv run`, ya permitido.

    uv run python scripts/restore_latency_reports.py            # todos los JSON modificados
    uv run python scripts/restore_latency_reports.py evals/reports/attacks.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPORTS = PurePosixPath("evals/reports")
LATENCY_SUFFIX = "_ms"


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


def only_latencies_changed(head: str, current: str) -> bool:
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
        if line.endswith(".json")
    ]


def restore(path: str) -> str:
    """Restaura `path` si solo cambian latencias; devuelve qué ha hecho."""
    posix = PurePosixPath(path.replace("\\", "/"))
    if REPORTS not in posix.parents or posix.suffix != ".json":
        return f"{path}: no es un informe JSON de {REPORTS}; no se toca"
    head = _git("show", f"HEAD:{posix}")
    current = Path(posix).read_bytes()
    # `.gitattributes` fija `eol=lf`: los bytes de `HEAD` y los del disco son comparables.
    if head == current:
        return f"{path}: sin cambios"
    if not only_latencies_changed(head.decode("utf-8"), current.decode("utf-8")):
        return f"{path}: cambian campos que no son latencias; NO se restaura (entra en el commit)"
    Path(posix).write_bytes(head)
    return f"{path}: solo cambiaban latencias; restaurado"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Por defecto, los JSON modificados del directorio")
    args = parser.parse_args()
    paths = args.paths or modified_reports()
    messages = [restore(path) for path in paths]
    print("\n".join(messages) or "Ningún informe modificado")
    return 1 if any("NO se restaura" in message for message in messages) else 0


if __name__ == "__main__":
    sys.exit(main())
