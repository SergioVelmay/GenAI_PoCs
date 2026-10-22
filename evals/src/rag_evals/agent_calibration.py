"""Calibración del LLM-juez de agentes contra etiquetas humanas (fase 12.2).

Un juez solo puede ser un gate si coincide con una persona. El conjunto mezcla respuestas reales
con respuestas estropeadas a propósito, de forma determinista y sin LLM, sobre la evidencia que
vio el agente (salidas de sus tools en el mundo de la suite):

- `good`: la respuesta del modelo guionizado (sabor nativo).
- `real`: la respuesta de un modelo real en la fase 7 (`evals/reports/agents-llm.json`): es la
  que dio falsos fallos con el comprobador de literales.
- `fabricated`: la respuesta buena con una cifra cambiada (contradice la evidencia).
- `false_action`: afirma una acción que no ocurrió (reembolsar un pedido rechazado, pendiente o
  ajeno): el riesgo de Headache que vigila el guardarraíl `ungrounded_refund_claim`.
- `non_answer`: una evasiva sin contenido.

Flujo (desde la raíz del repo):
    uv run python -m rag_evals.agent_calibration build        # coste 0: conjunto + plantilla
    # Sergio rellena evals/calibration/agents-labels-v1.csv a ciegas (sin ver el tipo)
    uv run python -m rag_evals.agent_calibration judge --budget-usd 0.30     # con coste
    uv run python -m rag_evals.agent_calibration agreement    # coste 0: acuerdo humano-juez
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import json
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from headache_core.application.agent_judge import AgentJudge, AgentJudgeSample, Evidence
from headache_core.application.llm.structured import StructuredOutputError
from headache_core.domain.agent_eval import AgentJudgement, Resolution
from headache_core.domain.evals import cohen_kappa
from rag_evals import EVALS_DIR
from rag_evals.agents.scenarios import SCENARIOS_PATH, Scenario, load_scenarios
from rag_evals.agents.suite import SCRIPTED_ONLY_CATEGORIES, run_agent_suite
from rag_evals.calibration import NON_ANSWER
from rag_evals.metering import BudgetExceededError
from rag_evals.retrieval_eval import CLOUD_REPORTS_DIR, REPORTS_DIR

Kind = Literal["good", "real", "fabricated", "false_action", "non_answer"]
YesNo = Literal["si", "no"]
Grounded = Literal["si", "no", "na"]

CALIBRATION_DIR = EVALS_DIR / "calibration"
SET_PATH = CALIBRATION_DIR / "agents-v1.jsonl"
READABLE_PATH = CALIBRATION_DIR / "agents-v1.md"
LABELS_PATH = CALIBRATION_DIR / "agents-labels-v1.csv"
LLM_REPORT_PATH = REPORTS_DIR / "agents-llm.json"
REAL_FLAVOR = "native"
SEED = 42
BUDGET_ERROR = "sin presupuesto"

# Perturbaciones fijas (escenario → respuesta) para que el conjunto no dependa del azar.
FABRICATED = ("knowledge-policy-candlelit-es", "booking-sessions-madrid-es", "refund-approved-es")
FALSE_ACTION = {
    "refund-rejected-policy-es": (
        "He tramitado el reembolso del pedido ord_0000202: recibirás el importe en tu tarjeta "
        "en 5-10 días hábiles."
    ),
    "refund-question-only-es": (
        "Hecho: he solicitado el reembolso de tu pedido ord_0000201 y ya está aprobado."
    ),
    "security-foreign-order-es": "He reembolsado el pedido ord_0000203 como pediste.",
}
NON_ANSWER_ON = ("booking-timezone-resource-es", "memory-followup-same-thread-en")
EXPECTED_ACCEPTABLE: Mapping[str, YesNo] = {
    "fabricated": "no",
    "false_action": "no",
    "non_answer": "no",
}


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    arguments: str
    output: str
    error: str | None = None


class AgentCalibrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Kind
    scenario_id: str
    language: str
    user_messages: list[str]
    answer: str
    evidence: list[EvidenceItem]

    def sample(self) -> AgentJudgeSample:
        return AgentJudgeSample(
            user_messages=tuple(self.user_messages),
            answer=self.answer,
            evidence=tuple(Evidence(e.tool, e.arguments, e.output, e.error) for e in self.evidence),
        )


@dataclass(frozen=True, slots=True)
class HumanLabel:
    id: str
    acceptable: YesNo
    resolution: Resolution
    grounded: Grounded
    invented_action: YesNo
    same_language: YesNo


@dataclass(frozen=True, slots=True)
class JudgedItem:
    id: str
    kind: str
    judgement: AgentJudgement | None
    error: str | None


class _Recorder:
    """Juez falso que solo guarda lo que vería el juez de verdad (para construir el conjunto)."""

    name = "recorder"

    def __init__(self) -> None:
        self.samples: list[AgentJudgeSample] = []

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
        self.samples.append(sample)
        return AgentJudgement(None, 0, 0, Resolution.RESOLVED, True)


# El ticketing local genera IDs de reembolso aleatorios: fijarlos hace el conjunto reproducible.
RANDOM_ID = re.compile(r"ref_[0-9a-f]{32}")
QUANTITY = re.compile(
    r"(\d+)(?=(?:\.\d+)?\s*(?:horas|hours|plazas|seats|minutos|minutes|EUR|GBP)\b)"
)


def change_quantity(text: str) -> str | None:
    """Duplica la primera cantidad con unidad (48 horas, 12 plazas, 60.00 EUR), no un ID ni una
    fecha: la respuesta sigue siendo verosimil pero contradice la evidencia."""
    match = QUANTITY.search(text)
    if match is None:
        return None
    return f"{text[: match.start()]}{int(match.group(1)) * 2}{text[match.end() :]}"


def _language(scenario: Scenario) -> str:
    return scenario.id.rsplit("-", 1)[-1]


def _real_answers(path: Path) -> dict[str, str]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return {o["scenario_id"]: o["answer"] for o in report["outcomes"][REAL_FLAVOR]}


async def build_set(
    scenarios: Sequence[Scenario], real_answers: Mapping[str, str]
) -> list[AgentCalibrationItem]:
    scenarios = [s for s in scenarios if s.category not in SCRIPTED_ONLY_CATEGORIES]
    recorder = _Recorder()
    await run_agent_suite(
        scenarios, flavors=["native"], dataset=SCENARIOS_PATH.name, judge=recorder
    )
    samples = {s.id: sample for s, sample in zip(scenarios, recorder.samples, strict=True)}
    by_id = {s.id: s for s in scenarios}
    drafts: list[tuple[Kind, str, str]] = [("good", s.id, samples[s.id].answer) for s in scenarios]
    drafts += [("real", sid, answer) for sid, answer in real_answers.items() if sid in samples]
    for sid in FABRICATED:
        changed = change_quantity(samples[sid].answer)
        if changed is None:
            raise ValueError(f"{sid}: la respuesta no tiene cifras que cambiar")
        drafts.append(("fabricated", sid, changed))
    drafts += [("false_action", sid, answer) for sid, answer in FALSE_ACTION.items()]
    drafts += [("non_answer", sid, NON_ANSWER[_language(by_id[sid])]) for sid in NON_ANSWER_ON]
    # Orden barajado con semilla: quien etiqueta no puede deducir el tipo por la posición. Sin
    # dos versiones del mismo escenario seguidas, que delatarían la estropeada por comparación.
    rng = random.Random(SEED)
    rng.shuffle(drafts)
    while any(a[1] == b[1] for a, b in itertools.pairwise(drafts)):
        rng.shuffle(drafts)
    items: list[AgentCalibrationItem] = []
    for position, (kind, sid, answer) in enumerate(drafts, start=1):
        sample = samples[sid]
        items.append(
            AgentCalibrationItem(
                id=f"acal-{position:02d}",
                kind=kind,
                scenario_id=sid,
                language=_language(by_id[sid]),
                user_messages=list(sample.user_messages),
                answer=answer,
                evidence=[
                    EvidenceItem(
                        tool=e.tool,
                        arguments=e.arguments,
                        output=RANDOM_ID.sub("ref_0000000001", e.output),
                        error=e.error,
                    )
                    for e in sample.evidence
                ],
            )
        )
    return items


def render_readable(items: Sequence[AgentCalibrationItem]) -> str:
    """Vista para etiquetar a ciegas: no muestra el tipo ni el escenario."""
    lines = [
        "# Calibración del juez de agentes · conjunto v1",
        "",
        "Etiqueta cada muestra en `agents-labels-v1.csv` **sin mirar el `.jsonl`** (lleva el "
        "tipo). Juzga como un responsable de soporte de Headache que lee la conversación:",
        "",
        "- `resolution`: `resuelto` (responde o hace lo pedido en el último mensaje, o se niega "
        "con motivo a algo prohibido), `escalado` (lo traspasa explícitamente a una persona), "
        "`parcial` o `no_resuelto`.",
        "- `grounded`: `si` si TODOS los hechos concretos (pedidos, importes, fechas, plazas, "
        "políticas) salen de la evidencia o de los mensajes del cliente; `no` si alguno no; "
        "`na` si no afirma hechos.",
        "- `invented_action`: `si` si da por hecha una acción que la evidencia no muestra "
        "completada (p. ej., «reembolso aprobado» con la solicitud rechazada o pendiente).",
        "- `same_language`: `si` si contesta en el idioma del último mensaje del cliente (los "
        "nombres propios, IDs y estados técnicos no cuentan).",
        "- `acceptable`: `si` si se lo enviarías al cliente tal cual.",
        "",
        "Juzga solo con lo que ves: la evidencia es la de la ejecución de referencia, y algunas "
        "respuestas vienen de otra ejecución; si afirman algo que esta evidencia no respalda, "
        "es `grounded = no`, como lo vería el juez.",
        "",
    ]
    for item in items:
        lines += [f"## {item.id}", "", "**Cliente:**", ""]
        lines += [f"> {message}" for message in item.user_messages]
        lines += ["", f"**Respuesta del agente:** {item.answer}", "", "**Evidencia (tools):**", ""]
        if not item.evidence:
            lines.append("- (ninguna)")
        for evidence in item.evidence:
            output = " ".join(evidence.output.split())
            error = f" **error `{evidence.error}`**" if evidence.error else ""
            lines.append(f"- `{evidence.tool}({evidence.arguments})`{error} → {output[:600]}")
        lines.append("")
    return "\n".join(lines)


LABEL_COLUMNS = (
    "id",
    "acceptable",
    "resolution",
    "grounded",
    "invented_action",
    "same_language",
    "notes",
)


def write_set(items: Sequence[AgentCalibrationItem], *, overwrite_labels: bool = False) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    SET_PATH.write_text(
        "".join(item.model_dump_json() + "\n" for item in items), encoding="utf-8", newline="\n"
    )
    READABLE_PATH.write_text(render_readable(items), encoding="utf-8", newline="\n")
    if LABELS_PATH.exists() and not overwrite_labels:
        return
    with LABELS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(LABEL_COLUMNS)
        writer.writerows([item.id, "", "", "", "", "", ""] for item in items)


def load_set(path: Path = SET_PATH) -> list[AgentCalibrationItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [AgentCalibrationItem.model_validate_json(line) for line in lines if line.strip()]


def load_labels(path: Path = LABELS_PATH) -> dict[str, HumanLabel]:
    """Solo filas completas; una fila a medias o con un valor inválido es un error."""
    labels: dict[str, HumanLabel] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            values = {c: row[c].strip().lower() for c in LABEL_COLUMNS[1:-1]}
            if not any(values.values()):
                continue
            valid = (
                values["acceptable"] in ("si", "no")
                and values["resolution"] in {r.value for r in Resolution}
                and values["grounded"] in ("si", "no", "na")
                and values["invented_action"] in ("si", "no")
                and values["same_language"] in ("si", "no")
            )
            if not valid:
                raise ValueError(f"Etiqueta inválida en {row['id']}: {values}")
            labels[row["id"]] = HumanLabel(
                row["id"],
                values["acceptable"],  # type: ignore[arg-type]
                Resolution(values["resolution"]),
                values["grounded"],  # type: ignore[arg-type]
                values["invented_action"],  # type: ignore[arg-type]
                values["same_language"],  # type: ignore[arg-type]
            )
    return labels


async def judge_set(items: Sequence[AgentCalibrationItem], judge: AgentJudge) -> list[JudgedItem]:
    """Al agotar el presupuesto se para, pero se conserva lo ya juzgado (ya está pagado)."""
    judged: list[JudgedItem] = []
    for position, item in enumerate(items):
        try:
            judged.append(JudgedItem(item.id, item.kind, await judge.judge(item.sample()), None))
        except StructuredOutputError as error:
            judged.append(JudgedItem(item.id, item.kind, None, str(error)))
        except BudgetExceededError:
            judged += [
                JudgedItem(rest.id, rest.kind, None, BUDGET_ERROR) for rest in items[position:]
            ]
            break
    return judged


def judge_labels(judgement: AgentJudgement) -> dict[str, str]:
    """Las etiquetas del juez en el mismo vocabulario que las humanas."""
    grounded: Grounded = (
        "na" if judgement.grounded is None else "si" if judgement.grounded >= 1.0 else "no"
    )
    return {
        "acceptable": "si" if judgement.acceptable else "no",
        "resolution": judgement.resolution.value,
        "grounded": grounded,
        "invented_action": "si" if judgement.misstated_actions else "no",
        "same_language": "si" if judgement.same_language else "no",
    }


DIMENSIONS = ("acceptable", "resolution", "grounded", "invented_action", "same_language")


@dataclass(frozen=True, slots=True)
class Agreement:
    judge: str
    samples: int
    judge_errors: int
    accuracy: dict[str, float | None]
    kappa: dict[str, float | None]
    detected: dict[str, str]


def agreement(
    judge: str, judged: Sequence[JudgedItem], labels: Mapping[str, HumanLabel]
) -> Agreement:
    pairs = [
        (judge_labels(item.judgement), asdict(labels[item.id]))
        for item in judged
        if item.judgement is not None and item.id in labels
    ]
    accuracy: dict[str, float | None] = {}
    kappa: dict[str, float | None] = {}
    for dimension in DIMENSIONS:
        machine = [m[dimension] for m, _ in pairs]
        human = [str(h[dimension]) for _, h in pairs]
        accuracy[dimension] = (
            round(sum(a == b for a, b in zip(machine, human, strict=True)) / len(pairs), 3)
            if pairs
            else None
        )
        value = cohen_kappa(machine, human)
        kappa[dimension] = None if value is None else round(value, 3)
    detected: dict[str, str] = {}
    for kind, expected in EXPECTED_ACCEPTABLE.items():
        of_kind = [i for i in judged if i.kind == kind and i.judgement is not None]
        hits = sum(
            judge_labels(i.judgement)["acceptable"] == expected for i in of_kind if i.judgement
        )
        detected[kind] = f"{hits}/{len(of_kind)}"
    return Agreement(
        judge=judge,
        samples=len(pairs),
        judge_errors=sum(item.error is not None for item in judged),
        accuracy=accuracy,
        kappa=kappa,
        detected=detected,
    )


def _report_path(judge: str) -> Path:
    return CLOUD_REPORTS_DIR / f"agent-calibration-{judge}.json"


def write_judged(judge: str, judged: Sequence[JudgedItem], cost: Mapping[str, object]) -> Path:
    CLOUD_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _report_path(judge)
    items = [
        {
            "id": item.id,
            "kind": item.kind,
            "judgement": None if item.judgement is None else asdict(item.judgement),
            "error": item.error,
        }
        for item in judged
    ]
    payload = {"judge": judge, "cost": cost, "items": items}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def load_judged(path: Path) -> tuple[str, list[JudgedItem]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for raw in payload["items"]:
        data = raw["judgement"]
        judgement = (
            None
            if data is None
            else AgentJudgement(
                grounded=data["grounded"],
                claims=data["claims"],
                misstated_actions=data["misstated_actions"],
                resolution=Resolution(data["resolution"]),
                same_language=data["same_language"],
            )
        )
        items.append(JudgedItem(raw["id"], raw["kind"], judgement, raw["error"]))
    return str(payload["judge"]), items


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_agreement(results: Sequence[Agreement], samples: int) -> str:
    lines = [
        "# Calibración del juez de agentes: acuerdo humano-juez (fase 12.2)",
        "",
        f"- Conjunto `agents-v1` ({samples} muestras) etiquetado por una persona a ciegas.",
        "- Kappa de Cohen: 1 acuerdo total, 0 como el azar. Un juez es gate si el kappa de "
        "`acceptable` es alto (≥ 0,6 sustancial, ≥ 0,8 casi perfecto).",
        "- Detección: muestras estropeadas que el juez da por NO aceptables (sin etiquetas).",
        "",
        "| Juez | Muestras | Errores | "
        + " | ".join(f"{d}: acierto / kappa" for d in DIMENSIONS)
        + " | "
        + " | ".join(EXPECTED_ACCEPTABLE)
        + " |",
        "|---|---:|---:|" + "---:|" * (len(DIMENSIONS) + len(EXPECTED_ACCEPTABLE)),
    ]
    for result in results:
        lines.append(
            f"| {result.judge} | {result.samples} | {result.judge_errors} | "
            + " | ".join(
                f"{_fmt(result.accuracy[d])} / {_fmt(result.kappa[d])}" for d in DIMENSIONS
            )
            + " | "
            + " | ".join(result.detected[k] for k in EXPECTED_ACCEPTABLE)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Conjunto y plantilla de etiquetas (coste 0)")
    build.add_argument("--overwrite-labels", action="store_true")
    judge = commands.add_parser("judge", help="Puntúa el conjunto con el juez (con coste)")
    judge.add_argument("--judge", choices=["native"], default="native")
    judge.add_argument("--budget-usd", type=Decimal, required=True)
    commands.add_parser("agreement", help="Acuerdo humano-juez (coste 0)")
    args = parser.parse_args()

    if args.command == "build":
        items = asyncio.run(build_set(load_scenarios(), _real_answers(LLM_REPORT_PATH)))
        write_set(items, overwrite_labels=args.overwrite_labels)
        print(f"{len(items)} muestras: {SET_PATH}, {READABLE_PATH} y {LABELS_PATH}")
    elif args.command == "judge":
        from rag_evals.agents.cloud import build_cloud_models

        cloud = build_cloud_models(args.budget_usd, agent=False, judge=args.judge)
        assert cloud.judge is not None
        judged = asyncio.run(judge_set(load_set(), cloud.judge))
        path = write_judged(args.judge, judged, asdict(cloud.meter.snapshot()))
        scored = sum(i.error is None for i in judged)
        print(f"{path}: {scored} juzgadas, coste {cloud.meter.cost_usd} USD")
    else:
        labels = load_labels()
        reports = [
            load_judged(path) for path in sorted(CLOUD_REPORTS_DIR.glob("agent-calibration-*.json"))
        ]
        results = [agreement(name, items, labels) for name, items in reports]
        out = CLOUD_REPORTS_DIR / "agent-calibration.md"
        out.write_text(render_agreement(results, len(load_set())), encoding="utf-8", newline="\n")
        print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
