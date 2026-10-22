"""Genera el dashboard de Grafana (11.5) y las reglas de Prometheus (11.6). Sin coste.

uv run python scripts/build_observability.py          # escribe los ficheros
uv run python scripts/build_observability.py --check  # falla si no están al día
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from headache_core.adapters.prometheus import build_dashboard, build_rules

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "infra/local/telemetry/grafana/dashboards/headache-genai.json"
RULES = ROOT / "infra/local/telemetry/rules/headache.rules.yml"
HEADER = (
    "# GENERADO por scripts/build_observability.py desde domain/slo.py y metrics.CATALOG.\n"
    "# No editar a mano. Tests: rules/tests (promtool test rules).\n"
)


def render() -> dict[Path, str]:
    rules = yaml.safe_dump(build_rules(), sort_keys=False, allow_unicode=True, width=100)
    return {
        DASHBOARD: json.dumps(build_dashboard(), ensure_ascii=False, indent=2) + "\n",
        RULES: HEADER + rules,
    }


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
