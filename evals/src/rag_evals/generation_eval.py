"""Evals de generacion (fase 5.3): citas, abstencion, fugas de ACL y, opcionalmente, jueces LLM.

Por defecto es 100 % local y sin coste: evalua el modo extractivo (las citas son los propios
chunks recuperados). El modo `llm` y los jueces llaman al gateway y EXIGEN `--budget-usd`.

Uso (desde la raiz del repo):
    uv run python -m rag_evals.generation_eval [--engine native] [--strategy hybrid]
    uv run python -m rag_evals.generation_eval --mode llm --judge native --limit 20 \
        --budget-usd 0.50          # con coste: pedir confirmacion antes
"""

import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Literal

from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import (
    InvalidCitationError,
    RetrievalRequest,
    RetrievalService,
)
from headache_core.domain.evals import percentile, ratio
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.llm import LLMPort
from headache_core.ports.retrieval import RetrievalStrategy
from headache_core.ports.vector_store import MetadataValue
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, DatasetMeta, GoldenItem, load_golden
from rag_evals.golden import verify_corpus as verify_corpus_hash
from rag_evals.judges import JudgeSample, JudgeScores, NativeJudge, RagJudge
from rag_evals.metering import CostMeter, MeterSnapshot
from rag_evals.retrieval_eval import EMBEDDINGS, REPORTS_DIR, Engine, local_settings
from retrieval.bootstrap import build_local_service, load_synthetic

Mode = Literal["extractive", "llm"]
JudgeName = Literal["none", "native", "ragas", "deepeval"]
# Solo estrategias sin LLM en el retrieval: el coste queda acotado a generacion y juez.
GENERATION_STRATEGIES: tuple[RetrievalStrategy, ...] = (
    "bm25",
    "vector",
    "hybrid",
    "rerank",
    "parent-child",
    "auto-merge",
)
# El auditor lee la fuente sin filtrar y aplica el scope del usuario por su cuenta.
AUDIT_SCOPE = AccessScope(allowed_acls=("public", "internal"))
JUDGE_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    item: GoldenItem
    abstained: bool
    rejected: bool
    citations: int
    valid_citations: int
    relevant_citations: int
    has_evidence: bool
    leaks: tuple[str, ...]
    latency_ms: float
    judge: JudgeScores | None = None


@dataclass(frozen=True, slots=True)
class TypeScores:
    questions: int
    abstention_rate: float
    evidence_rate: float


@dataclass(frozen=True, slots=True)
class GenerationSummary:
    engine: str
    strategy: str
    mode: str
    judge: str
    answerable: int
    unanswerable: int
    answer_evidence: float | None
    false_abstention: float | None
    correct_abstention: float | None
    citation_validity: float | None
    citation_precision: float | None
    citations_per_answer: float | None
    rejected: int
    acl_leaks: int
    latency_p50_ms: float
    latency_p95_ms: float
    by_type: dict[str, TypeScores]
    judged: int
    judge_scores: dict[str, float | None]
    cost: MeterSnapshot | None = None
    missed: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GenerationReport:
    dataset: DatasetMeta
    top_k: int
    candidate_k: int
    embeddings: str
    runs: list[GenerationSummary]

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset.model_dump(),
            "top_k": self.top_k,
            "candidate_k": self.candidate_k,
            "embeddings": self.embeddings,
            "runs": [asdict(run) for run in self.runs],
        }


def _has_evidence(item: GoldenItem, answer: str) -> bool:
    return any(evidence.casefold() in answer.casefold() for evidence in item.evidence)


