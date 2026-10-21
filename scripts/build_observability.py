"""Genera el dashboard de Grafana desde el catálogo de métricas (fase 11.5). Sin coste.

uv run python scripts/build_observability.py          # escribe los ficheros
uv run python scripts/build_observability.py --check  # falla si no están al día
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from headache_core.adapters.prometheus import build_dashboard

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "infra/local/telemetry/grafana/dashboards/headache-genai.json"


def render() -> dict[Path, str]:
    return {DASHBOARD: json.dumps(build_dashboard(), ensure_ascii=False, indent=2) + "\n"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Solo comprobar")
    args = parser.parse_args()
    stale = []
    for path, content in render().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(path)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"Escrito {path.relative_to(ROOT)}")
    if stale:
        names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
        print(f"Desactualizado: {names}. Ejecuta scripts/build_observability.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
