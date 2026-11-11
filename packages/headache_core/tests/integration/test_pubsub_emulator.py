"""Smoke test del emulador de Pub/Sub (fase 0.G3): tema, suscripción y un mensaje de ida y vuelta.

El contrato completo del puerto de mensajería (reintentos y *dead-letter topic*) llega con el
adaptador en la 3.G1; aquí solo se comprueba que el emulador del entorno local y de la CI
responde a la librería cliente oficial.

    docker compose --profile pubsub up -d
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator

import pytest
from google.cloud import pubsub_v1

from headache_core.config import get_settings

pytestmark = pytest.mark.integration

# El emulador acepta cualquier proyecto; no hace falta que exista en GCP.
PROJECT = get_settings().pubsub_project() or "headache-local"
EMULATOR_HOST = get_settings().pubsub.emulator_host or "127.0.0.1:8085"


@pytest.fixture
def emulator(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    host, _, port = EMULATOR_HOST.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
    except OSError:
        pytest.skip(f"El emulador de Pub/Sub no está arrancado en {EMULATOR_HOST}")
    # Las librerías de Google leen esta variable: con ella hablan con el emulador, sin TLS ni
    # credenciales. En la nube no se define y usan ADC.
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", EMULATOR_HOST)
    yield


def test_message_round_trip_with_attributes(emulator: None) -> None:
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    name = f"it-{uuid.uuid4().hex[:12]}"
    topic = publisher.topic_path(PROJECT, name)
    subscription = subscriber.subscription_path(PROJECT, name)

    publisher.create_topic(name=topic)
    try:
        subscriber.create_subscription(name=subscription, topic=topic, ack_deadline_seconds=10)
        try:
            # Los atributos son donde viajará el `traceparent` (fase 11.G1), como en SQS.
            publisher.publish(
                topic, b'{"document_id": "doc-1"}', traceparent="00-abc-def-01"
            ).result(timeout=10)

            response = subscriber.pull(subscription=subscription, max_messages=1, timeout=10)

            assert len(response.received_messages) == 1
            received = response.received_messages[0]
            assert received.message.data == b'{"document_id": "doc-1"}'
            assert received.message.attributes["traceparent"] == "00-abc-def-01"
            assert received.delivery_attempt == 0  # Solo se cuenta con dead-letter policy.

            subscriber.acknowledge(subscription=subscription, ack_ids=[received.ack_id])
        finally:
            subscriber.delete_subscription(subscription=subscription)
    finally:
        publisher.delete_topic(topic=topic)
        subscriber.close()
