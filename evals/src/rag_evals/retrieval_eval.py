"""Evals de retrieval sin coste: carriles B (nativo) y C (LlamaIndex) sobre el golden dataset.

Uso (desde la raiz del repo):
    uv run python -m rag_evals.retrieval_eval [--engine native] [--strategy bm25] [--top-k 5]
"""

import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Literal

from headache_core.adapters.memory import HashingEmbedding
from headache_core.application.retrieval import RetrievalRequest, RetrievalService
from headache_core.domain.evals import (
    hit_at_k,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
    unique_in_order,
)
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.retrieval import RetrievalStrategy
from headache_core.ports.vector_store import MetadataValue, VectorRecord
from rag_evals import EVALS_DIR
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, DatasetMeta, GoldenItem, load_golden
from rag_evals.golden import verify_corpus as verify_corpus_hash
from retrieval.bootstrap import build_local_service, load_synthetic
from retrieval.settings import RetrievalSettings

Engine = Literal["native", "llamaindex", "azure", "azure-semantic"]
LOCAL_ENGINES: tuple[Engine, ...] = ("native", "llamaindex")
# Con coste (embeddings de Foundry por consulta) y solo si se piden con --engine: nunca en CI.
CLOUD_ENGINES: tuple[Engine, ...] = ("azure", "azure-semantic")
ENGINE_STRATEGIES: dict[Engine, tuple[RetrievalStrategy, ...]] = {
    "native": ("bm25", "vector", "hybrid", "rerank", "parent-child"),
    "llamaindex": ("bm25", "vector", "hybrid", "rerank", "parent-child", "auto-merge"),
    # Índice `knowledge` del carril B en AI Search (corpus clasificado con ACL explícita).
    "azure": ("bm25", "vector", "hybrid"),
    # Híbrida + Semantic Ranker (reordena hasta 50 candidatos dentro del servicio).
    "azure-semantic": ("hybrid",),
}
REPORTS_DIR = EVALS_DIR / "reports"
CLOUD_REPORTS_DIR = REPORTS_DIR / "azure"
EMBEDDINGS = "hashing-1024"
CLOUD_EMBEDDINGS = "azure:text-embedding-3-small-1024"


@dataclass(frozen=True, slots=True)
class Outcome:
    item: GoldenItem
    documents: tuple[str, ...]
    texts: tuple[str, ...]
    latency_ms: float
    leaks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Scores:
    questions: int
    hit_at_1: float
    hit_at_k: float
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    evidence_at_k: float


@dataclass(frozen=True, slots=True)
class RunSummary:
    engine: str
    strategy: str
    overall: Scores
    segments: dict[str, Scores]
    acl_leaks: int
    unanswerable: int
    unanswerable_empty: int
    latency_p50_ms: float
    latency_p95_ms: float
    missed: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    dataset: DatasetMeta
    top_k: int
    candidate_k: int
    embeddings: str
    documents: int
    chunks: int
    runs: list[RunSummary]

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset.model_dump(),
            "top_k": self.top_k,
            "candidate_k": self.candidate_k,
            "embeddings": self.embeddings,
            "documents": self.documents,
            "chunks": self.chunks,
            "runs": [asdict(run) for run in self.runs],
        }


def document_of(record: VectorRecord) -> str:
    return str(record.metadata.get("document_id", record.id.split("::", 1)[0]))


async def evaluate_service(
    service: RetrievalService,
    items: Sequence[GoldenItem],
    *,
    strategy: RetrievalStrategy,
    top_k: int,
    candidate_k: int,
    scope: AccessScope | None = None,
) -> list[Outcome]:
    effective_scope = scope or AccessScope()
    outcomes: list[Outcome] = []
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
        response = await service.retrieve(request)
        elapsed = (perf_counter() - started) * 1000
        records = [result.record for result in response.results]
        # La eval no confia en el codigo evaluado: revisa la ACL de cada resultado.
        leaks = {
            document_of(record)
            for record in records
            if not effective_scope.permits(record.metadata)
            or document_of(record) in item.forbidden_document_ids
        }
        outcomes.append(
            Outcome(
                item,
                unique_in_order(document_of(record) for record in records),
                tuple(record.text for record in records),
                elapsed,
                tuple(sorted(leaks)),
            )
        )
    return outcomes


def _has_evidence(outcome: Outcome) -> bool:
    return any(
        evidence.casefold() in text.casefold()
        for text in outcome.texts
        for evidence in outcome.item.evidence
    )


