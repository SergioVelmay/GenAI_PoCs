"""Fidelidad de las respuestas del carril A (Foundry IQ / Bedrock KB) con el juez calibrado.

Las respuestas gestionadas son texto redactado por el modelo del servicio, no citas literales: la
fidelidad ya no esta garantizada por construccion y hay que medirla. El juez nativo (calibrado
contra etiquetas humanas, kappa 0,82) juzga cada respuesta frente a los textos que ella misma
cita; una respuesta sin citas no tiene respaldo y cuenta como no fiel sin gastar juez.

Uso (con coste: AI Search encendido, agente aprovisionado y perfil de `az` aislado):
    uv run python -m rag_evals.managed_eval --budget-usd 0.30 [--answerable 10 --unanswerable 5]
"""

import argparse
import asyncio
import json
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import fmean, median
from time import perf_counter
from typing import Literal

from headache_core.application.llm.structured import StructuredOutputError
from headache_core.application.retrieval import InvalidCitationError
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.llm import LLMContentFilterError
from headache_core.ports.retrieval import ManagedKnowledgePort, ManagedRagResponse
from rag_evals.golden import DATASET_PATH, GoldenItem, load_golden
from rag_evals.judges import JudgeError, JudgeSample, JudgeScores, RagJudge
from rag_evals.retrieval_eval import CLOUD_REPORTS_DIR

ManagedEngine = Literal["foundry_iq", "bedrock_kb"]
Status = Literal["answered", "rejected", "blocked"]
SEED = 42
NO_CITATIONS = "respuesta sin citas: sin respaldo"


@dataclass(frozen=True, slots=True)
class ManagedOutcome:
    id: str
    question_type: str
    answerable: bool
    abstained: bool
    answer: str
    cited_documents: tuple[str, ...]
    leaks: tuple[str, ...]
    latency_ms: float
    scores: JudgeScores | None
    judge_error: str | None
    # rejected: guardarrail de citas del adaptador; blocked: filtro de contenido del proveedor.
    status: Status = "answered"
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedSummary:
    engine: str
    answerable: int
    unanswerable: int
    answered: int
    correct_abstentions: int
    over_answers: list[str]
    faithful_rate: float | None
    mean_faithfulness: float | None
    unfaithful: list[str]
    judge_errors: int
    acl_leaks: int
    latency_p50_ms: float | None
    rejected: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


def sample_items(
    items: Sequence[GoldenItem], answerable: int, unanswerable: int
) -> list[GoldenItem]:
    """Misma muestra que la validacion de la fase 4: sin filtros (el agente no los admite)."""
    rng = random.Random(SEED)
    plain = [item for item in items if not item.filters]
    chosen = rng.sample([item for item in plain if item.relevance], answerable)
    return chosen + rng.sample([item for item in plain if not item.relevance], unanswerable)


