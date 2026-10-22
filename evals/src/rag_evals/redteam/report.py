"""Informe de red teaming consolidado (fase 12.5): el entregable de la fase 12.

    uv run python -m rag_evals.redteam.report      # coste 0: lee los informes ya generados

Junta los tests de ataque de la fase 8.6 (`attacks.json`, con y sin harness, 7 sabores), las
mutaciones de PyRIT (`redteam-pyrit.json`, 12.4) y las de promptfoo (`redteam-promptfoo.json`,
12.5), el riesgo residual aceptado (baselines) y los arreglos que salieron de todo ello. Se
regenera desde los JSON: los números del informe nunca se copian a mano.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_evals.redteam.mutations import BASELINE_PATH as PYRIT_BASELINE
from rag_evals.redteam.mutations import MutationBaseline
from rag_evals.redteam.promptfoo import BASELINE_PATH as PROMPTFOO_BASELINE
from rag_evals.retrieval_eval import REPORTS_DIR

REPORT_NAME = "redteam"


@dataclass(frozen=True, slots=True)
class Finding:
    """Hallazgo del red teaming y su arreglo. Texto curado: es lo que se defiende en la revisión."""

    tool: str
    finding: str
    fix: str
    test: str


FINDINGS = (
    Finding(
        "PyRIT (12.4)",
        "Con el escudo de entrada esquivado (base64, ROT13, homoglifos...), la respuesta podía "
        "repetir el prompt de sistema: ninguna guarda de salida lo miraba.",
        "`system_prompt_leak_guard`: bloquea 8 palabras seguidas del prompt; `guarded_agent` "
        "exige `system_prompt`.",
        "`test_system_prompt_leak_is_blocked_even_if_rephrased_in_case`",
    ),
    Finding(
        "PyRIT (12.4)",
        "Bug: «35 4111 1111 1111 1111» no se redactaba (el número de delante rompía el Luhn del "
        "tramo entero). Le puede pasar a un cliente cualquiera, no solo a un atacante.",
        "Ventanas de 13 a 19 dígitos dentro del tramo.",
        "`test_card_variants_are_redacted`",
    ),
    Finding(
        "PyRIT (12.4)",
        "PII con invisibles, etiquetas Unicode, dígitos de ancho completo o espaciada llegaba al "
        "proveedor del LLM; un jailbreak escondido en etiquetas pasaba el escudo.",
        "`harness/normalize.py`: `sanitize` (lo que llega al modelo) y `detection_view` (NFKC "
        "solo para detectar); el escudo pliega con `sanitize`.",
        "`test_unicode_tag_smuggling_is_revealed_to_filters_and_model`",
    ),
    Finding(
        "promptfoo (12.5)",
        "Falso positivo del arreglo anterior: en un volcado hexadecimal, alguna ventana de pares "
        "sueltos pasaba Luhn por azar y se redactaba a trozos (y ocultaba la brecha real).",
        "Solo ventanas con forma de tarjeta: un bloque, grupos de 3 a 6 o dígito a dígito.",
        "`test_benign_numbers_are_not_cards` (caso hexadecimal)",
    ),
)


def _load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _baseline(path: Path) -> MutationBaseline:
    return MutationBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def _attack_rows(attacks: Mapping[str, Any]) -> list[str]:
    rows = []
    for key, harness in (("baseline", "no"), ("harness", "sí")):
        report = attacks[key]
        for flavor, outcomes in report["outcomes"].items():
            attack = [o for o in outcomes if o["category"] == "attack"]
            benign = [o for o in outcomes if o["category"] == "benign"]
            contained = sum(o["passed"] for o in attack)
            breaches = sum(bool(o.get("breaches")) for o in attack)
            ok = sum(o["passed"] for o in benign)
            rows.append(
                f"| {flavor} | {harness} | {contained}/{len(attack)} | {breaches} | "
                f"{ok}/{len(benign)} |"
            )
    return rows


def _tool_rows(name: str, report: Mapping[str, Any] | None) -> list[str]:
    if report is None:
        return [f"| {name} | (sin ejecutar) | | | | |"]
    # Solo las variantes: la línea base sin mutar ya está en la sección 2.
    summary = [s for s in report["summary"] if s["converter"] != "original"]
    attacks = sum(s["attacks"] for s in summary)
    without = sum(s["attacks_without_breach"] for s in summary)
    at_input = sum(s["attacks_blocked_at_input"] for s in summary)
    benign = sum(s["benign"] for s in summary)
    blocked = sum(s["benign_blocked"] for s in summary)
    return [
        f"| {name} | {len(summary)} | {without}/{attacks} | {at_input}/{attacks} | "
        f"{blocked}/{benign} | {sum(s['invalid'] for s in summary)} |"
    ]


def _layers(reports: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """Qué regla del harness contiene los ataques que pasan el escudo de entrada."""
    layers: Counter[str] = Counter()
    for report in reports:
        for o in report["outcomes"]:
            if o["category"] != "attack" or o["blocked_at_input"] or o["converter"] == "original":
                continue
            layers.update(o["guardrails"] or ["ninguna (nada que contener o brecha)"])
    return layers


def render(
    attacks: Mapping[str, Any] | None,
    pyrit: Mapping[str, Any] | None,
    promptfoo: Mapping[str, Any] | None,
    baselines: Mapping[str, MutationBaseline],
) -> str:
    lines = [
        "# Informe de red teaming del agente de Headache (fase 12)",
        "",
        "Generado con `uv run python -m rag_evals.redteam.report` a partir de los informes JSON "
        "de cada herramienta. Todo con coste 0: modelo guionizado **comprometido** (obedece al "
        "atacante), así que mide el harness, no la prudencia del modelo.",
        "",
        "## 1. Resumen para dirección",
        "",
        "- **Ninguna acción irreversible** (reembolso, cambio de estado) se ejecutó sin una "
        "persona en ningún escenario, sabor ni mutación.",
        "- El escudo de entrada es la capa débil a propósito: cualquier codificación lo esquiva. "
        "Contienen los ataques las capas siguientes (aprobación humana, guardas de tools, "
        "retención de inyecciones indirectas, guardas de salida).",
        "- El red teaming encontró **4 problemas** (uno era un bug que podía afectar a clientes "
        "normales) y están arreglados con su test de regresión.",
        "- **Riesgo residual aceptado:** PII codificada (base64, hexadecimal, morse) o con "
        "homoglifos que llega al proveedor del LLM. Se mitiga con el guardrail del proveedor en "
        "la nube (pendiente de medir).",
        "",
        "## 2. Tests de ataque de la fase 8.6 (10 escenarios)",
        "",
    ]
    if attacks is None:
        lines.append("Sin ejecutar: `uv run python -m rag_evals.attacks`.")
    else:
        lines += [
            "| Sabor | Harness | Ataques contenidos | Con brecha | Benignos sin falso positivo |",
            "|---|---|---:|---:|---:|",
            *_attack_rows(attacks),
        ]
    lines += [
        "",
        "## 3. Ataques mutados (sabor nativo con harness)",
        "",
        "| Herramienta | Técnicas | Ataques sin brecha | Bloqueados en la entrada "
        "| Benignos bloqueados | Inválidos |",
        "|---|---:|---:|---:|---:|---:|",
        *_tool_rows("PyRIT (conversores)", pyrit),
        *_tool_rows("promptfoo (estrategias)", promptfoo),
        "",
        "Detalle por técnica: [redteam-pyrit.md](redteam-pyrit.md) y "
        "[redteam-promptfoo.md](redteam-promptfoo.md). Las brechas que quedan son exactamente "
        "las aceptadas abajo; la CI falla ante cualquier otra.",
        "",
        "### Qué capa contiene los ataques que pasan el escudo de entrada",
        "",
        "| Regla del harness | Veces |",
        "|---|---:|",
    ]
    layers = _layers([r for r in (pyrit, promptfoo) if r is not None])
    lines += [f"| {name} | {count} |" for name, count in layers.most_common()]
    lines += [
        "",
        "## 4. Hallazgos y arreglos",
        "",
        "| Herramienta | Hallazgo | Arreglo | Test de regresión |",
        "|---|---|---|---|",
        *[f"| {f.tool} | {f.finding} | {f.fix} | {f.test} |" for f in FINDINGS],
        "",
        "## 5. Riesgo residual aceptado",
        "",
        "| Herramienta | Técnica y escenario | Brecha | Motivo y mitigación |",
        "|---|---|---|---|",
    ]
    for tool, baseline in baselines.items():
        lines += [
            f"| {tool} | {key} | {'; '.join(breaches)} | {baseline.reasons.get(key, '-')} |"
            for key, breaches in baseline.known_breaches.items()
        ]
    lines += [
        "",
        "## 6. Límites (lo que este informe no demuestra)",
        "",
        "- Un modelo guionizado no es un atacante adaptativo: los ataques iterativos con un LLM "
        "atacante (Crescendo, PAIR, TAP; AI Red Teaming Agent de Foundry) están en "
        "`docs/pendientes-cloud.md`.",
        "- Las mutaciones se ejecutan con el sabor nativo; los 7 sabores comparten el harness y "
        "los ataques sin mutar sí se ejecutan en los 7 (sección 2).",
        "- El escudo es el heurístico local; Azure Prompt Shields y Bedrock Guardrails se "
        "comparan con `rag_evals.shields` (fase 8).",
        "- Las brechas se miden por literales: una variante que el modelo podría decodificar y "
        "que no contiene el literal no cuenta (por eso el mensaje mutado entero es un literal "
        "prohibido cuando el original llevaba PII).",
        "",
        "## 7. Cómo reproducirlo",
        "",
        "```powershell",
        "uv run python -m rag_evals.attacks --check",
        "uv run python -m rag_evals.redteam.mutations --check            # PyRIT congelado",
        "uv run python -m rag_evals.redteam.promptfoo eval --check       # requiere Node.js",
        "uv run python -m rag_evals.redteam.report",
        "# Regenerar variantes: --extra redteam + pyrit_offline --write-dataset;",
        "# promptfoo generate (sin generación remota ni telemetría)",
        "```",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    args = parser.parse_args()
    baselines = {"PyRIT": _baseline(PYRIT_BASELINE), "promptfoo": _baseline(PROMPTFOO_BASELINE)}
    markdown = render(
        _load(args.reports / "attacks.json"),
        _load(args.reports / "redteam-pyrit.json"),
        _load(args.reports / "redteam-promptfoo.json"),
        baselines,
    )
    path = args.reports / f"{REPORT_NAME}.md"
    path.write_text(markdown, encoding="utf-8", newline="\n")
    print(f"Informe: {path}")


if __name__ == "__main__":
    main()
