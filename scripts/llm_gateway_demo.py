"""Triaje de emails de soporte a través del LLM Gateway.

Ejercita todo a la vez: model routing, structured outputs, fallback, rate limits,
trazas en Langfuse y coste por petición. Compara la intención predicha con la esperada.

    uv run python scripts/llm_gateway_demo.py --limit 20
    $env:LLM_GATEWAY__ENGINE="litellm"; uv run python scripts/llm_gateway_demo.py --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from headache_core.adapters.llm_factory import build_gateway, build_tracer
from headache_core.adapters.memory import RecordingTracer
from headache_core.application import SupportTriageService
from headache_core.application.llm import CompositeTracer
from headache_core.config import get_settings
from headache_core.domain import SupportEmail, SupportIntent
from headache_core.ports import LLMError

ROOT = Path(__file__).resolve().parents[1]


def load_emails(path: Path, limit: int, seed: int) -> list[tuple[SupportEmail, SupportIntent]]:
    if not path.exists():
        raise SystemExit(
            f"No existe {path}. Ejecuta antes:\n"
            "  uv run python scripts/generate_synthetic_data.py --scale small"
        )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    sample = random.Random(seed).sample(rows, min(limit, len(rows)))
    return [
        (
            SupportEmail(
                id=row["id"],
                customer_id=row["customer_id"],
                language=row["language"],
                subject=row["subject"],
                body=row["body"],
                received_at=datetime.fromisoformat(row["received_at"]),
                order_id=row["order_id"],
            ),
            SupportIntent(row["expected_intent"]),
        )
        for row in sample
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Demo del LLM Gateway con triaje de soporte")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--emails", type=Path, default=ROOT / "data/synthetic/support_emails.jsonl")
    args = parser.parse_args()

    settings = get_settings()
    ledger = RecordingTracer()
    tracer = build_tracer(settings)
    service = SupportTriageService(
        build_gateway(settings, tracer=CompositeTracer([tracer, ledger]))
    )
    emails = load_emails(args.emails, args.limit, args.seed)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def triage(email: SupportEmail, expected: SupportIntent) -> bool | None:
        async with semaphore:
            try:
                result = await service.triage(email)
            except LLMError as exc:
                print(f"  {email.id}  ERROR {type(exc).__name__}: {exc}")
                return None
        response = result.responses[-1]
        ok = result.value.intent is expected
        print(
            f"  {email.id}  {'OK ' if ok else 'MAL'}  esperado={expected:<14} "
            f"predicho={result.value.intent:<14} modelo={response.model:<40} "
            f"{response.latency_ms:>6.0f} ms"
        )
        return ok

    print(
        f"Motor={settings.llm_gateway.engine}  router={settings.llm_gateway.router}  "
        f"proveedores={[str(p) for p in settings.provider_chain()]}\n"
    )
    outcomes = await asyncio.gather(*(triage(email, expected) for email, expected in emails))

    answered = [o for o in outcomes if o is not None]
    print(f"\nAcierto: {sum(answered)}/{len(answered)}  errores: {len(outcomes) - len(answered)}")

    by_route: dict[str, list[tuple[int, int, int, Decimal, float]]] = defaultdict(list)
    for record in ledger.records:
        if record.response is None:
            by_route[f"{record.name} (fallos)"].append((0, 0, 0, Decimal(0), 0.0))
            continue
        usage = record.response.usage
        by_route[f"{record.name} -> {record.response.model}"].append(
            (
                usage.input_tokens,
                usage.cached_input_tokens,
                usage.output_tokens,
                record.cost_usd or Decimal(0),
                record.response.latency_ms,
            )
        )
    print("\nContabilidad por ruta (incluye llamadas del router LLM y reintentos):")
    total = Decimal(0)
    for route, calls in sorted(by_route.items()):
        cost = sum((c[3] for c in calls), Decimal(0))
        total += cost
        print(
            f"  {route:<60} llamadas={len(calls):>3}  in={sum(c[0] for c in calls):>7}  "
            f"cached={sum(c[1] for c in calls):>6}  out={sum(c[2] for c in calls):>6}  "
            f"p50={statistics.median(c[4] for c in calls):>6.0f} ms  ${cost:.6f}"
        )
    print(f"  Coste total: ${total:.6f}")

    flush = getattr(tracer, "flush", None)
    if callable(flush):
        flush()


if __name__ == "__main__":
    asyncio.run(main())
