"""Calibración de la caché semántica (fase 10.3): aciertos frente a falsos aciertos.

    uv run python -m rag_evals.semantic_cache [--check]                          # coste 0
    uv run python -m rag_evals.semantic_cache --embeddings azure --budget-usd 0.01   # coste

Cada caso guarda la respuesta de `seed` en una caché vacía y pregunta `probe`:
- `paraphrase`: debería reutilizarse (mide la tasa de aciertos, el ahorro).
- `trap`: no debería (otra ciudad, categoría, operación, número, idioma o ACL). Un acierto
  aquí es un **falso acierto**: una respuesta equivocada servida con total confianza.
- `personal`: no se guarda nunca, ni siquiera si la pregunta es idéntica.

Se barre el umbral de similitud con y sin la guarda de facetas del dominio. El umbral
recomendado es el de más aciertos con 0 falsos aciertos. Con `hashing` (bolsa de palabras) se
valida el arnés, no la calidad semántica: el umbral real se calibra con los embeddings reales.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from headache_core.adapters.memory import HashingEmbedding, InMemorySemanticCache
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.admission import ClientPlan, access_scope
from headache_core.domain.semantic_cache import CacheScope, detect_language
from headache_core.ports.vector_store import EmbeddingPort
from rag_evals.retrieval_eval import REPORTS_DIR

DATASET_PATH = Path(__file__).resolve().parents[2] / "datasets" / "semantic-cache-v1.jsonl"
THRESHOLDS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99)
# Suelo de la tasa de aciertos en el umbral recomendado (gate de la CI con `hashing`).
MIN_HIT_RATE = {"hashing": 0.45, "azure": 0.7}
EMBEDDING_PRICE_PER_MILLION = Decimal("0.02")  # text-embedding-3-small

type Backend = Literal["hashing", "azure"]
type Kind = Literal["paraphrase", "trap", "personal"]


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    kind: Kind
    seed: str
    probe: str
    should_hit: bool
    seed_plan: ClientPlan = ClientPlan.PUBLIC
    probe_plan: ClientPlan = ClientPlan.PUBLIC
    known_gap: bool = False
    note: str = ""


def load_cases(path: Path) -> list[Case]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            Case(
                id=raw["id"],
                kind=raw["kind"],
                seed=raw["seed"],
                probe=raw["probe"],
                should_hit=raw["should_hit"],
                seed_plan=ClientPlan(raw.get("seed_plan", "public")),
                probe_plan=ClientPlan(raw.get("probe_plan", "public")),
                known_gap=raw.get("known_gap", False),
                note=raw.get("note", ""),
            )
        )
    return cases


class FrozenEmbedding:
    """Embeddings precalculados: cada texto se paga una vez aunque se barran 11 umbrales."""

    def __init__(self, vectors: dict[str, list[float]], dimensions: int) -> None:
        self._vectors = vectors
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vectors[text]


def scope_for(question: str, plan: ClientPlan) -> CacheScope:
    return CacheScope(
        access_scope(plan).allowed_acls, detect_language(question), "eval", "extractive"
    )


@dataclass(frozen=True, slots=True)
class CaseResult:
    id: str
    kind: str
    should_hit: bool
    hit: bool
    reason: str
    stored: bool
    known_gap: bool

    @property
    def false_hit(self) -> bool:
        return self.hit and not self.should_hit


async def run_case(
    case: Case, embeddings: EmbeddingPort, *, threshold: float, guard: bool
) -> CaseResult:
    cache = SemanticCache(
        InMemorySemanticCache(), embeddings, threshold=threshold, facet_guard=guard
    )
    seed = await cache.lookup(case.seed, scope_for(case.seed, case.seed_plan))
    stored = await cache.store_answer(seed, json.dumps({"answer_of": case.id}))
    probe = await cache.lookup(case.probe, scope_for(case.probe, case.probe_plan))
    return CaseResult(
        case.id, case.kind, case.should_hit, probe.hit, probe.reason, stored, case.known_gap
    )


@dataclass(frozen=True, slots=True)
class SweepPoint:
    threshold: float
    guard: bool
    paraphrase_hits: int
    paraphrases: int
    false_hits: int
    known_gap_hits: int
    personal_stored: int
    false_hit_ids: tuple[str, ...]

    @property
    def hit_rate(self) -> float:
        return self.paraphrase_hits / self.paraphrases if self.paraphrases else 0.0


def summarize(threshold: float, guard: bool, results: Sequence[CaseResult]) -> SweepPoint:
    false = [r for r in results if r.false_hit and not r.known_gap]
    return SweepPoint(
        threshold=threshold,
        guard=guard,
        paraphrase_hits=sum(r.hit for r in results if r.kind == "paraphrase"),
        paraphrases=sum(r.kind == "paraphrase" for r in results),
        false_hits=len(false),
        known_gap_hits=sum(r.false_hit for r in results if r.known_gap),
        personal_stored=sum(r.stored for r in results if r.kind == "personal"),
        false_hit_ids=tuple(r.id for r in false),
    )


def recommend(points: Sequence[SweepPoint]) -> SweepPoint | None:
    """El umbral con más aciertos sin falsos aciertos; a igualdad, el más alto (prudente)."""
    safe = [p for p in points if p.false_hits == 0]
    return max(safe, key=lambda p: (p.paraphrase_hits, p.threshold), default=None)


def lowest_safe(points: Sequence[SweepPoint]) -> SweepPoint | None:
    """El umbral más bajo a partir del cual ya no hay falsos aciertos."""
    safe = None
    for point in sorted(points, key=lambda p: p.threshold, reverse=True):
        if point.false_hits:
            break
        safe = point
    return safe


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


@dataclass
class CacheEvalReport:
    embeddings: str
    dataset: str
    cases: int
    sweep: list[SweepPoint]
    recommended: SweepPoint | None
    recommended_without_guard: SweepPoint | None
    similarities: dict[str, float]
    decisions: list[CaseResult]
    cost_usd: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        def point(p: SweepPoint | None) -> dict[str, object] | None:
            return None if p is None else {**asdict(p), "hit_rate": round(p.hit_rate, 4)}

        return {
            "embeddings": self.embeddings,
            "dataset": self.dataset,
            "cases": self.cases,
            "recommended": point(self.recommended),
            "recommended_without_guard": point(self.recommended_without_guard),
            "sweep": [point(p) for p in self.sweep],
            "similarities": {k: round(v, 4) for k, v in self.similarities.items()},
            "decisions": [asdict(d) for d in self.decisions],
            "cost_usd": self.cost_usd,
        }


async def run_eval(
    cases: Sequence[Case], embeddings: EmbeddingPort, *, backend: str, dataset: str
) -> CacheEvalReport:
    texts = sorted({text for case in cases for text in (case.seed, case.probe)})
    vectors = dict(zip(texts, await embeddings.embed_texts(texts), strict=True))
    frozen = FrozenEmbedding(vectors, embeddings.dimensions)
    sweep: list[SweepPoint] = []
    by_point: dict[tuple[float, bool], list[CaseResult]] = {}
    for guard in (True, False):
        for threshold in THRESHOLDS:
            results = [
                await run_case(case, frozen, threshold=threshold, guard=guard) for case in cases
            ]
            by_point[(threshold, guard)] = results
            sweep.append(summarize(threshold, guard, results))
    recommended = recommend([p for p in sweep if p.guard])
    decisions = by_point[(recommended.threshold, True)] if recommended else []
    return CacheEvalReport(
        embeddings=backend,
        dataset=dataset,
        cases=len(cases),
        sweep=sweep,
        recommended=recommended,
        recommended_without_guard=lowest_safe([p for p in sweep if not p.guard]),
        similarities={c.id: cosine(vectors[c.seed], vectors[c.probe]) for c in cases},
        decisions=decisions,
    )


def check(report: CacheEvalReport) -> list[str]:
    problems = []
    for point in report.sweep:
        if point.personal_stored:
            problems.append(f"Preguntas personales guardadas en {point.threshold}")
        if point.guard and point.false_hits:
            problems.append(
                f"Falsos aciertos con guarda en {point.threshold}: {point.false_hit_ids}"
            )
    floor = MIN_HIT_RATE[report.embeddings]
    if report.recommended is None or report.recommended.hit_rate < floor:
        problems.append(f"Tasa de aciertos por debajo de {floor:.0%} en el umbral recomendado")
    return problems


def render_markdown(report: CacheEvalReport) -> str:
    rec, raw = report.recommended, report.recommended_without_guard
    lines = [
        "# Caché semántica: calibración del umbral",
        "",
        f"Embeddings: `{report.embeddings}` · dataset `{report.dataset}` ({report.cases} casos)."
        + (
            " Bolsa de palabras: valida el arnés, no la calidad semántica."
            if report.embeddings == "hashing"
            else ""
        ),
        "",
    ]
    if rec:
        lines.append(
            f"**Umbral recomendado (con guarda de facetas): {rec.threshold}** → "
            f"{rec.paraphrase_hits}/{rec.paraphrases} paráfrasis reutilizadas "
            f"({rec.hit_rate:.0%}), 0 falsos aciertos."
        )
    if raw:
        lines.append(
            f"Sin guarda, para no servir respuestas equivocadas hay que subir el umbral a "
            f"{raw.threshold}, y entonces se reutilizan {raw.paraphrase_hits}/{raw.paraphrases} "
            f"({raw.hit_rate:.0%})."
        )
    lines += [
        "",
        "| Umbral | Guarda | Paráfrasis reutilizadas | Falsos aciertos | Límite conocido | "
        "Personales guardadas |",
        "|---:|:---:|---:|---:|---:|---:|",
    ]
    for p in report.sweep:
        lines.append(
            f"| {p.threshold} | {'sí' if p.guard else 'no'} | {p.paraphrase_hits}/"
            f"{p.paraphrases} ({p.hit_rate:.0%}) | {p.false_hits} | {p.known_gap_hits} "
            f"| {p.personal_stored} |"
        )
    lines += ["", "## Casos en el umbral recomendado", ""]
    lines += ["| Caso | Tipo | Similitud | ¿Debe acertar? | Decisión |", "|---|---|---:|:---:|---|"]
    for d in report.decisions:
        mark = "falso acierto" if d.false_hit else d.reason
        gap = " (límite conocido)" if d.known_gap else ""
        lines.append(
            f"| {d.id} | {d.kind} | {report.similarities[d.id]:.3f} | "
            f"{'sí' if d.should_hit else 'no'} | {mark}{gap} |"
        )
    if report.cost_usd:
        lines += ["", f"Coste de embeddings: {report.cost_usd} USD."]
    return "\n".join(lines) + "\n"


def write_report(report: CacheEvalReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "semantic-cache.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path = out_dir / "semantic-cache.md"
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return md_path


def estimated_cost(cases: Sequence[Case]) -> Decimal:
    chars = sum(len(text) for text in {t for c in cases for t in (c.seed, c.probe)})
    return Decimal(chars // 4 + 1) * EMBEDDING_PRICE_PER_MILLION / Decimal(1_000_000)


def build_embeddings(backend: Backend) -> EmbeddingPort:
    if backend == "hashing":
        return HashingEmbedding(1024)
    from headache_core.adapters.cloud_embeddings import AzureOpenAIEmbedding
    from headache_core.config import Settings

    return AzureOpenAIEmbedding.from_settings(Settings().azure_openai)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embeddings", choices=["hashing", "azure"], default="hashing")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    parser.add_argument("--check", action="store_true", help="Sale con 1 si algo falla")
    parser.add_argument("--budget-usd", type=Decimal)
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    cost: Decimal | None = None
    if args.embeddings != "hashing":
        if args.budget_usd is None:
            parser.error("--embeddings azure requiere --budget-usd (llamadas con coste)")
        cost = estimated_cost(cases)
        if cost > args.budget_usd:
            parser.error(f"Coste estimado {cost} USD por encima del presupuesto")
    report = asyncio.run(
        run_eval(
            cases,
            build_embeddings(args.embeddings),
            backend=args.embeddings,
            dataset=args.dataset.name,
        )
    )
    report.cost_usd = f"~{cost:.6f}" if cost is not None else None
    out = args.out if args.embeddings == "hashing" else args.out / "azure"
    md_path = write_report(report, out)
    rec = report.recommended
    if rec:
        print(
            f"{args.embeddings}: umbral {rec.threshold}, {rec.paraphrase_hits}/{rec.paraphrases} "
            f"paráfrasis, 0 falsos aciertos"
        )
    print(f"Informe: {md_path}")
    problems = check(report)
    for problem in problems:
        print(f"  FALLO {problem}")
    if args.check and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
