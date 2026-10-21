"""Prueba de carga del edge (fase 10.7): un pico de salida a la venta de un festival.

    uv run --group load locust -f loadtests/locustfile.py --host http://127.0.0.1:8200 \
        --headless --csv evals/reports/load/protegido --html evals/reports/load/protegido.html

Perfiles de usuario (pesos relativos):
- `Visitor` (público, token propio): pregunta por streaming; las preguntas siguen una
  popularidad tipo Zipf (pocas preguntas muy repetidas: horarios, reembolsos, accesibilidad).
- `Anonymous`: preguntas sin token desde IPs distintas (`X-Forwarded-For`).
- `Staff` (interno): operaciones también pregunta durante el pico.
- `CaseSubmitter`: abre casos con `Idempotency-Key` y reintenta a veces (doble clic).
- `Abuser`: un script que martillea sin pausa (debe recibir 429, no tirar a los demás).

Las respuestas 429/503/504 se cuentan como fallos con su motivo (failures.csv) para el
informe de límites. Al terminar se guarda `/ready` del edge junto a las CSV.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from locust import HttpUser, LoadTestShape, between, constant, events, task
from locust.env import Environment

from edge_api.settings import EdgeSettings
from edge_api.tokens import mint
from headache_core.domain.admission import ClientPlan

CITIES = ["Madrid", "Barcelona", "Lisboa", "London", "Paris", "Milán", "Nueva York", "Chicago"]
TEMPLATES = [
    "¿A qué hora abren las puertas en {city}?",
    "¿Cuándo abren las puertas en {city}?",
    "¿Necesito imprimir la entrada?",
    "¿Hace falta imprimir las entradas?",
    "¿Puedo pedir un reembolso de un concierto a la luz de las velas?",
    "¿Me devuelven el dinero de un concierto con velas si no puedo ir?",
    "¿Hay edad mínima para el ocio nocturno?",
    "¿Puedo cambiar la fecha de una entrada de teatro?",
    "¿Cómo llego al recinto en {city}?",
    "¿Qué horario tiene la atención al cliente en {city}?",
    "¿Se puede ir con niños a una exposición inmersiva?",
    "¿Qué pasa si el organizador cancela la sesión?",
]
QUESTIONS = [t.format(city=c) for t in TEMPLATES for c in (CITIES if "{city}" in t else [""])]
# Popularidad tipo Zipf: la pregunta k-ésima aparece con peso 1/k.
WEIGHTS = [1 / (k + 1) for k in range(len(QUESTIONS))]
EDGE = EdgeSettings()


def question() -> str:
    return random.choices(QUESTIONS, weights=WEIGHTS, k=1)[0]


def bearer(user: str, plan: ClientPlan = ClientPlan.PUBLIC) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(EDGE, user=user, plan=plan, ttl_s=7200)}"}


def random_ip() -> str:
    return f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


def judge(response: Any, ok: tuple[int, ...] = (200,)) -> None:
    """Éxito o fallo con un motivo legible (agrupa failures.csv por causa)."""
    if response.status_code in ok:
        response.success()
        return
    try:
        reason = response.json().get("reason", "")
    except ValueError:
        reason = ""
    response.failure(f"{response.status_code} {reason}".strip())


class Visitor(HttpUser):
    weight = 6
    wait_time = between(2, 6)

    def on_start(self) -> None:
        self.headers = bearer(f"fan_{uuid.uuid4().hex[:10]}")

    @task
    def ask_stream(self) -> None:
        started = time.perf_counter()
        first: float | None = None
        cache = "?"
        with self.client.post(
            "/ask/stream",
            json={"question": question()},
            headers=self.headers,
            stream=True,
            catch_response=True,
            name="/ask/stream",
        ) as response:
            if response.status_code != 200:
                judge(response)
                return
            for raw in response.iter_lines():
                line = raw.decode() if isinstance(raw, bytes) else raw
                if line.startswith("event: delta") and first is None:
                    first = time.perf_counter() - started
                if line.startswith("data: ") and '"cache"' in line:
                    done = json.loads(line[6:])
                    cache = "degraded" if done.get("degraded") else done.get("cache", "?")
                if line.startswith("event: error"):
                    response.failure("stream error")
                    return
            response.success()
        if first is not None:
            # Tiempo hasta el primer trozo: lo que percibe el usuario.
            events.request.fire(
                request_type="SSE",
                name=f"TTFT ({cache})",
                response_time=first * 1000,
                response_length=0,
                exception=None,
                context={},
            )


class Anonymous(HttpUser):
    weight = 2
    # Unas 4 preguntas/min: por debajo del plan anónimo (5/min) salvo rachas.
    wait_time = between(10, 20)

    def on_start(self) -> None:
        self.headers = {"X-Forwarded-For": random_ip()}

    @task
    def ask(self) -> None:
        with self.client.post(
            "/ask", json={"question": question()}, headers=self.headers, catch_response=True
        ) as response:
            judge(response)


class Staff(HttpUser):
    weight = 1
    wait_time = between(5, 10)

    def on_start(self) -> None:
        self.headers = bearer(f"ops_{uuid.uuid4().hex[:8]}", ClientPlan.INTERNAL)

    @task
    def ask(self) -> None:
        with self.client.post(
            "/ask",
            json={"question": question()},
            headers=self.headers,
            catch_response=True,
            name="/ask (interno)",
        ) as response:
            judge(response)


class CaseSubmitter(HttpUser):
    weight = 1
    wait_time = between(5, 15)

    def on_start(self) -> None:
        self.headers = bearer(f"cus_load_{uuid.uuid4().hex[:8]}")

    @task
    def open_case(self) -> None:
        key = f"load-{uuid.uuid4().hex}"
        body = {"subject": "Cambio de fecha", "body": question()}
        # Un 20 % de las veces, el usuario pulsa dos veces: misma clave, mismo caso.
        for _ in range(2 if random.random() < 0.2 else 1):
            with self.client.post(
                "/cases",
                json=body,
                headers={**self.headers, "Idempotency-Key": key},
                catch_response=True,
            ) as response:
                judge(response, ok=(202,))


class Abuser(HttpUser):
    fixed_count = 2
    wait_time = constant(0.05)

    def on_start(self) -> None:
        self.headers = bearer(f"bot_{uuid.uuid4().hex[:6]}")

    @task
    def hammer(self) -> None:
        with self.client.post(
            "/ask",
            json={"question": question()},
            headers=self.headers,
            catch_response=True,
            name="/ask (abuso)",
        ) as response:
            judge(response)


class FestivalOnSale(LoadTestShape):
    """Tráfico normal, la salida a la venta (pico) y la vuelta a la calma."""

    peak = int(os.environ.get("LOAD_PEAK", "200"))
    stages = (
        (30, 0.15),  # Normal.
        (60, 1.0),  # Abre la venta: sube al pico.
        (120, 1.0),  # Pico sostenido.
        (150, 0.15),  # Vuelta a la calma.
    )

    def tick(self) -> tuple[int, float] | None:
        elapsed = self.get_run_time()
        for until, fraction in self.stages:
            if elapsed < until:
                return max(5, math.ceil(self.peak * fraction)), 20.0
        return None


@events.test_stop.add_listener
def save_ready(environment: Environment, **_: Any) -> None:
    """Contadores del edge (admisión, caché, proveedor) junto a las CSV de Locust."""
    options = environment.parsed_options
    prefix = getattr(options, "csv_prefix", None) if options else None
    if not prefix or environment.host is None:
        return
    try:
        with urllib.request.urlopen(f"{environment.host}/ready", timeout=5) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:  # 503 si la réplica sigue llena: el cuerpo vale igual.
        body = exc.read()
    Path(f"{prefix}_ready.json").write_bytes(body)
