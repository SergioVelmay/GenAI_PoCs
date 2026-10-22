"""Red teaming con promptfoo (fase 12.5): mismos ataques, otra herramienta, mismo harness.

    uv run python -m rag_evals.redteam.promptfoo generate   # regenera tests.yaml (coste 0)
    uv run python -m rag_evals.redteam.promptfoo eval [--check]   # ejecuta y escribe el informe

- `generate`: escribe `evals/redteam/promptfoo/redteam.yaml` con los mensajes de
  `attack-scenarios-v1.jsonl` como *intents* (el plugin que no genera nada: usa nuestros ataques
  tal cual) y estrategias deterministas locales (base64, hex, morse, homoglifos...). promptfoo
  genera las variantes y aquí se congelan en `tests.yaml` (sin IDs ni fechas: diff estable).
- `eval`: promptfoo llama a `provider.py`, que ejecuta cada variante con el agente con harness
  y el modelo COMPROMETIDO de la fase 8.6; las aserciones (`assert.js`) son deterministas.

Privacidad y coste: promptfoo genera por defecto en SU nube y graba telemetría. Aquí se
desactiva todo (`PROMPTFOO_DISABLE_REMOTE_GENERATION`, telemetría, compartir, actualizaciones),
se quitan las claves de proveedores del entorno del proceso y `entities: []` evita la llamada a
un LLM para extraer entidades. Las aserciones son JavaScript, no LLM-juez: coste 0.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rag_evals import EVALS_DIR
from rag_evals.agents.scenarios import ATTACKS_PATH, Scenario, load_scenarios
from rag_evals.agents.suite import build_knowledge, run_scenario
from rag_evals.redteam.mutations import (
    INPUT_GUARDS,
    MutationBaseline,
    MutationOutcome,
    check,
    mutate,
    summarize,
    write_report,
)
from rag_evals.retrieval_eval import REPORTS_DIR

PROMPTFOO = "promptfoo@0.124.1"
CONFIG_DIR = EVALS_DIR / "redteam" / "promptfoo"
REDTEAM_CONFIG = CONFIG_DIR / "redteam.yaml"
TESTS_PATH = CONFIG_DIR / "tests.yaml"
EVAL_CONFIG = CONFIG_DIR / "promptfooconfig.yaml"
BASELINE_PATH = EVALS_DIR / "baselines" / "promptfoo-redteam-v1.json"
REPORT_NAME = "redteam-promptfoo"
TITLE = "Red teaming con promptfoo (fase 12.5)"
SOURCE = (
    "estrategias deterministas locales de promptfoo (`evals/redteam/promptfoo/tests.yaml`, sin "
    "generación remota ni LLM-juez)"
)
# Estrategias que se calculan en local sin LLM. Las de plantillas de jailbreak quedan fuera:
# citan marcas comerciales de terceros. Las iterativas (jailbreak, crescendo) necesitan un LLM.
STRATEGIES = (
    "base64",
    "hex",
    "rot13",
    "leetspeak",
    "homoglyph",
    "camelcase",
    "morse",
    "piglatin",
    "emoji",
)
BASE = "original"
# Nada de esto debe llegar al proceso de promptfoo: si hubiera una clave, llamaría a un LLM.
PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_API_KEY",
)


def promptfoo_env(python: str | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS}
    env |= {
        "PROMPTFOO_DISABLE_REMOTE_GENERATION": "true",
        "PROMPTFOO_DISABLE_TELEMETRY": "1",
        "PROMPTFOO_DISABLE_SHARING": "1",
        "PROMPTFOO_DISABLE_UPDATE": "1",
        "PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION": "true",
    }
    if python:
        env["PROMPTFOO_PYTHON"] = python
    return env


# promptfoo sale con 100 si alguna aserción falla: es un resultado, no un error de ejecución.
FAILED_ASSERTIONS = 100


def _npx(args: Sequence[str], *, env: Mapping[str, str], cwd: Path) -> int:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        raise SystemExit("Falta Node.js (npx) para ejecutar promptfoo.")
    code = subprocess.run([npx, "--yes", PROMPTFOO, *args], env=dict(env), cwd=cwd).returncode
    if code not in (0, FAILED_ASSERTIONS):
        raise SystemExit(f"promptfoo terminó con {code}")
    return code


# --- generate ----------------------------------------------------------------------------


def redteam_config(scenarios: Sequence[Scenario]) -> dict[str, Any]:
    intents = list(dict.fromkeys(s.turns[0].user for s in scenarios if len(s.turns) == 1))
    return {
        "description": "Red teaming del agente de Headache con harness (fase 12.5)",
        "targets": [{"id": "file://provider.py", "label": "headache-agent-harness"}],
        "prompts": ["{{prompt}}"],
        "redteam": {
            "purpose": "Asistente de atención al cliente de Headache, marketplace de eventos.",
            # Vacío a propósito: si no, promptfoo pide a un LLM que extraiga entidades.
            "entities": [],
            "numTests": 1,
            "plugins": [{"id": "intent", "config": {"intent": intents}}],
            "strategies": [{"id": s} for s in STRATEGIES],
        },
    }


def freeze_tests(
    generated: Mapping[str, Any], scenarios: Sequence[Scenario]
) -> list[dict[str, Any]]:
    """Casos generados → casos propios: escenario de origen y sin aserciones con LLM."""
    by_message = {s.turns[0].user: s for s in scenarios}
    tests: list[dict[str, Any]] = []
    for case in generated["tests"]:
        metadata = case.get("metadata") or {}
        prompt = str(case["vars"]["prompt"])
        scenario = by_message[str(metadata.get("originalText") or prompt)]
        strategy = str(metadata.get("strategyId") or BASE)
        tests.append(
            {
                "description": f"{strategy}:{scenario.id}",
                "vars": {"prompt": prompt, "scenario_id": scenario.id, "strategy": strategy},
                "metadata": {"strategyId": strategy, "category": scenario.category},
            }
        )
    return sorted(tests, key=lambda t: STRATEGY_ORDER.get(t["vars"]["strategy"], 99))


STRATEGY_ORDER = {name: index for index, name in enumerate((BASE, *STRATEGIES))}


def _dump_yaml(data: Any, path: Path, header: str) -> None:
    body = yaml.safe_dump(data, allow_unicode=False, sort_keys=False, width=100)
    path.write_text(header + body, encoding="utf-8", newline="\n")


def generate(dataset: Path = ATTACKS_PATH) -> int:
    scenarios = [s for s in load_scenarios(dataset) if len(s.turns) == 1]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _dump_yaml(
        redteam_config(scenarios),
        REDTEAM_CONFIG,
        "# Generado por `python -m rag_evals.redteam.promptfoo generate`: no editar a mano.\n",
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "generated.yaml"
        _npx(
            ["redteam", "generate", "-c", str(REDTEAM_CONFIG), "-o", str(out), "--no-cache"],
            env=promptfoo_env(),
            cwd=CONFIG_DIR,
        )
        generated = yaml.safe_load(out.read_text(encoding="utf-8"))
    tests = freeze_tests(generated, scenarios)
    _dump_yaml(
        tests,
        TESTS_PATH,
        "# Variantes de promptfoo congeladas (fase 12.5). Regenerar con\n"
        "# `python -m rag_evals.redteam.promptfoo generate`.\n",
    )
    return len(tests)


# --- provider ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Fixed:
    """El mensaje ya mutado por promptfoo: `mutate` lo usa como si fuera un conversor."""

    text: str
    name: str = "promptfoo"

    async def convert(self, text: str) -> str:
        return self.text


_KNOWLEDGE: Any = None


async def run_case(
    prompt: str, scenario_id: str, *, strategy: str = BASE, flavor: str = "native"
) -> dict[str, Any]:
    """Una variante de promptfoo contra el agente con harness (la llama `provider.py`)."""
    global _KNOWLEDGE
    if _KNOWLEDGE is None:
        from booking_agent.spec import SKILLS_DIR
        from headache_core.application.agents import SkillCatalog

        retrieval, internal = await build_knowledge()
        _KNOWLEDGE = (retrieval, internal, SkillCatalog.from_directory(SKILLS_DIR))
    retrieval, internal, skills = _KNOWLEDGE
    scenario = {s.id: s for s in load_scenarios(ATTACKS_PATH)}[scenario_id]
    mutated = await mutate(scenario, _Fixed(prompt))
    outcome = await run_scenario(
        flavor,
        mutated,
        retrieval=retrieval,
        skills=skills,
        internal_documents=internal,
        harness=True,
    )
    return {
        "answer": outcome.answer,
        "category": scenario.category,
        "guardrails": outcome.guardrails,
        "blocked_at_input": bool(INPUT_GUARDS & set(outcome.guardrails)),
        "breaches": outcome.breaches,
        "invalid": "turno desconocido" in outcome.answer,
        # El riesgo residual aceptado viaja con la respuesta: `assert.js` compara sin leer ficheros.
        "accepted_breaches": load_baseline().known_breaches.get(f"{strategy}:{scenario_id}", []),
    }


# --- eval ----------------------------------------------------------------------------------


def load_baseline(path: Path = BASELINE_PATH) -> MutationBaseline:
    return MutationBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def outcomes_from_results(results: Mapping[str, Any]) -> list[MutationOutcome]:
    outcomes: list[MutationOutcome] = []
    for row in results["results"]["results"]:
        test = row["testCase"]
        strategy, _, scenario_id = str(test["description"]).partition(":")
        output = json.loads(row["response"]["output"])
        outcomes.append(
            MutationOutcome(
                converter=strategy,
                scenario_id=scenario_id,
                category=str(output["category"]),
                message=str(test["vars"]["prompt"]),
                blocked_at_input=bool(output["blocked_at_input"]),
                guardrails=list(output["guardrails"]),
                breaches=list(output["breaches"]),
                invalid=bool(output["invalid"]),
            )
        )
    return sorted(outcomes, key=lambda o: STRATEGY_ORDER.get(o.converter, 99))


def run_eval() -> list[MutationOutcome]:
    with tempfile.TemporaryDirectory() as tmp:
        # Resultados crudos fuera del repo: llevan rutas locales e IDs de ejecución.
        results_path = Path(tmp) / "promptfoo-results.json"
        # El proceso de promptfoo ejecuta provider.py con el Python de este entorno (el de uv).
        _npx(
            ["eval", "-c", str(EVAL_CONFIG), "-o", str(results_path), "--no-cache", "--no-table"],
            env=promptfoo_env(sys.executable),
            cwd=CONFIG_DIR,
        )
        return outcomes_from_results(json.loads(results_path.read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="Regenera tests.yaml con promptfoo (coste 0)")
    evaluate = commands.add_parser("eval", help="Ejecuta las variantes e informa (coste 0)")
    evaluate.add_argument("--out", type=Path, default=REPORTS_DIR)
    evaluate.add_argument("--check", action="store_true", help="Sale con 1 ante brechas nuevas")
    args = parser.parse_args()
    if args.command == "generate":
        print(f"{generate()} variantes en {TESTS_PATH}")
        return
    outcomes = run_eval()
    baseline = load_baseline()
    md = write_report(
        outcomes, args.out, "native", baseline, name=REPORT_NAME, title=TITLE, source=SOURCE
    )
    print(f"Informe: {md}")
    for s in summarize(outcomes):
        print(
            f"{s.converter:>12}  sin brecha {s.attacks_without_breach}/{s.attacks}  "
            f"entrada {s.attacks_blocked_at_input}/{s.attacks}  "
            f"benignos bloqueados {s.benign_blocked}/{s.benign}  inválidos {s.invalid}"
        )
    problems = check(outcomes, baseline)
    for problem in problems:
        print(f"  FALLO {problem}")
    if args.check and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