async def evaluate_generation(
    service: RetrievalService,
    items: Sequence[GoldenItem],
    *,
    strategy: RetrievalStrategy,
    top_k: int,
    candidate_k: int,
    mode: Mode = "extractive",
    judge: RagJudge | None = None,
    scope: AccessScope | None = None,
) -> list[GenerationOutcome]:
    effective_scope = scope or AccessScope()
    outcomes: list[GenerationOutcome] = []
    for item in items:
        filters: dict[str, MetadataValue] = dict(item.filters)
        request = RetrievalRequest(
            item.question,
            strategy=strategy,
            top_k=top_k,
            candidate_k=candidate_k,
            filters=filters,
            scope=effective_scope,
        )
        started = perf_counter()
        try:
            response = await service.answer(request, generate=mode == "llm")
        except InvalidCitationError:
            # Fail-closed: el servicio no devuelve citas inventadas; cuenta como no respuesta.
            elapsed = (perf_counter() - started) * 1000
            outcomes.append(GenerationOutcome(item, False, True, 0, 0, 0, False, (), elapsed))
            continue
        elapsed = (perf_counter() - started) * 1000
        valid = relevant = 0
        leaks: set[str] = set()
        for citation in response.citations:
            record = await service.source(citation.source_id, scope=AUDIT_SCOPE)
            document = citation.document_id
            if record is None:
                continue
            if not effective_scope.permits(record.metadata) or (
                document in item.forbidden_document_ids
            ):
                leaks.add(document)
            if citation.quote in record.text:
                valid += 1
                relevant += document in item.relevance
        judged: JudgeScores | None = None
        if judge is not None and not response.abstained:
            # Los contextos son los mismos top_k que vio el generador (retrieval determinista).
            retrieved = await service.retrieve(request)
            judged = await judge.score(
                JudgeSample(
                    question=item.question,
                    answer=response.answer,
                    contexts=tuple(result.record.text for result in retrieved.results),
                    reference=item.expected_answer if item.answerable else None,
                )
            )
        outcomes.append(
            GenerationOutcome(
                item,
                response.abstained,
                False,
                len(response.citations),
                valid,
                relevant,
                not response.abstained and _has_evidence(item, response.answer),
                tuple(sorted(leaks)),
                elapsed,
                judged,
            )
        )
    return outcomes