def score(outcomes: Sequence[Outcome], top_k: int) -> Scores:
    answerable = [outcome for outcome in outcomes if outcome.item.answerable]
    if not answerable:
        raise ValueError("Se necesitan preguntas con respuesta para puntuar el retrieval")

    def average(metric: Callable[[Outcome], float]) -> float:
        return round(fmean(metric(outcome) for outcome in answerable), 4)

    return Scores(
        questions=len(answerable),
        hit_at_1=average(lambda o: hit_at_k(o.documents, o.item.relevance, 1)),
        hit_at_k=average(lambda o: hit_at_k(o.documents, o.item.relevance, top_k)),
        recall_at_k=average(lambda o: recall_at_k(o.documents, o.item.relevance, top_k)),
        mrr=average(lambda o: reciprocal_rank(o.documents, o.item.relevance, top_k)),
        ndcg_at_k=average(lambda o: ndcg_at_k(o.documents, o.item.relevance, top_k)),
        evidence_at_k=average(lambda o: float(_has_evidence(o))),
    )


def summarize(engine: str, strategy: str, outcomes: Sequence[Outcome], top_k: int) -> RunSummary:
    groups: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.item.answerable:
            item = outcome.item
            groups[f"question_type={item.question_type}"].append(outcome)
            groups[f"language={item.language}"].append(outcome)
            groups[f"doc_type={item.doc_type}"].append(outcome)
    unanswerable = [outcome for outcome in outcomes if not outcome.item.answerable]
    latencies = [outcome.latency_ms for outcome in outcomes]
    return RunSummary(
        engine=engine,
        strategy=strategy,
        overall=score(outcomes, top_k),
        segments={key: score(group, top_k) for key, group in sorted(groups.items())},
        acl_leaks=sum(len(outcome.leaks) for outcome in outcomes),
        unanswerable=len(unanswerable),
        unanswerable_empty=sum(not outcome.documents for outcome in unanswerable),
        latency_p50_ms=round(percentile(latencies, 50), 2),
        latency_p95_ms=round(percentile(latencies, 95), 2),
        missed=[
            outcome.item.id
            for outcome in outcomes
            if outcome.item.answerable
            and not hit_at_k(outcome.documents, outcome.item.relevance, top_k)
        ],
    )


def local_settings(corpus: Path) -> RetrievalSettings:
    # Todo explicito: ninguna variable RETRIEVAL__* del entorno puede activar la nube.
    return RetrievalSettings(
        engine="native",
        source="synthetic",
        documents_path=corpus,
        max_documents=100_000,
        chunk_max_words=80,
        chunk_overlap_words=15,
        merge_ratio=0.5,
        backend="local",
        embeddings="hashing",
        semantic_ranker=False,
        reranker="token-coverage",
        llm="none",
        managed="none",
    )


def cloud_settings(corpus: Path, engine: Engine) -> RetrievalSettings:
    # Composición explícita: solo el backend de AI Search con embeddings de Foundry.
    return local_settings(corpus).model_copy(
        update={
            "backend": "azure_search",
            "embeddings": "azure",
            "semantic_ranker": engine == "azure-semantic",
        }
    )


async def run_retrieval_eval(
    items: Sequence[GoldenItem],
    meta: DatasetMeta,
    *,
    corpus: Path = CORPUS_PATH,
    engines: Sequence[Engine] = LOCAL_ENGINES,
    strategies: Sequence[RetrievalStrategy] | None = None,
    top_k: int = 5,
    candidate_k: int = 20,
) -> RetrievalReport:
    verify_corpus_hash(meta, corpus)
    if not engines:
        raise ValueError("Se necesita al menos un motor")
    settings = local_settings(corpus)
    embeddings = HashingEmbedding(1024)
    records, parents = await load_synthetic(settings, embeddings)
    runs: list[RunSummary] = []
    for engine in engines:
        selected = [s for s in ENGINE_STRATEGIES[engine] if not strategies or s in strategies]
        if not selected:
            continue
        if engine in CLOUD_ENGINES:
            from retrieval.bootstrap import build_runtime

            runtime = await build_runtime(cloud_settings(corpus, engine))
            try:
                for strategy in selected:
                    outcomes = await evaluate_service(
                        runtime.service,
                        items,
                        strategy=strategy,
                        top_k=top_k,
                        candidate_k=candidate_k,
                    )
                    runs.append(summarize(engine, strategy, outcomes, top_k))
            finally:
                await runtime.aclose()
            continue
        service = build_local_service(
            settings.model_copy(update={"engine": engine}),
            records=records,
            parents=parents,
            embeddings=embeddings,
        )
        for strategy in selected:
            outcomes = await evaluate_service(
                service, items, strategy=strategy, top_k=top_k, candidate_k=candidate_k
            )
            runs.append(summarize(engine, strategy, outcomes, top_k))
    if not runs:
        raise ValueError("Ninguna combinacion motor/estrategia seleccionada")
    label = CLOUD_EMBEDDINGS if all(e in CLOUD_ENGINES for e in engines) else EMBEDDINGS
    return RetrievalReport(meta, top_k, candidate_k, label, len(parents), len(records), runs)


