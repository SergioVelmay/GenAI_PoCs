"""Nombres de las métricas en Managed Prometheus, comprobados con el collector real (11.G1).

Las reglas de alerta y el dashboard (`adapters/prometheus.py`) usan los nombres que produce el
Prometheus local (`genaipocs_llm_cost_USD_total`). En GCP las métricas van por el exporter
`googlemanagedprometheus` de `infra/otel/collector-gcp.yaml`: si tradujera distinto, las alertas
de Cloud Monitoring (11.G2) se quedarían sin datos y nunca saltarían, en silencio.

Sin nube ni coste: arranca un Cloud Monitoring falso (gRPC `CreateTimeSeries`) en este proceso,
el collector de GCP en Docker apuntando a él (overlay sin trazas ni autenticación), emite un punto
de cada métrica de `metrics.CATALOG` por OTLP y compara cada `metric.type` recibido con el
nombre que espera `adapters/prometheus.metric`.

    uv run python scripts/check_collector_gcp.py
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import grpc
from google.cloud import monitoring_v3

from genaipocs_core.adapters import prometheus
from genaipocs_core.adapters.otel import setup_observability, shutdown_telemetry
from genaipocs_core.application.telemetry import metrics
from genaipocs_core.application.telemetry.metrics import CATALOG, InstrumentKind
from genaipocs_core.config import Settings, TelemetrySettings

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "otel/opentelemetry-collector-contrib:0.163.1"  # La misma que fija la CI.
PREFIX = "prometheus.googleapis.com/"

# Sobre collector-gcp.yaml (con `--config=yaml:`, sin ficheros temporales): métricas al servidor
# falso sin TLS; trazas y logs a `debug` (sin credenciales de Google no se usa `googleclientauth`).
OVERRIDES = (
    "exporters::googlemanagedprometheus::metric::endpoint: host.docker.internal:{port}",
    "exporters::googlemanagedprometheus::metric::use_insecure: true",
    "exporters::debug::verbosity: basic",
    "service::extensions: [health_check]",
    "service::pipelines::traces::processors: [memory_limiter, batch]",
    "service::pipelines::traces::exporters: [debug]",
    "service::pipelines::logs::processors: [memory_limiter, batch]",
    "service::pipelines::logs::exporters: [debug]",
)


def expected_types() -> dict[str, str]:
    """`metric.type` que debería llegar a Cloud Monitoring por cada métrica del catálogo."""
    expected: dict[str, str] = {}
    for name, spec in CATALOG.items():
        if spec.kind is InstrumentKind.HISTOGRAM:
            base = prometheus.metric(name, "count").removesuffix("_count")
            expected[name] = f"{PREFIX}{base}/histogram"
        elif spec.kind is InstrumentKind.COUNTER:
            expected[name] = f"{PREFIX}{prometheus.metric(name)}/counter"
        else:
            expected[name] = f"{PREFIX}{prometheus.metric(name)}/gauge"
    return expected


def emit_catalog() -> None:
    """Un punto de cada métrica, con un atributo cualquiera (los nombres no dependen de él)."""
    for name, spec in CATALOG.items():
        attributes = {"genaipocs.check": "gcp"}
        if spec.kind is InstrumentKind.COUNTER:
            metrics.counter(name).add(1, attributes)
        elif spec.kind is InstrumentKind.HISTOGRAM:
            metrics.histogram(name).record(0.5, attributes)
        else:
            metrics.gauge(name).set(1, attributes)


class FakeMonitoring:
    """Servidor gRPC con `MetricService.CreateTimeSeries`: guarda los tipos recibidos."""

    def __init__(self, port: int) -> None:
        self.types: set[str] = set()
        self.received = threading.Event()
        method = grpc.unary_unary_rpc_method_handler(
            self._create, request_deserializer=bytes, response_serializer=bytes
        )
        handler = grpc.method_handlers_generic_handler(
            "google.monitoring.v3.MetricService", {"CreateTimeSeries": method}
        )
        self.server = grpc.server(ThreadPoolExecutor(max_workers=2))
        self.server.add_generic_rpc_handlers((handler,))
        self.server.add_insecure_port(f"0.0.0.0:{port}")

    def _create(self, request: bytes, _context: grpc.ServicerContext) -> bytes:
        for series in monitoring_v3.CreateTimeSeriesRequest.deserialize(request).time_series:
            self.types.add(series.metric.type)
        self.received.set()
        return b""  # google.protobuf.Empty serializado.


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(port: int, timeout: float) -> None:
    """El health check del collector responde 200 cuando todas las piezas han arrancado."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError("El collector no llegó a estar listo.")


@contextmanager
def collector(image: str, *, grpc_port: int, otlp_port: int, health_port: int) -> Iterator[str]:
    """Collector de GCP en Docker con las métricas apuntando al servidor falso."""
    name = f"genaipocs-collector-gcp-check-{otlp_port}"
    command = [
        "docker", "run", "-d", "--rm", "--name", name,
        "--add-host=host.docker.internal:host-gateway",
        "-p", f"127.0.0.1:{otlp_port}:4318", "-p", f"127.0.0.1:{health_port}:13133",
        "-e", "GOOGLE_CLOUD_PROJECT=genaipocs-check", "-e", "ENVIRONMENT=check",
        "-v", f"{ROOT / 'infra' / 'otel' / 'collector-gcp.yaml'}:/cfg/collector-gcp.yaml:ro",
        image, "--config=/cfg/collector-gcp.yaml",
        *(f"--config=yaml:{line.format(port=grpc_port)}" for line in OVERRIDES),
    ]  # fmt: skip
    subprocess.run(command, check=True, capture_output=True)
    try:
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def run(image: str, timeout: float) -> int:
    grpc_port, otlp_port, health_port = free_port(), free_port(), free_port()
    fake = FakeMonitoring(grpc_port)
    fake.server.start()
    ports = {"grpc_port": grpc_port, "otlp_port": otlp_port, "health_port": health_port}
    try:
        with collector(image, **ports) as name:
            wait_ready(health_port, timeout)
            settings = Settings(
                telemetry=TelemetrySettings(
                    exporter="otlp",
                    otlp_endpoint=f"http://127.0.0.1:{otlp_port}",
                    metrics_interval_seconds=1,
                )
            )
            setup_observability(settings, service_name="collector_gcp_check")
            # Dos rondas separadas por más de un intervalo de exportación: Cloud Monitoring
            # descarta los puntos acumulados con duración cero (inicio = fin), y un solo envío
            # justo al crear el instrumento lo es.
            emit_catalog()
            time.sleep(2.5)
            emit_catalog()
            shutdown_telemetry()  # Vacía la última ventana de métricas.
            fake.received.wait(timeout)
            time.sleep(3)  # El batch del collector puede partir el envío en varias peticiones.
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    finally:
        fake.server.stop(None)

    expected = expected_types()
    missing = {name: kind for name, kind in expected.items() if kind not in fake.types}
    for name, kind in sorted(expected.items()):
        print(f"{'NO' if name in missing else 'OK'}  {name:42} → {kind}")
    if missing:
        print("\nTipos recibidos:", *sorted(fake.types), sep="\n  ")
        print("\nLogs del collector:\n", logs.stdout[-3000:], logs.stderr[-3000:])
        return 1
    print(f"\n{len(expected)} métricas del catálogo con el mismo nombre que en Prometheus local.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Nombres de las métricas en Managed Prometheus")
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    sys.exit(run(args.image, args.timeout))


if __name__ == "__main__":
    main()
