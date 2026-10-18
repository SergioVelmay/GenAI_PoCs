"""Benchmark de prompt caching: el mismo manual de políticas largo, con y sin caché.

Sin caché: se antepone un nonce distinto en cada llamada, así ningún prefijo se repite
(Azure cachea solo y no se puede desactivar) y no se marca `cachePoint` (Bedrock).
Con caché: prefijo idéntico y `cache_checkpoint=True` al final del manual.

    uv run python scripts/llm_cache_benchmark.py --provider azure --tier strong --runs 5
    uv run python scripts/llm_cache_benchmark.py --provider aws --tier strong --runs 5

Tiene coste (pequeño): ~10 k tokens de entrada por llamada x 2 modos x runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from headache_core.adapters.llm_factory import build_provider_llm, build_tracer
from headache_core.application.llm import ModelTier, ObservedLLM, PriceTable, RateLimitedLLM
from headache_core.config import LLMProvider, get_settings
from headache_core.ports import ChatMessage, LLMPort, Role, TokenUsage

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = (
    "¿Puedo pedir el reembolso de un concierto candlelit 30 horas antes?",
    "¿Cuántas veces puedo cambiar la fecha de una entrada de teatro?",
    "Is the venue in Lisbon accessible for wheelchair users?",
    "¿Se devuelven los gastos de gestión?",
    "What happens if the organiser cancels a food & drink session?",
    "¿Cuál es el horario del chat de soporte en Madrid?",
    "Can I get a refund for a nightlife event?",
    "¿Qué edad mínima hay para los tours?",
)


@dataclass(frozen=True, slots=True)
class Call:
    mode: str
    run: int
    model: str
    usage: TokenUsage
    latency_ms: float
    cost_usd: Decimal


def build_manual(knowledge_dir: Path) -> str:
    docs = sorted(knowledge_dir.glob("*.md"))
    if not docs:
        raise SystemExit(
            f"No hay documentos en {knowledge_dir}. Ejecuta antes:\n"
            "  uv run python scripts/generate_synthetic_data.py --scale small"
        )
    body = "\n\n---\n\n".join(doc.read_text(encoding="utf-8") for doc in docs)
    return (
        "Eres el agente de soporte de Headache. Responde en el idioma de la pregunta, en dos "
        "frases como máximo y usando solo el manual siguiente.\n\n# Manual de Headache\n\n" + body
    )


async def run_mode(
    llm: LLMPort, manual: str, prices: PriceTable, *, cached: bool, runs: int, max_tokens: int
) -> list[Call]:
    mode = "con_cache" if cached else "sin_cache"
    calls: list[Call] = []
    for i in range(runs):
        system = manual if cached else f"[petición {uuid.uuid4()}]\n{manual}"
        response = await llm.complete(
            [
                ChatMessage(Role.SYSTEM, system, cache_checkpoint=cached),
                ChatMessage(Role.USER, QUESTIONS[i % len(QUESTIONS)]),
            ],
            max_tokens=max_tokens,
        )
        usage = response.usage
        cost = prices.cost(response.model, usage) or Decimal(0)
        calls.append(Call(mode, i, response.model, usage, response.latency_ms, cost))
        print(
            f"  {mode:<9} #{i}  in={usage.input_tokens:>6}  cached={usage.cached_input_tokens:>6}"
            f"  write={usage.cache_write_tokens:>6}  out={usage.output_tokens:>4}"
            f"  {response.latency_ms:>7.0f} ms  ${cost:.6f}"
        )
    return calls


def summarize(calls: list[Call]) -> dict[str, Any]:
    input_tokens = sum(c.usage.input_tokens for c in calls)
    cached_tokens = sum(c.usage.cached_input_tokens for c in calls)
    latencies = [c.latency_ms for c in calls]
    return {
        "calls": len(calls),
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": sum(c.usage.cache_write_tokens for c in calls),
        "cache_hit_ratio": round(cached_tokens / input_tokens, 3) if input_tokens else 0.0,
        "cost_usd": sum((c.cost_usd for c in calls), Decimal(0)),
        "latency_p50_ms": round(statistics.median(latencies), 1),
        # La 1.ª llamada con caché la escribe; se benefician las siguientes.
        "latency_p50_ms_after_first": round(statistics.median(latencies[1:]), 1)
        if len(latencies) > 1
        else None,
    }


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Benchmark de prompt caching")
    parser.add_argument("--provider", type=LLMProvider, default=settings.llm_provider)
    parser.add_argument("--tier", type=ModelTier, default=ModelTier.STRONG)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--knowledge-dir", type=Path, default=ROOT / "data/synthetic/knowledge")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    manual = build_manual(args.knowledge_dir)
    prices = PriceTable()
    tracer = build_tracer(settings)
    # Mismo límite en cliente que el gateway: ~10 K tokens por llamada agotan rápido el TPM del
    # despliegue. La espera no cuenta en la latencia (la mide el adaptador).
    limited = RateLimitedLLM(
        build_provider_llm(settings, args.provider, args.tier),
        requests_per_minute=settings.llm_gateway.requests_per_minute,
        tokens_per_minute=settings.llm_gateway.tokens_per_minute,
    )
    llm = ObservedLLM(
        limited,
        tracer=tracer,
        prices=prices,
        name=f"cache-benchmark:{args.provider}:{args.tier}",
        metadata={"benchmark": "prompt-caching"},
    )

    print(f"Manual: {len(manual):,} caracteres (~{len(manual) // 4:,} tokens)")
    without = await run_mode(
        llm, manual, prices, cached=False, runs=args.runs, max_tokens=args.max_tokens
    )
    with_cache = await run_mode(
        llm, manual, prices, cached=True, runs=args.runs, max_tokens=args.max_tokens
    )
    summary = {"sin_cache": summarize(without), "con_cache": summarize(with_cache)}

    out = args.out or ROOT / f"data/benchmarks/llm_cache_{args.provider}_{args.tier}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"summary": summary, "calls": [asdict(c) for c in without + with_cache]}
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("\nResumen:")
    for mode, values in summary.items():
        print(f"  {mode}: {json.dumps(values, default=str)}")
    before, after = summary["sin_cache"]["cost_usd"], summary["con_cache"]["cost_usd"]
    if before:
        print(f"  Ahorro en coste con caché: {1 - after / before:.1%}")
    print(f"Resultados en {out}")

    flush = getattr(tracer, "flush", None)
    if callable(flush):
        flush()


if __name__ == "__main__":
    asyncio.run(main())
