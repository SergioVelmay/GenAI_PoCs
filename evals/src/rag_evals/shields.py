"""Escudos de contenido frente a frente: heurístico local y Azure AI Content Safety (fase 8).

Mismo conjunto etiquetado (`shield-cases-v1.jsonl`): ataques directos (jailbreak en el prompt)
e indirectos (instrucciones dentro de documentos), y textos legítimos que se parecen a un
ataque ("olvida lo que te dije", "instrucciones para llegar"). Mide detección (recall), falsos
positivos y latencia. El escudo de Azure usa Prompt Shields (F0, sin coste hasta la cuota).

Uso:
    uv run python -m rag_evals.shields                     # solo heurístico (coste 0)
    uv run python -m rag_evals.shields --shield azure      # y Content Safety (perfil de az)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict

from headache_core.ports.guardrails import ContentShieldPort, ShieldUnavailableError
from rag_evals import EVALS_DIR
from rag_evals.retrieval_eval import CLOUD_REPORTS_DIR, REPORTS_DIR

DATASET = EVALS_DIR / "datasets" / "shield-cases-v1.jsonl"
ShieldName = Literal["heuristic", "azure"]


class ShieldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    channel: Literal["prompt", "document"]
    attack: bool
    text: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    id: str
    channel: str
    attack: bool
    detected: bool | None
    categories: tuple[str, ...]
    latency_ms: float


@dataclass(frozen=True, slots=True)
class ShieldSummary:
    shield: str
    attacks: int
    detected: int
    benign: int
    false_positives: int
    errors: int
    recall: float | None
    false_positive_rate: float | None
    latency_p50_ms: float
    missed: list[str]
    blocked_benign: list[str]


def load_cases(path: Path = DATASET) -> list[ShieldCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ShieldCase.model_validate_json(line) for line in lines if line.strip()]


async def scan(shield: ContentShieldPort, cases: Sequence[ShieldCase]) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        started = perf_counter()
        try:
            if case.channel == "prompt":
                verdict = await shield.scan_prompt(case.text)
            else:
                [verdict] = await shield.scan_documents([case.text])
        except ShieldUnavailableError:
            elapsed = (perf_counter() - started) * 1000
            results.append(CaseResult(case.id, case.channel, case.attack, None, (), elapsed))
            continue
        elapsed = (perf_counter() - started) * 1000
        results.append(
            CaseResult(
                case.id, case.channel, case.attack, verdict.flagged, verdict.categories, elapsed
            )
        )
    return results


def summarize(shield: str, results: Sequence[CaseResult]) -> ShieldSummary:
    scored = [r for r in results if r.detected is not None]
    attacks = [r for r in scored if r.attack]
    benign = [r for r in scored if not r.attack]
    detected = [r for r in attacks if r.detected]
    blocked = [r for r in benign if r.detected]
    return ShieldSummary(
        shield=shield,
        attacks=len(attacks),
        detected=len(detected),
        benign=len(benign),
        false_positives=len(blocked),
        errors=len(results) - len(scored),
        recall=round(len(detected) / len(attacks), 3) if attacks else None,
        false_positive_rate=round(len(blocked) / len(benign), 3) if benign else None,
        latency_p50_ms=round(median(r.latency_ms for r in results), 1) if results else 0.0,
        missed=[r.id for r in attacks if not r.detected],
        blocked_benign=[r.id for r in blocked],
    )


def render(summaries: Sequence[ShieldSummary], cases: int) -> str:
    lines = [
        "# Escudos de contenido: heurístico frente a Azure AI Content Safety",
        "",
        f"- Conjunto `shield-cases-v1` ({cases} textos): ataques directos en el prompt, "
        "inyecciones indirectas en documentos y textos legítimos parecidos a un ataque.",
        "",
        "| Escudo | Ataques detectados | Falsos positivos | Errores | p50 ms | No detectados "
        "| Legítimos bloqueados |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.shield} | {s.detected}/{s.attacks} | {s.false_positives}/{s.benign} | "
            f"{s.errors} | {s.latency_p50_ms} | {', '.join(s.missed) or '-'} | "
            f"{', '.join(s.blocked_benign) or '-'} |"
        )
    return "\n".join(lines) + "\n"


async def run(shields: Sequence[ShieldName]) -> tuple[list[ShieldSummary], list[CaseResult]]:
    from booking_agent.guardrails import build_shield
    from headache_core.config import Settings

    cases = load_cases()
    summaries: list[ShieldSummary] = []
    details: list[CaseResult] = []
    for name in shields:
        settings = Settings()
        settings = settings.model_copy(
            update={"guardrails": settings.guardrails.model_copy(update={"shield": name})}
        )
        async with build_shield(settings) as shield:
            results = await scan(shield, cases)
        summaries.append(summarize(name, results))
        details += results
    return summaries, details


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shield", action="append", choices=["heuristic", "azure"])
    args = parser.parse_args()
    shields: list[ShieldName] = ["heuristic", *(s for s in args.shield or [] if s != "heuristic")]
    summaries, details = asyncio.run(run(shields))
    out_dir = CLOUD_REPORTS_DIR if "azure" in shields else REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render(summaries, len(load_cases()))
    (out_dir / "shields.md").write_text(markdown, encoding="utf-8", newline="\n")
    payload = {"summaries": [asdict(s) for s in summaries], "cases": [asdict(d) for d in details]}
    (out_dir / "shields.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(markdown)


if __name__ == "__main__":
    main()