def render_markdown(report: RetrievalReport) -> str:
    k = report.top_k
    lines = [
        "# Evals de retrieval (fase 5.2)",
        "",
        f"- Golden dataset v{report.dataset.version}: {report.dataset.items} preguntas "
        f"({report.dataset.by_type}); corpus `{report.dataset.corpus}` "
        f"(sha256 `{report.dataset.corpus_sha256[:12]}`).",
        f"- Corpus indexado: {report.documents} documentos / {report.chunks} chunks; "
        f"embeddings `{report.embeddings}`; top_k={k}, candidate_k={report.candidate_k}.",
        "- Coste: 0 (todo local). Latencia medida en este equipo, solo orientativa.",
        "- Limitacion: con embeddings `hashing` las cifras de `vector` validan el arnes, no la "
        "calidad semantica; BM25 si es una baseline legitima.",
        "",
        "## Resumen (preguntas con respuesta)",
        "",
        f"| Motor | Estrategia | Hit@1 | Hit@{k} | Recall@{k} | MRR | nDCG@{k} | Evidencia@{k} "
        "| Fugas ACL | Sin resultados (sin respuesta) | p50 ms | p95 ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in report.runs:
        s = run.overall
        lines.append(
            f"| {run.engine} | {run.strategy} | {s.hit_at_1:.3f} | {s.hit_at_k:.3f} | "
            f"{s.recall_at_k:.3f} | {s.mrr:.3f} | {s.ndcg_at_k:.3f} | {s.evidence_at_k:.3f} | "
            f"{run.acl_leaks} | {run.unanswerable_empty}/{run.unanswerable} | "
            f"{run.latency_p50_ms:.1f} | {run.latency_p95_ms:.1f} |"
        )
    segments = sorted({key for run in report.runs for key in run.segments})
    lines += [
        "",
        f"## Hit@{k} por segmento",
        "",
        "| Motor | Estrategia | " + " | ".join(f"{key} (n)" for key in segments) + " |",
        "|---|---|" + "---:|" * len(segments),
    ]
    for run in report.runs:
        cells = [
            f"{run.segments[key].hit_at_k:.2f} ({run.segments[key].questions})"
            if key in run.segments
            else "-"
            for key in segments
        ]
        lines.append(f"| {run.engine} | {run.strategy} | " + " | ".join(cells) + " |")
    always_missed = sorted(set.intersection(*(set(run.missed) for run in report.runs)))
    lines += [
        "",
        f"## Preguntas que ninguna estrategia acierta en el top-{k} ({len(always_missed)})",
        "",
        *(f"- `{item_id}`" for item_id in always_missed[:25]),
    ]
    if len(always_missed) > 25:
        lines.append(f"- ... y {len(always_missed) - 25} mas (ver JSON)")
    return "\n".join(lines) + "\n"


def write_report(report: RetrievalReport, out_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "retrieval.json", out_dir / "retrieval.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--engine",
        action="append",
        choices=list(ENGINE_STRATEGIES),
        help="Por defecto, solo los locales. azure y azure-semantic consultan la nube (coste).",
    )
    parser.add_argument("--strategy", action="append", choices=ENGINE_STRATEGIES["llamaindex"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    args = parser.parse_args()
    engines: list[Engine] = args.engine or list(LOCAL_ENGINES)
    cloud = any(engine in CLOUD_ENGINES for engine in engines)
    meta, items = load_golden(args.dataset)
    report = asyncio.run(
        run_retrieval_eval(
            items,
            meta,
            corpus=args.corpus,
            engines=engines,
            strategies=args.strategy,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
        )
    )
    # Los informes de la nube van aparte: no sustituyen al baseline local que usa la CI.
    json_path, md_path = write_report(
        report, args.out or (CLOUD_REPORTS_DIR if cloud else REPORTS_DIR)
    )
    print(f"Informe: {md_path} y {json_path}")
    for run in report.runs:
        s = run.overall
        print(
            f"{run.engine:>10} {run.strategy:>12}  hit@{report.top_k}={s.hit_at_k:.3f} "
            f"mrr={s.mrr:.3f} ndcg={s.ndcg_at_k:.3f} fugas={run.acl_leaks}"
        )


if __name__ == "__main__":
    main()
