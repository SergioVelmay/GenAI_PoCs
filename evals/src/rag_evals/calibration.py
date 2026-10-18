"""Calibracion de los jueces LLM contra etiquetas humanas (fase 5, checkpoint 8->9).

Un juez solo puede ser un gate si coincide con un humano. Con las respuestas reales del sistema
casi todo es fiel por construccion (citas literales), asi que etiquetarlas no enseña nada: el
conjunto de calibracion mezcla respuestas correctas con respuestas estropeadas a proposito, de
forma determinista y sin LLM:

- `good`: la respuesta de referencia del golden dataset.
- `fabricated`: la referencia con la cifra cambiada (contradice los contextos).
- `unsupported`: la referencia mas una afirmacion inventada.
- `off_topic`: la referencia de otra pregunta.
- `non_answer`: una evasiva sin contenido.

Flujo (desde la raiz del repo):
    uv run python -m rag_evals.calibration build        # coste 0: conjunto + plantilla
    # Sergio rellena evals/calibration/labels-v1.csv a ciegas (sin ver el tipo)
    uv run python -m rag_evals.calibration judge --judge native --budget-usd 0.30   # con coste
    uv run python -m rag_evals.calibration agreement    # coste 0: acuerdo humano-juez
"""

import argparse
import asyncio
import csv
import json
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.llm.structured import StructuredOutputError
from headache_core.application.retrieval import RetrievalRequest
from headache_core.domain.evals import cohen_kappa
from headache_core.domain.retrieval import AccessScope
from rag_evals import EVALS_DIR
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, GoldenItem, load_golden
from rag_evals.golden import verify_corpus as verify_corpus_hash
from rag_evals.judges import JudgeError, JudgeSample, JudgeScores, RagJudge
from rag_evals.metering import BudgetExceededError
from rag_evals.retrieval_eval import CLOUD_REPORTS_DIR, local_settings
from retrieval.bootstrap import build_local_service, load_synthetic

Kind = Literal["good", "fabricated", "unsupported", "off_topic", "non_answer"]
Faithful = Literal["si", "no", "na"]

CALIBRATION_DIR = EVALS_DIR / "calibration"
SET_PATH = CALIBRATION_DIR / "calibration-v1.jsonl"
READABLE_PATH = CALIBRATION_DIR / "calibration-v1.md"
LABELS_PATH = CALIBRATION_DIR / "labels-v1.csv"
# Adjudicacion documentada tras ver al juez: se conserva tambien la version a ciegas.
ADJUDICATED_PATH = CALIBRATION_DIR / "labels-v1-adjudicated.csv"
# 20 muestras: 8 correctas y 12 estropeadas (las que un buen juez debe detectar).
KIND_PLAN: tuple[Kind, ...] = (
    ("good",) * 8 + ("fabricated",) * 4 + ("unsupported",) * 4 + ("off_topic",) * 2
) + ("non_answer",) * 2
SEED = 42
# Una afirmacion plausible pero ausente del corpus (el parking no aparece en ningun documento).
UNSUPPORTED_CLAIM = {
    "es": "Además, el parking del recinto es gratuito para todos los asistentes.",
    "en": "Parking at the venue is also free for every attendee.",
}
NON_ANSWER = {
    "es": "Depende de cada caso; consulta las condiciones en la web de Headache.",
    "en": "It depends on the case; please check the conditions on the Headache website.",
}
# Relevancia >= 2 (responde, aunque sea con ruido) cuenta como relevante al binarizar.
RELEVANT_FROM = 2
BUDGET_ERROR = "sin presupuesto"


class CalibrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Kind
    language: str
    question: str
    answer: str
    contexts: list[str]
    reference: str


@dataclass(frozen=True, slots=True)
class HumanLabel:
    id: str
    faithful: Faithful
    relevance: int


@dataclass(frozen=True, slots=True)
class JudgedItem:
    id: str
    kind: str
    scores: JudgeScores | None
    error: str | None


def _change_number(text: str) -> str | None:
    """Cambia la primera cifra de forma determinista: 48 -> 96, 100 -> 50, 15 -> 30."""
    match = re.search(r"\d+", text)
    if match is None:
        return None
    value = int(match.group())
    changed = value // 2 if value >= 100 else value * 2 if value else 7
    return f"{text[: match.start()]}{changed}{text[match.end() :]}"