def contexts_of(response: ManagedRagResponse) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Textos y documentos citados, sin duplicados y en orden de aparicion."""
    texts: dict[str, str] = {}
    documents: dict[str, None] = {}
    for citation in response.citations:
        for reference in citation.references:
            texts.setdefault(reference.source_id, reference.text)
            documents.setdefault(reference.document_id, None)
    return tuple(texts.values()), tuple(documents)


async def evaluate_managed(
    knowledge: ManagedKnowledgePort,
    items: Sequence[GoldenItem],
    judge: RagJudge,
    *,
    top_k: int = 5,
    scope: AccessScope | None = None,
) -> list[ManagedOutcome]:
    effective_scope = scope or AccessScope()
    outcomes: list[ManagedOutcome] = []
    for item in items:
        started = perf_counter()
        try:
            response = await knowledge.answer(
                item.question, top_k=top_k, filters={}, scope=effective_scope
            )
        except (InvalidCitationError, LLMContentFilterError) as exc:
            # Fail-closed del servicio: no llega respuesta al usuario. Se cuenta, no aborta.
            status: Status = "rejected" if isinstance(exc, InvalidCitationError) else "blocked"
            outcomes.append(
                ManagedOutcome(
                    id=item.id,
                    question_type=item.question_type,
                    answerable=item.answerable,
                    abstained=True,
                    answer="",
                    cited_documents=(),
                    leaks=(),
                    latency_ms=round((perf_counter() - started) * 1000, 1),
                    scores=None,
                    judge_error=None,
                    status=status,
                    detail=str(exc)[:200],
                )
            )
            continue
        elapsed = (perf_counter() - started) * 1000
        contexts, documents = contexts_of(response)
        scores: JudgeScores | None = None
        error: str | None = None
        if not response.abstained:
            if not contexts:
                error = NO_CITATIONS
            else:
                try:
                    scores = await judge.score(
                        JudgeSample(
                            item.question,
                            response.answer,
                            contexts,
                            item.expected_answer if item.answerable else None,
                        )
                    )
                except (JudgeError, StructuredOutputError) as exc:
                    error = str(exc)
        outcomes.append(
            ManagedOutcome(
                id=item.id,
                question_type=item.question_type,
                answerable=item.answerable,
                abstained=response.abstained,
                answer=response.answer,
                cited_documents=documents,
                leaks=tuple(d for d in documents if d in item.forbidden_document_ids),
                latency_ms=round(elapsed, 1),
                scores=scores,
                judge_error=error,
            )
        )
    return outcomes


def _is_faithful(outcome: ManagedOutcome) -> bool:
    return outcome.scores is not None and (outcome.scores.faithfulness or 0.0) >= 0.999


def summarize_managed(engine: str, outcomes: Sequence[ManagedOutcome]) -> ManagedSummary:
    answered = [o for o in outcomes if not o.abstained]
    # Fiel = juez con todas las afirmaciones respaldadas; sin citas cuenta como no fiel.
    gradable = [o for o in answered if o.scores is not None or o.judge_error == NO_CITATIONS]
    faithfulness = [
        o.scores.faithfulness
        for o in answered
        if o.scores is not None and o.scores.faithfulness is not None
    ]
    latencies = [o.latency_ms for o in outcomes]
    return ManagedSummary(
        engine=engine,
        answerable=sum(o.answerable for o in outcomes),
        unanswerable=sum(not o.answerable for o in outcomes),
        answered=len(answered),
        correct_abstentions=sum(o.abstained for o in outcomes if not o.answerable),
        over_answers=[o.id for o in answered if not o.answerable],
        faithful_rate=round(fmean(_is_faithful(o) for o in gradable), 3) if gradable else None,
        mean_faithfulness=round(fmean(faithfulness), 3) if faithfulness else None,
        unfaithful=[o.id for o in gradable if not _is_faithful(o)],
        judge_errors=sum(
            o.judge_error is not None and o.judge_error != NO_CITATIONS for o in outcomes
        ),
        acl_leaks=sum(len(o.leaks) for o in outcomes),
        latency_p50_ms=round(median(latencies), 1) if latencies else None,
        rejected=[o.id for o in outcomes if o.status == "rejected"],
        blocked=[o.id for o in outcomes if o.status == "blocked"],
    )


def render_markdown(
    summary: ManagedSummary, outcomes: Sequence[ManagedOutcome], cost_usd: str
) -> str:
    lines = [
        f"# Fidelidad de las respuestas gestionadas ({summary.engine})",
        "",
        "- Juez nativo calibrado (kappa 0,82 en fidelidad) sobre los textos que cita cada "
        "respuesta. Fiel = todas las afirmaciones respaldadas; sin citas = no fiel.",
        f"- Coste del juez: {cost_usd} USD (el modelo del agente se factura aparte).",
        f"- Rechazadas por el guardarrail de citas: {', '.join(summary.rejected) or '-'}. "
        f"Bloqueadas por el filtro de contenido: {', '.join(summary.blocked) or '-'}. "
        "En ambos casos no llega respuesta al usuario (cuentan como abstencion).",
        "",
        "| Respondidas | Fieles | Faithfulness media | No fieles | Abstenciones correctas "
        "| Sobre-respuestas | Fugas ACL | Errores juez | p50 ms |",
        "|---:|---:|---:|---|---:|---|---:|---:|---:|",
        f"| {summary.answered} | {summary.faithful_rate} | {summary.mean_faithfulness} | "
        f"{', '.join(summary.unfaithful) or '-'} | "
        f"{summary.correct_abstentions}/{summary.unanswerable} | "
        f"{', '.join(summary.over_answers) or '-'} | {summary.acl_leaks} | "
        f"{summary.judge_errors} | {summary.latency_p50_ms} |",
        "",
        "## Respuestas",
        "",
        "| Id | Abstiene | Faithfulness | Documentos citados | Respuesta |",
        "|---|---|---:|---|---|",
    ]
    for outcome in outcomes:
        faithfulness = (
            "-"
            if outcome.scores is None or outcome.scores.faithfulness is None
            else f"{outcome.scores.faithfulness:.2f}"
        )
        if outcome.judge_error is not None:
            faithfulness = outcome.judge_error
        if outcome.status != "answered":
            faithfulness = outcome.status
        answer = " ".join(outcome.answer.split()).replace("|", "/")[:160]
        lines.append(
            f"| {outcome.id} | {'si' if outcome.abstained else 'no'} | {faithfulness} | "
            f"{', '.join(outcome.cited_documents) or '-'} | {answer} |"
        )
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> Path:
    from headache_core.config import Settings
    from rag_evals.generation_eval import _gateway, build_judge
    from retrieval.cloud import build_cloud_parts
    from retrieval.settings import RetrievalSettings

    _, items = load_golden(DATASET_PATH)
    sample = sample_items(items, args.answerable, args.unanswerable)
    _, judge_llm, meter = _gateway(args.budget_usd)
    judge = build_judge("native", judge_llm)
    if judge is None:
        raise ValueError("Juez no disponible")
    parts = await build_cloud_parts(RetrievalSettings(managed=args.engine), Settings())
    try:
        if parts.managed is None:
            raise ValueError(f"{args.engine} no esta configurado")
        outcomes = await evaluate_managed(parts.managed, sample, judge)
    finally:
        await parts.aclose()
    summary = summarize_managed(args.engine, outcomes)
    CLOUD_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = CLOUD_REPORTS_DIR / f"managed-{args.engine}"
    cost = asdict(meter.snapshot())
    payload = {"summary": asdict(summary), "cost": cost, "outcomes": [asdict(o) for o in outcomes]}
    base.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    markdown = render_markdown(summary, outcomes, str(cost["cost_usd"]))
    base.with_suffix(".md").write_text(markdown, encoding="utf-8", newline="\n")
    return base.with_suffix(".md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["foundry_iq", "bedrock_kb"], default="foundry_iq")
    parser.add_argument("--answerable", type=int, default=10)
    parser.add_argument("--unanswerable", type=int, default=5)
    parser.add_argument("--budget-usd", type=Decimal, required=True)
    args = parser.parse_args()
    path = asyncio.run(_run(args))
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
