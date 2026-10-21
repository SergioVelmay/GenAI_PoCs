"""Plan de capacidad de los workers de casos (fase 10.6): ley de Little y cuota del modelo.

Responde a "¿cuántas réplicas pongo como máximo?" con números, no con intuición. Sin coste.

    uv run python scripts/capacity_plan.py
    uv run python scripts/capacity_plan.py --arrivals 600 --service 8 --tokens 4000 --tpm 150000
"""

from __future__ import annotations

import argparse
import io
import math
import sys

from headache_core.domain.autoscaling import CapacityPlan


def describe(plan: CapacityPlan, backlog: int) -> str:
    lines = [
        f"Llegadas: {plan.arrivals_per_minute:.0f}/min · servicio {plan.service_seconds:.1f} s · "
        f"{plan.concurrency_per_replica} a la vez por réplica · {plan.tokens_per_item} tokens "
        f"por caso · cuota {plan.tokens_per_minute_quota:,} TPM",
        f"- En vuelo (Little): {plan.in_flight:.1f} casos → {plan.replicas_needed} réplica(s) "
        f"al {plan.target_utilization:.0%} de uso.",
        f"- Techo de la cuota: {plan.max_items_per_minute:.0f} casos/min → más de "
        f"{plan.max_useful_replicas} réplica(s) no sirven (solo producen 429).",
    ]
    if plan.quota_bound:
        lines.append(
            "- La cuota manda: llegan más casos de los que el modelo deja procesar. La cola "
            "crece sin parar aunque haya réplicas: hace falta más cuota (otro despliegue o "
            "región), un modelo más barato en tokens o recortar tokens por caso."
        )
    for replicas in sorted({1, plan.replicas_needed, plan.max_useful_replicas}):
        minutes = plan.drain_minutes(backlog, replicas)
        drain = "nunca" if math.isinf(minutes) else f"{minutes:.1f} min"
        lines.append(f"- Vaciar {backlog} casos atrasados con {replicas} réplica(s): {drain}.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arrivals", type=float, help="Casos por minuto en el pico")
    parser.add_argument("--service", type=float, default=8.0, help="Segundos por caso (LLM)")
    parser.add_argument("--concurrency", type=int, default=4, help="Prefetch por réplica")
    parser.add_argument(
        "--tokens", type=int, default=4000, help="Tokens por caso (todas las llamadas)"
    )
    parser.add_argument("--tpm", type=int, default=150_000, help="Cuota del despliegue (TPM)")
    parser.add_argument("--backlog", type=int, default=1000)
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # La consola de Windows no es UTF-8.
    scenarios = (
        [args.arrivals]
        if args.arrivals
        # Día normal, salida a la venta de un festival y pico extremo.
        else [10, 30, 120]
    )
    for arrivals in scenarios:
        plan = CapacityPlan(arrivals, args.service, args.concurrency, args.tokens, args.tpm)
        print(describe(plan, args.backlog), end="\n\n")


if __name__ == "__main__":
    main()
