"""Genera el dashboard de Grafana (11.5), las reglas de Prometheus (11.6) y las alertas de Cloud
Monitoring (11.G2: reglas para promtool, sus tests y el JSON de Terraform). Sin coste.

uv run python scripts/build_observability.py          # escribe los ficheros
uv run python scripts/build_observability.py --check  # falla si no están al día
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from genaipocs_core.adapters.cloud_monitoring import (
    build_gcp_rules,
    build_terraform_policies,
    inline_tests,
)
from genaipocs_core.adapters.prometheus import build_dashboard, build_rules

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "infra/local/telemetry/grafana/dashboards/genaipocs-genai.json"
RULES = ROOT / "infra/local/telemetry/rules/genaipocs.rules.yml"
RULE_TESTS = ROOT / "infra/local/telemetry/rules/tests/genaipocs.test.yml"
GCP_ALERTS = ROOT / "infra/terraform/gcp/alerts"
GCP_RULES = GCP_ALERTS / "genaipocs.alerts.yml"
GCP_RULE_TESTS = GCP_ALERTS / "tests/genaipocs.test.yml"
GCP_POLICIES = GCP_ALERTS / "alert_policies.json"
HEADER = (
    "# GENERADO por scripts/build_observability.py desde domain/slo.py y metrics.CATALOG.\n"
    "# No editar a mano. Tests: rules/tests (promtool test rules).\n"
)
GCP_HEADER = (
    "# GENERADO por scripts/build_observability.py (adapters/cloud_monitoring.py) desde las\n"
    "# reglas locales: alertas de Cloud Monitoring, sin recording rules. No editar a mano.\n"
)


def _yaml(data: object) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def render() -> dict[Path, str]:
    rules = build_rules()
    tests = yaml.safe_load(RULE_TESTS.read_text(encoding="utf-8"))
    return {
        DASHBOARD: json.dumps(build_dashboard(), ensure_ascii=False, indent=2) + "\n",
        RULES: HEADER + _yaml(rules),
        GCP_RULES: GCP_HEADER + _yaml(build_gcp_rules(rules)),
        GCP_RULE_TESTS: GCP_HEADER
        + "# Los mismos tests que infra/local/telemetry/rules/tests/genaipocs.test.yml.\n"
        + _yaml(inline_tests(tests, rules, "../genaipocs.alerts.yml")),
        GCP_POLICIES: json.dumps(build_terraform_policies(rules), ensure_ascii=False, indent=2)
        + "\n",
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
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"Escrito {path.relative_to(ROOT)}")
    if stale:
        names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
        print(f"Desactualizado: {names}. Ejecuta scripts/build_observability.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