def perturb(item: GoldenItem, kind: Kind, other: GoldenItem) -> str | None:
    """Respuesta del tipo pedido; None si el item no admite esa perturbacion."""
    reference = item.expected_answer or ""
    language = item.language if item.language in UNSUPPORTED_CLAIM else "en"
    match kind:
        case "good":
            return reference
        case "fabricated":
            return _change_number(reference)
        case "unsupported":
            return f"{reference} {UNSUPPORTED_CLAIM[language]}"
        case "off_topic":
            return other.expected_answer if other.topic != item.topic else None
        case "non_answer":
            return NON_ANSWER[language]


def _supported(item: GoldenItem, contexts: Sequence[str]) -> bool:
    """Solo items cuya evidencia esta en los contextos: si no, 'good' tampoco seria fiel."""
    text = " ".join(contexts).casefold()
    return any(evidence.casefold() in text for evidence in item.evidence)


async def build_set(
    items: Sequence[GoldenItem], *, corpus: Path = CORPUS_PATH
) -> list[CalibrationItem]:
    settings = local_settings(corpus)
    embeddings = HashingEmbedding(1024)
    records, parents = await load_synthetic(settings, embeddings)
    service = build_local_service(settings, records=records, parents=parents, embeddings=embeddings)
    candidates = [item for item in items if item.answerable and item.expected_answer]
    random.Random(SEED).shuffle(candidates)
    kinds = list(KIND_PLAN)
    random.Random(SEED).shuffle(kinds)
    chosen: list[CalibrationItem] = []
    used: set[str] = set()
    for kind in kinds:
        for position, item in enumerate(candidates):
            if item.id in used:
                continue
            other = candidates[(position + len(candidates) // 2) % len(candidates)]
            answer = perturb(item, kind, other)
            if answer is None:
                continue
            retrieved = await service.retrieve(
                RetrievalRequest(
                    item.question,
                    strategy="hybrid",
                    top_k=5,
                    candidate_k=20,
                    filters=dict(item.filters),
                    scope=AccessScope(),
                )
            )
            contexts = [result.record.text for result in retrieved.results]
            if not _supported(item, contexts):
                continue
            used.add(item.id)
            chosen.append(
                CalibrationItem(
                    id=f"cal-{len(chosen) + 1:02d}",
                    kind=kind,
                    language=item.language,
                    question=item.question,
                    answer=answer,
                    contexts=contexts,
                    reference=item.expected_answer or "",
                )
            )
            break
    if len(chosen) != len(KIND_PLAN):
        raise ValueError(f"Solo {len(chosen)} de {len(KIND_PLAN)} muestras validas")
    return chosen


def render_readable(items: Sequence[CalibrationItem]) -> str:
    """Vista para etiquetar a ciegas: no muestra el tipo ni la referencia."""
    lines = [
        "# Calibracion de jueces · conjunto v1",
        "",
        "Etiqueta cada muestra en `labels-v1.csv` **sin mirar el `.jsonl`** (lleva el tipo):",
        "",
        "- `faithful`: `si` si TODAS las afirmaciones de la respuesta estan respaldadas por los "
        "contextos, `no` si alguna no lo esta, `na` si la respuesta no afirma nada.",
        "- `relevance`: 0 no responde, 1 tangencial, 2 responde con ruido, 3 responde directa "
        "y completamente. Mide si responde a lo preguntado, **con independencia de que sea "
        "verdad** (eso ya lo mide `faithful`).",
        "",
    ]
    for item in items:
        lines += [f"## {item.id}", "", f"**Pregunta:** {item.question}", ""]
        lines += [f"**Respuesta:** {item.answer}", "", "**Contextos:**", ""]
        lines += [f"{index}. {' '.join(text.split())}" for index, text in enumerate(item.contexts)]
        lines.append("")
    return "\n".join(lines)


def write_set(items: Sequence[CalibrationItem], *, overwrite_labels: bool = False) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    SET_PATH.write_text(
        "".join(item.model_dump_json() + "\n" for item in items), encoding="utf-8", newline="\n"
    )
    READABLE_PATH.write_text(render_readable(items), encoding="utf-8", newline="\n")
    if LABELS_PATH.exists() and not overwrite_labels:
        return
    with LABELS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "faithful", "relevance", "notes"])
        writer.writerows([item.id, "", "", ""] for item in items)


