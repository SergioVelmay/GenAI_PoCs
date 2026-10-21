"""Autoescalador local por longitud de cola (fase 10.6): lo que KEDA hace en ACA/AKS.

Lee la profundidad de una cola de RabbitMQ (API de gestión) y ajusta las réplicas de un servicio
de docker compose. Por defecto en *dry-run* (solo informa); `--apply` mueve réplicas.

    docker compose up -d rabbitmq
    docker compose --profile scale build queue-worker
    uv run python scripts/queue_autoscaler.py --apply --duration 210 \
        --report evals/reports/autoscaling.md
    uv run python -m orchestrator.load_worker --publish 1200   # en otra terminal: la ráfaga
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import sys
import time
from pathlib import Path

from headache_core.adapters.compose_scaler import DockerComposeScaler
from headache_core.adapters.queue_metrics import RabbitMQManagementMetrics
from headache_core.application.autoscaler import QueueAutoscaler, ScalingEvent, ScalingTarget
from headache_core.config import get_settings
from headache_core.domain.autoscaling import ScalingPolicy


def render(events: list[ScalingEvent], policy: ScalingPolicy, started: float) -> str:
    lines = [
        "# Autoescalado por cola (demo local)",
        "",
        f"Política: objetivo {policy.target_per_replica} mensajes por réplica, "
        f"{policy.min_replicas}-{policy.max_replicas} réplicas, activación {policy.activation}, "
        f"ventana de bajada {policy.scale_down_window_seconds:.0f} s, "
        f"enfriamiento {policy.cooldown_seconds:.0f} s.",
        "",
        "| t (s) | Mensajes | Pide la fórmula | Réplicas antes | Réplicas después | Motivo |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    previous: tuple[int, int, int] | None = None
    for event in events:
        d = event.decision
        row = (d.messages, d.current, d.desired)
        if row == previous and not d.changed:
            continue  # Sin cambios: no repetir filas idénticas.
        previous = row
        lines.append(
            f"| {event.at - started:.0f} | {d.messages} | {d.raw} | {d.current} | {d.desired} "
            f"| {d.reason} |"
        )
    peak = max((e.decision.desired for e in events), default=0)
    lines += ["", f"Pico de réplicas: {peak}."]
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default="demo.jobs")
    parser.add_argument("--service", default="queue-worker")
    parser.add_argument("--target", type=int, default=20, help="Mensajes por réplica")
    parser.add_argument("--min", type=int, default=0)
    parser.add_argument("--max", type=int, default=8)
    parser.add_argument("--activation", type=int, default=0)
    parser.add_argument("--window", type=float, default=30.0, help="Ventana de bajada (s)")
    parser.add_argument("--cooldown", type=float, default=45.0, help="Enfriamiento a cero (s)")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--duration", type=float, help="Segundos y termina (si no, Ctrl+C)")
    parser.add_argument("--apply", action="store_true", help="Mover réplicas (si no, dry-run)")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")  # La consola de Windows no es UTF-8.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    policy = ScalingPolicy(
        target_per_replica=args.target,
        min_replicas=args.min,
        max_replicas=args.max,
        activation=args.activation,
        scale_down_window_seconds=args.window,
        cooldown_seconds=args.cooldown,
    )
    metrics = RabbitMQManagementMetrics.from_settings(get_settings().rabbitmq)
    controller = QueueAutoscaler(
        metrics,
        DockerComposeScaler(),
        [ScalingTarget(args.queue, args.service, policy)],
        dry_run=not args.apply,
    )
    stop = asyncio.Event()
    started = time.monotonic()
    if args.duration:
        asyncio.get_running_loop().call_later(args.duration, stop.set)
    try:
        await controller.run(stop, interval=args.interval)
    finally:
        await metrics.aclose()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                render(controller.history, policy, started), encoding="utf-8", newline="\n"
            )
            print(f"Informe: {args.report}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