def _mean(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(fmean(present), 4) if present else None


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def summarize_generation(
    engine: str,
    strategy: str,
    mode: str,
    judge: str,
    outcomes: Sequence[GenerationOutcome],
    cost: MeterSnapshot | None = None,
) -> GenerationSummary:
    answerable = [outcome for outcome in outcomes if outcome.item.answerable]
    unanswerable = [outcome for outcome in outcomes if not outcome.item.answerable]
    answered = [o for o in answerable if not o.abstained and not o.rejected]
    groups: dict[str, list[GenerationOutcome]] = defaultdict(list)
    for outcome in outcomes:
        groups[outcome.item.question_type].append(outcome)
    citations = sum(o.citations for o in outcomes)
    valid_answerable = sum(o.valid_citations for o in answerable)
    judged = [o.judge for o in outcomes if o.judge is not None]
    latencies = [o.latency_ms for o in outcomes] or [0.0]
    return GenerationSummary(
        engine=engine,
        strategy=strategy,
        mode=mode,
        judge=judge,
        answerable=len(answerable),
        unanswerable=len(unanswerable),
        answer_evidence=_rounded(ratio([o.has_evidence for o in answerable])),
        false_abstention=_rounded(ratio([o.abstained for o in answerable])),
        correct_abstention=_rounded(ratio([o.abstained for o in unanswerable])),
        citation_validity=_rounded(sum(o.valid_citations for o in outcomes) / citations)
        if citations
        else None,
        citation_precision=_rounded(
            sum(o.relevant_citations for o in answerable) / valid_answerable
        )
        if valid_answerable
        else None,
        citations_per_answer=_mean([float(o.citations) for o in answered]),
        rejected=sum(o.rejected for o in outcomes),
        acl_leaks=sum(len(o.leaks) for o in outcomes),
        latency_p50_ms=round(percentile(latencies, 50), 2),
        latency_p95_ms=round(percentile(latencies, 95), 2),
        by_type={
            key: TypeScores(
                questions=len(group),
                abstention_rate=round(fmean(o.abstained for o in group), 4),
                evidence_rate=round(fmean(o.has_evidence for o in group), 4),
            )
            for key, group in sorted(groups.items())
        },
        judged=len(judged),
        judge_scores={
            metric: _mean([getattr(scores, metric) for scores in judged])
            for metric in JUDGE_METRICS
        },
        cost=cost,
        missed=[o.item.id for o in answerable if not o.has_evidence],
    )


def build_judge(name: JudgeName, llm: LLMPort) -> RagJudge | None:
    if name == "none":
        return None
    if name == "native":
        return NativeJudge(llm)
    if name == "ragas":
        from rag_evals.frameworks.ragas_judge import RagasJudge

        # Limitacion local: embeddings hashing (lexicos) en AnswerRelevancy de RAGAS.
        return RagasJudge(llm, HashingEmbedding(1024))
    from rag_evals.frameworks.deepeval_judge import DeepEvalJudge

    return DeepEvalJudge(llm)


async def run_generation_eval(
    items: Sequence[GoldenItem],
    meta: DatasetMeta,
    *,
    corpus: Path = CORPUS_PATH,
    engines: Sequence[Engine] = ("native", "llamaindex"),
    strategies: Sequence[RetrievalStrategy] = ("hybrid",),
    top_k: int = 5,
    candidate_k: int = 20,
    mode: Mode = "extractive",
    judge: JudgeName = "none",
    llm: LLMPort | None = None,
    meter: CostMeter | None = None,
) -> GenerationReport:
    verify_corpus_hash(meta, corpus)
    if (mode == "llm" or judge != "none") and llm is None:
        raise ValueError("El modo llm y los jueces necesitan un LLM (con presupuesto)")
    if not engines or not strategies:
        raise ValueError("Se necesita al menos un motor y una estrategia")
    if invalid := set(strategies) - set(GENERATION_STRATEGIES):
        raise ValueError(f"Estrategias con LLM en el retrieval no admitidas: {sorted(invalid)}")
    settings = local_settings(corpus)
    embeddings = HashingEmbedding(1024)
    records, parents = await load_synthetic(settings, embeddings)
    rag_judge = build_judge(judge, llm) if llm is not None else None
    runs: list[GenerationSummary] = []
    for engine in engines:
        service = build_local_service(
            settings.model_copy(update={"engine": engine}),
            records=records,
            parents=parents,
            embeddings=embeddings,
            llm=llm,
        )
        for strategy in strategies:
            if strategy == "auto-merge" and engine != "llamaindex":
                continue
            outcomes = await evaluate_generation(
                service,
                items,
                strategy=strategy,
                top_k=top_k,
                candidate_k=candidate_k,
                mode=mode,
                judge=rag_judge,
            )
            snapshot = meter.snapshot() if meter is not None else None
            runs.append(summarize_generation(engine, strategy, mode, judge, outcomes, snapshot))
    if not runs:
        raise ValueError("Ninguna combinacion motor/estrategia seleccionada")
    return GenerationReport(meta, top_k, candidate_k, EMBEDDINGS, runs)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_markdown(report: GenerationReport) -> str:
    lines = [
        "# Evals de generacion (fase 5.3)",
        "",
        f"- Golden dataset v{report.dataset.version}: {report.dataset.items} preguntas "
        f"({report.dataset.by_type}); corpus sha256 `{report.dataset.corpus_sha256[:12]}`.",
        f"- top_k={report.top_k}, candidate_k={report.candidate_k}, "
        f"embeddings `{report.embeddings}`.",
        "- `extractive`: la respuesta son los chunks recuperados citados (coste 0). "
        "`llm`: el modelo selecciona citas literales; el servicio rechaza citas inventadas.",
        "- Evidencia = algun literal esperado aparece en la respuesta. Precision de citas = "
        "citas validas cuyo documento es gold (solo preguntas con respuesta).",
        "",
        "## Resumen",
        "",
        "| Motor | Estrategia | Modo | Evidencia | Abstencion falsa | Abstencion correcta "
        "| Validez citas | Precision citas | Citas/resp. | Rechazadas | Fugas ACL "
        "| p50 ms | p95 ms | Coste acumulado USD |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in report.runs:
        cost = run.cost.cost_usd if run.cost is not None else "0"
        lines.append(
            f"| {run.engine} | {run.strategy} | {run.mode} | {_fmt(run.answer_evidence)} | "
            f"{_fmt(run.false_abstention)} | {_fmt(run.correct_abstention)} | "
            f"{_fmt(run.citation_validity)} | {_fmt(run.citation_precision)} | "
            f"{_fmt(run.citations_per_answer)} | {run.rejected} | {run.acl_leaks} | "
            f"{run.latency_p50_ms:.1f} | {run.latency_p95_ms:.1f} | {cost} |"
        )
    types = sorted({key for run in report.runs for key in run.by_type})
    lines += [
        "",
        "## Abstencion / evidencia por tipo de pregunta",
        "",
        "| Motor | Estrategia | " + " | ".join(f"{key} (n)" for key in types) + " |",
        "|---|---|" + "---:|" * len(types),
    ]
    for run in report.runs:
        cells = [
            f"{run.by_type[key].abstention_rate:.2f} / {run.by_type[key].evidence_rate:.2f} "
            f"({run.by_type[key].questions})"
            if key in run.by_type
            else "-"
            for key in types
        ]
        lines.append(f"| {run.engine} | {run.strategy} | " + " | ".join(cells) + " |")
    judged = [run for run in report.runs if run.judge != "none"]
    if judged:
        lines += [
            "",
            "## Jueces LLM",
            "",
            "| Motor | Estrategia | Juez | Muestras | " + " | ".join(JUDGE_METRICS) + " |",
            "|---|---|---|---:|" + "---:|" * len(JUDGE_METRICS),
        ]
        for run in judged:
            scores = " | ".join(_fmt(run.judge_scores[metric]) for metric in JUDGE_METRICS)
            lines.append(
                f"| {run.engine} | {run.strategy} | {run.judge} | {run.judged} | {scores} |"
            )
    return "\n".join(lines) + "\n"


def write_report(
    report: GenerationReport, out_dir: Path = REPORTS_DIR, name: str = "generation"
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / f"{name}.json", out_dir / f"{name}.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path


def stratified_sample(items: Sequence[GoldenItem], limit: int) -> list[GoldenItem]:
    """Primeras N por turnos entre tipos de pregunta: una muestra barata pero representativa."""
    if limit < 1:
        raise ValueError("limit debe ser positivo")
    groups: dict[str, list[GoldenItem]] = defaultdict(list)
    for item in items:
        groups[item.question_type].append(item)
    queues = [group for _, group in sorted(groups.items())]
    sample: list[GoldenItem] = []
    for position in range(max(map(len, queues), default=0)):
        sample += [group[position] for group in queues if position < len(group)]
    return sample[:limit]


def _gateway(budget: Decimal) -> tuple[LLMPort, CostMeter]:
    from headache_core.adapters.llm_factory import build_gateway, build_tracer
    from headache_core.config import Settings

    settings = Settings()
    meter = CostMeter(budget_usd=budget, forward_to=build_tracer(settings))
    return build_gateway(settings, tracer=meter), meter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument("--engine", action="append", choices=["native", "llamaindex"])
    parser.add_argument("--strategy", action="append", choices=GENERATION_STRATEGIES)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--mode", choices=["extractive", "llm"], default="extractive")
    parser.add_argument("--judge", choices=["none", "native", "ragas", "deepeval"], default="none")
    parser.add_argument("--limit", type=int, help="Muestra estratificada de N (acota el coste)")
    parser.add_argument("--budget-usd", type=Decimal, help="Obligatorio si hay llamadas al LLM")
    args = parser.parse_args()
    needs_llm = args.mode == "llm" or args.judge != "none"
    if needs_llm and args.budget_usd is None:
        parser.error("--mode llm y --judge requieren --budget-usd (llamadas con coste)")
    meta, items = load_golden(args.dataset)
    if args.limit is not None:
        items = stratified_sample(items, args.limit)
    llm, meter = _gateway(args.budget_usd) if needs_llm else (None, None)
    report = asyncio.run(
        run_generation_eval(
            items,
            meta,
            corpus=args.corpus,
            engines=args.engine or ["native", "llamaindex"],
            strategies=args.strategy or ["hybrid"],
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            mode=args.mode,
            judge=args.judge,
            llm=llm,
            meter=meter,
        )
    )
    name = "generation" if not needs_llm else f"generation-{args.mode}-{args.judge}"
    json_path, md_path = write_report(report, args.out, name)
    print(f"Informe: {md_path} y {json_path}")
    for run in report.runs:
        print(
            f"{run.engine:>10} {run.strategy:>12} {run.mode:>10}  "
            f"evidencia={_fmt(run.answer_evidence)} abst_ok={_fmt(run.correct_abstention)} "
            f"prec_citas={_fmt(run.citation_precision)} fugas={run.acl_leaks}"
        )


if __name__ == "__main__":
    main()
