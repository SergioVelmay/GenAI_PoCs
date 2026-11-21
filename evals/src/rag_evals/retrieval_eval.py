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
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Literal

from genaipocs_core.adapters.memory import HashingEmbedding
from genaipocs_core.application.llm.structured import StructuredOutputError
from genaipocs_core.application.retrieval import RetrievalRequest, RetrievalService
from genaipocs_core.config import LLMProvider
from genaipocs_core.domain.evals import (
    hit_at_k,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
    unique_in_order,
)
from genaipocs_core.domain.retrieval import AccessScope
from genaipocs_core.ports.llm import LLMContentFilterError, LLMPort
from genaipocs_core.ports.retrieval import RetrievalStrategy
from genaipocs_core.ports.vector_store import MetadataValue, VectorRecord
from rag_evals import EVALS_DIR
from rag_evals.golden import CORPUS_PATH, DATASET_PATH, DatasetMeta, GoldenItem, load_golden
from rag_evals.golden import verify_corpus as verify_corpus_hash
from rag_evals.metering import CostMeter
from retrieval.bootstrap import build_local_service, load_synthetic
from retrieval.settings import RetrievalSettings

Engine = Literal["native", "llamaindex", "azure", "azure-semantic", "gcp", "gcp-semantic"]
type Cloud = Literal["azure", "aws", "gcp"]
LOCAL_ENGINES: tuple[Engine, ...] = ("native", "llamaindex")
# Con coste (embeddings por consulta) y solo si se piden con --engine: nunca en CI.
ENGINE_CLOUD: dict[Engine, Cloud] = {
    "azure": "azure",
    "azure-semantic": "azure",
    "gcp": "gcp",
    "gcp-semantic": "gcp",
}
CLOUD_ENGINES: tuple[Engine, ...] = tuple(ENGINE_CLOUD)
ENGINE_STRATEGIES: dict[Engine, tuple[RetrievalStrategy, ...]] = {
    "native": ("bm25", "vector", "hybrid", "rerank", "parent-child"),
    "llamaindex": ("bm25", "vector", "hybrid", "rerank", "parent-child", "auto-merge"),
    # Índice `knowledge` del carril B en AI Search (corpus clasificado con ACL explícita).
    "azure": ("bm25", "vector", "hybrid"),
    # Híbrida + Semantic Ranker (reordena hasta 50 candidatos dentro del servicio).
    "azure-semantic": ("hybrid",),
    # Colección `knowledge` de Agent Retrieval (4.G1); `rerank` = híbrida → Ranking API.
    "gcp": ("bm25", "vector", "hybrid", "rerank"),
    # Híbrida + VertexRanker integrado en el batch (degrada a RRF en silencio si falla).
    "gcp-semantic": ("hybrid",),
}
# Estrategias que planifican la consulta con un LLM (fase 4): solo en la nube y con
# `--budget-usd`, sobre la busqueda hibrida de cada motor en la nube.
LLM_STRATEGIES: tuple[RetrievalStrategy, ...] = (
    "rewrite",
    "hyde",
    "subquestions",
    "router",
    "auto",
)
REPORTS_DIR = EVALS_DIR / "reports"
# Informes con coste de Azure. Las evals de la fase 5 eligen la carpeta por nube
# (`cloud_reports_dir`, `llm_reports_dir`); las de otras fases, en su pasada GCP.
CLOUD_REPORTS_DIR = REPORTS_DIR / "azure"
EMBEDDINGS = "hashing-1024"
CLOUD_EMBEDDINGS: dict[Cloud, str] = {
    "azure": "azure:text-embedding-3-small-1024",
    "gcp": "gcp:gemini-embedding-001-1024",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    item: GoldenItem
    documents: tuple[str, ...]
    texts: tuple[str, ...]
    latency_ms: float
    leaks: tuple[str, ...]
    # Plan del LLM invalido o rechazado (fail-closed): la pregunta se queda sin resultados.
    error: str | None = None


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
    plan_errors: int = 0


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
        try:
            response = await service.retrieve(request)
        except (ValueError, StructuredOutputError, LLMContentFilterError) as exc:
            # Prompt Shields bloquea el planificador ante una inyeccion: sin resultados (seguro).
            if strategy not in LLM_STRATEGIES:
                raise
            elapsed = (perf_counter() - started) * 1000
            outcomes.append(Outcome(item, (), (), elapsed, (), str(exc)[:200]))
            continue
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
        plan_errors=sum(outcome.error is not None for outcome in outcomes),
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


def engines_cloud(engines: Sequence[Engine]) -> Cloud | None:
    """La nube de los motores pedidos (None = solo locales). Un informe, una nube."""
    clouds = {ENGINE_CLOUD[engine] for engine in engines if engine in ENGINE_CLOUD}
    if len(clouds) > 1:
        raise ValueError("Una ejecucion compara motores de una sola nube")
    return clouds.pop() if clouds else None


def cloud_reports_dir(cloud: Cloud) -> Path:
    return REPORTS_DIR / cloud


def llm_cloud(provider: LLMProvider) -> Cloud:
    """La nube de un proveedor de LLM real (el simulado no genera informes con coste)."""
    if provider is LLMProvider.SIMULATED:
        raise ValueError("El proveedor simulado no genera informes de evals con coste")
    clouds: dict[LLMProvider, Cloud] = {
        LLMProvider.AZURE: "azure",
        LLMProvider.AWS: "aws",
        LLMProvider.GCP: "gcp",
    }
    return clouds[provider]


def llm_reports_dir(provider: LLMProvider) -> Path:
    """Carpeta de los informes con LLM real (generacion y jueces): la de la nube del proveedor.

    Asi una calibracion con Gemini no sobrescribe la de Azure (mismos nombres de fichero).
    """
    return cloud_reports_dir(llm_cloud(provider))


def cloud_settings(corpus: Path, engine: Engine) -> RetrievalSettings:
    # Composición explícita: solo el backend de la nube con los embeddings de su ingesta.
    if ENGINE_CLOUD[engine] == "gcp":
        return local_settings(corpus).model_copy(
            update={
                "backend": "gcp_vector_search",
                "embeddings": "gcp",
                "semantic_ranker": engine == "gcp-semantic",
                # La estrategia `rerank` del motor `gcp` es la Ranking API.
                "reranker": "gcp" if engine == "gcp" else "token-coverage",
            }
        )
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
    llm: LLMPort | None = None,
) -> RetrievalReport:
    """Con `llm` (gateway con presupuesto), los motores de la nube suman `LLM_STRATEGIES`."""
    verify_corpus_hash(meta, corpus)
    if not engines:
        raise ValueError("Se necesita al menos un motor")
    cloud = engines_cloud(engines)
    if llm is not None and cloud is None:
        raise ValueError("Las estrategias con LLM solo se evaluan sobre motores en la nube")
    settings = local_settings(corpus)
    embeddings = HashingEmbedding(1024)
    records, parents = await load_synthetic(settings, embeddings)
    runs: list[RunSummary] = []
    for engine in engines:
        available = ENGINE_STRATEGIES[engine]
        if engine in CLOUD_ENGINES and llm is not None:
            available = available + LLM_STRATEGIES
        selected = [s for s in available if not strategies or s in strategies]
        if not selected:
            continue
        if engine in CLOUD_ENGINES:
            from genaipocs_core.adapters.local_retrieval import TokenCoverageReranker
            from genaipocs_core.config import Settings
            from retrieval.cloud import build_cloud_parts

            parts = await build_cloud_parts(cloud_settings(corpus, engine), Settings())
            try:
                if parts.backend is None:
                    raise ValueError("El backend en la nube no esta configurado")
                service = RetrievalService(
                    parts.backend, reranker=parts.reranker or TokenCoverageReranker(), llm=llm
                )
                for strategy in selected:
                    outcomes = await evaluate_service(
                        service,
                        items,
                        strategy=strategy,
                        top_k=top_k,
                        candidate_k=candidate_k,
                    )
                    runs.append(summarize(engine, strategy, outcomes, top_k))
            finally:
                await parts.aclose()
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
    only_cloud = cloud is not None and all(e in CLOUD_ENGINES for e in engines)
    label = CLOUD_EMBEDDINGS[cloud] if cloud is not None and only_cloud else EMBEDDINGS
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
        "| Fugas ACL | Sin resultados (sin respuesta) | Planes fallidos | p50 ms | p95 ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in report.runs:
        s = run.overall
        lines.append(
            f"| {run.engine} | {run.strategy} | {s.hit_at_1:.3f} | {s.hit_at_k:.3f} | "
            f"{s.recall_at_k:.3f} | {s.mrr:.3f} | {s.ndcg_at_k:.3f} | {s.evidence_at_k:.3f} | "
            f"{run.acl_leaks} | {run.unanswerable_empty}/{run.unanswerable} | "
            f"{run.plan_errors} | {run.latency_p50_ms:.1f} | {run.latency_p95_ms:.1f} |"
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


def write_report(
    report: RetrievalReport, out_dir: Path = REPORTS_DIR, name: str = "retrieval"
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


def _planner(budget: Decimal) -> tuple[LLMPort, CostMeter]:
    """Gateway enrutado (el planificador de consultas va al modelo barato) con presupuesto."""
    from genaipocs_core.adapters.llm_factory import build_gateway, build_tracer
    from genaipocs_core.config import Settings

    settings = Settings()
    meter = CostMeter(budget_usd=budget, forward_to=build_tracer(settings))
    return build_gateway(settings, tracer=meter), meter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--engine",
        action="append",
        choices=list(ENGINE_STRATEGIES),
        help="Por defecto, solo los locales. azure* y gcp* consultan la nube (coste); "
        "una ejecución, una nube.",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        choices=[*ENGINE_STRATEGIES["llamaindex"], *LLM_STRATEGIES],
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument(
        "--budget-usd",
        type=Decimal,
        help="Activa las estrategias con LLM en los motores de la nube (con coste).",
    )
    args = parser.parse_args()
    engines: list[Engine] = args.engine or list(LOCAL_ENGINES)
    try:
        cloud = engines_cloud(engines)
    except ValueError as error:
        parser.error(str(error))
    if args.budget_usd is not None and cloud is None:
        parser.error("--budget-usd solo tiene sentido con un motor en la nube (azure*, gcp*)")
    llm, meter = _planner(args.budget_usd) if args.budget_usd is not None else (None, None)
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
            llm=llm,
        )
    )
    # Los informes de la nube van aparte: no sustituyen al baseline local que usa la CI.
    json_path, md_path = write_report(
        report,
        args.out or (cloud_reports_dir(cloud) if cloud else REPORTS_DIR),
        "retrieval-llm" if llm is not None else "retrieval",
    )
    print(f"Informe: {md_path} y {json_path}")
    if meter is not None:
        snapshot = meter.snapshot()
        print(f"Coste LLM: {snapshot.cost_usd} USD en {snapshot.calls} llamadas")
    for run in report.runs:
        s = run.overall
        print(
            f"{run.engine:>10} {run.strategy:>12}  hit@{report.top_k}={s.hit_at_k:.3f} "
            f"mrr={s.mrr:.3f} ndcg={s.ndcg_at_k:.3f} fugas={run.acl_leaks}"
        )


if __name__ == "__main__":
    main()