def load_set(path: Path = SET_PATH) -> list[CalibrationItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [CalibrationItem.model_validate_json(line) for line in lines if line.strip()]


def load_labels(path: Path = LABELS_PATH) -> dict[str, HumanLabel]:
    """Solo filas completas; una etiqueta invalida es un error, no se ignora."""
    labels: dict[str, HumanLabel] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            faithful, relevance = row["faithful"].strip().lower(), row["relevance"].strip()
            if not faithful and not relevance:
                continue
            if faithful not in ("si", "no", "na") or relevance not in ("0", "1", "2", "3"):
                raise ValueError(f"Etiqueta invalida en {row['id']}: {faithful!r}, {relevance!r}")
            labels[row["id"]] = HumanLabel(row["id"], faithful, int(relevance))  # type: ignore[arg-type]
    return labels


async def judge_set(items: Sequence[CalibrationItem], judge: RagJudge) -> list[JudgedItem]:
    """Al agotar el presupuesto se para, pero se conserva lo ya juzgado (ya esta pagado)."""
    judged: list[JudgedItem] = []
    for position, item in enumerate(items):
        sample = JudgeSample(item.question, item.answer, tuple(item.contexts), item.reference)
        try:
            judged.append(JudgedItem(item.id, item.kind, await judge.score(sample), None))
        except (JudgeError, StructuredOutputError) as error:
            judged.append(JudgedItem(item.id, item.kind, None, str(error)))
        except BudgetExceededError:
            judged += [
                JudgedItem(rest.id, rest.kind, None, BUDGET_ERROR) for rest in items[position:]
            ]
            break
    return judged


def judge_faithful(scores: JudgeScores | None) -> Faithful | None:
    """El juez da por fiel solo si TODAS las afirmaciones estan respaldadas (como el humano)."""
    if scores is None:
        return None
    if scores.faithfulness is None:
        return "na"
    return "si" if scores.faithfulness >= 0.999 else "no"


def judge_relevance(scores: JudgeScores | None) -> float | None:
    return None if scores is None else scores.answer_relevancy


EXPECTED_FAITHFUL: Mapping[str, Faithful] = {
    "good": "si",
    "fabricated": "no",
    "unsupported": "no",
    "off_topic": "no",
    "non_answer": "na",
}


@dataclass(frozen=True, slots=True)
class Agreement:
    judge: str
    samples: int
    judge_errors: int
    faithful_accuracy: float | None
    faithful_kappa: float | None
    relevance_mae: float | None
    relevance_accuracy: float | None
    relevance_kappa: float | None
    detected: dict[str, str]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def agreement(
    judge: str,
    judged: Sequence[JudgedItem],
    labels: Mapping[str, HumanLabel],
) -> Agreement:
    """Acuerdo humano-juez (solo muestras etiquetadas y puntuadas) y deteccion por tipo."""
    pairs = [(item, labels[item.id]) for item in judged if item.scores and item.id in labels]
    human_f = [label.faithful for _, label in pairs]
    judge_f = [judge_faithful(item.scores) or "na" for item, _ in pairs]
    rel = [(judge_relevance(item.scores), label.relevance) for item, label in pairs]
    rel_pairs = [(value, human) for value, human in rel if value is not None]
    human_rel = [human >= RELEVANT_FROM for _, human in rel_pairs]
    judge_rel = [value >= RELEVANT_FROM / 3 for value, _ in rel_pairs]
    detected: dict[str, str] = {}
    for kind, expected in EXPECTED_FAITHFUL.items():
        of_kind = [item for item in judged if item.kind == kind and item.scores is not None]
        hits = sum(judge_faithful(item.scores) == expected for item in of_kind)
        detected[kind] = f"{hits}/{len(of_kind)}"
    return Agreement(
        judge=judge,
        samples=len(pairs),
        judge_errors=sum(item.error is not None for item in judged),
        faithful_accuracy=_round(
            sum(h == j for h, j in zip(human_f, judge_f, strict=True)) / len(pairs)
        )
        if pairs
        else None,
        faithful_kappa=_round(cohen_kappa(human_f, judge_f)),
        relevance_mae=_round(
            sum(abs(value - human / 3) for value, human in rel_pairs) / len(rel_pairs)
        )
        if rel_pairs
        else None,
        relevance_accuracy=_round(
            sum(h == j for h, j in zip(human_rel, judge_rel, strict=True)) / len(rel_pairs)
        )
        if rel_pairs
        else None,
        relevance_kappa=_round(cohen_kappa(human_rel, judge_rel)),
        detected=detected,
    )


def _report_path(judge: str) -> Path:
    return CLOUD_REPORTS_DIR / f"calibration-{judge}.json"


def write_judged(judge: str, judged: Sequence[JudgedItem], cost: Mapping[str, object]) -> Path:
    CLOUD_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _report_path(judge)
    payload = {"judge": judge, "cost": cost, "items": [asdict(item) for item in judged]}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def load_judged(path: Path) -> tuple[str, list[JudgedItem]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = [
        JudgedItem(
            raw["id"],
            raw["kind"],
            JudgeScores(**raw["scores"]) if raw["scores"] is not None else None,
            raw["error"],
        )
        for raw in payload["items"]
    ]
    return str(payload["judge"]), items


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_agreement(sections: Mapping[str, Sequence[Agreement]]) -> str:
    """Una tabla por version de etiquetas (a ciegas y, si existe, adjudicada)."""
    kinds = list(EXPECTED_FAITHFUL)
    lines = [
        "# Calibracion de jueces: acuerdo humano-juez",
        "",
        f"- Conjunto `calibration-v1` ({len(KIND_PLAN)} muestras), etiquetado por un humano "
        "a ciegas; la adjudicacion corrige despistes y unifica el criterio, documentado en "
        "`labels-v1-adjudicated.csv`.",
        "- Fiel segun el juez = faithfulness 1 (todas las afirmaciones respaldadas). Relevante "
        f"= relevancia >= {RELEVANT_FROM}/3. Kappa de Cohen: 1 acuerdo total, 0 como el azar.",
        "- Deteccion: muestras de cada tipo en las que el juez da la fidelidad esperada "
        "(no depende de las etiquetas humanas).",
    ]
    for title, results in sections.items():
        lines += [
            "",
            f"## {title}",
            "",
            "| Juez | Muestras | Errores | Fiel: acierto | Fiel: kappa | Relevancia: MAE "
            "| Relevante: acierto | Relevante: kappa | " + " | ".join(kinds) + " |",
            "|---|---:|---:|---:|---:|---:|---:|---:|" + "---:|" * len(kinds),
        ]
        for result in results:
            lines.append(
                f"| {result.judge} | {result.samples} | {result.judge_errors} | "
                f"{_fmt(result.faithful_accuracy)} | {_fmt(result.faithful_kappa)} | "
                f"{_fmt(result.relevance_mae)} | {_fmt(result.relevance_accuracy)} | "
                f"{_fmt(result.relevance_kappa)} | "
                + " | ".join(result.detected[kind] for kind in kinds)
                + " |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Conjunto y plantilla de etiquetas (coste 0)")
    build.add_argument("--overwrite-labels", action="store_true")
    judge = commands.add_parser("judge", help="Puntua el conjunto con un juez (con coste)")
    judge.add_argument("--judge", choices=["native", "ragas", "deepeval"], required=True)
    judge.add_argument("--budget-usd", type=Decimal, required=True)
    commands.add_parser("agreement", help="Acuerdo humano-juez (coste 0)")
    args = parser.parse_args()

    if args.command == "build":
        meta, items = load_golden(DATASET_PATH)
        verify_corpus_hash(meta, CORPUS_PATH)
        chosen = asyncio.run(build_set(items))
        write_set(chosen, overwrite_labels=args.overwrite_labels)
        print(f"{len(chosen)} muestras: {SET_PATH}, {READABLE_PATH} y {LABELS_PATH}")
    elif args.command == "judge":
        from rag_evals.generation_eval import _gateway, _judge_embeddings, build_judge

        _, judge_llm, meter = _gateway(args.budget_usd)
        embeddings = _judge_embeddings() if args.judge == "ragas" else None
        rag_judge = build_judge(args.judge, judge_llm, embeddings)
        if rag_judge is None:
            raise ValueError("Juez desconocido")
        judged = asyncio.run(judge_set(load_set(), rag_judge))
        path = write_judged(args.judge, judged, asdict(meter.snapshot()))
        print(
            f"{path}: {sum(i.error is None for i in judged)} puntuadas, coste {meter.cost_usd} USD"
        )
    else:
        reports = [
            load_judged(path) for path in sorted(CLOUD_REPORTS_DIR.glob("calibration-*.json"))
        ]
        versions = {"Etiquetas a ciegas": LABELS_PATH, "Etiquetas adjudicadas": ADJUDICATED_PATH}
        sections = {
            title: [agreement(name, items, load_labels(path)) for name, items in reports]
            for title, path in versions.items()
            if path.exists()
        }
        out = CLOUD_REPORTS_DIR / "calibration.md"
        out.write_text(render_agreement(sections), encoding="utf-8", newline="\n")
        print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
