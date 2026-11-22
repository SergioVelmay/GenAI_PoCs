"""Adaptador Google Cloud Pub/Sub del puerto de mensajería (fase 3.G1). En local, el emulador.

Diferencias de semántica frente a las colas (RabbitMQ, SQS, Service Bus) y cómo se resuelven:
- **Tema y suscripción van separados.** El tema no guarda nada: cada suscripción recibe su copia.
  Los workers comparten la suscripción `<tema>.<sufijo>` (competing consumers, como una cola).
  Publicar en un tema sin suscripciones **pierde** el mensaje: la DLQ es un tema con su propia
  suscripción `<tema>.dlq` para que lo muerto se conserve.
- **Dead-letter policy en la suscripción** (`max_delivery_attempts`, 5 a 100). Sin ella,
  `delivery_attempt` vale 0 y un mensaje envenenado gira para siempre: el adaptador se niega a
  consumir una suscripción sin DLQ (como SQS sin redrive policy).
- **Reintento = nack** (`modifyAckDeadline` a 0). El backoff lo fija la `retry_policy` de la
  suscripción (infraestructura), como la redrive policy en SQS o `MaxDeliveryCount` en Service Bus.
- **No hay "rechazar a la DLQ"**: un error permanente se publica a mano en el dead-letter topic
  y se confirma (como en SQS). Al agotar las entregas, Pub/Sub lo reenvía solo.
- Cliente asíncrono generado (`google.pubsub_v1`), sin hilos. *Pull* unario: el streaming pull de
  la librería de alto nivel (hilos, *flow control* y renovación automática del plazo de ack) es la
  opción de producción con mucho volumen; aquí manda la paridad con los demás adaptadores.
- En la nube, ADC (o *impersonation*) y los recursos los crea Terraform (`pubsub.tf`). Con el
  emulador, el adaptador hace de Terraform y declara tema, suscripción y DLQ (`EmulatorTopology`).
- **Exactly-once delivery** (9.G3, opcional por suscripción): Pub/Sub no reentrega un mensaje
  confirmado con éxito y **el `ack` puede fallar** (`INVALID_ARGUMENT`,
  `EXACTLY_ONCE_ACKID_FAILURE`), p. ej. si el plazo caducó mientras el handler trabajaba. Un
  `ack` o `nack` fallido nunca tumba el bucle: los fallos transitorios se reintentan y el resto
  se registra (`ack_failures`), porque el mensaje puede volver y lo absorbe la idempotencia del
  handler. No elimina los duplicados **al publicar** (llegan con otro `message_id`).
- **Latido del plazo de ack** (10.G1): con *pull* unario nadie renueva el plazo, así que un turno
  más largo que `ack_deadline_seconds` se reentregaba a otro worker mientras el primero seguía
  (y con exactly-once, su `ack` fallaba). Mientras el handler trabaja, se llama a
  `modifyAckDeadline` a mitad de cada plazo (el de la suscripción, leído al suscribirse) hasta
  `max_lease_s`; pasado ese máximo se deja caducar: un handler colgado no retiene el mensaje
  para siempre. Es lo que hace el *lease management* del streaming pull de la librería.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import grpc
from google.api_core import exceptions
from google.pubsub_v1 import PublisherAsyncClient, SubscriberAsyncClient
from google.pubsub_v1.services.publisher.transports.grpc_asyncio import (
    PublisherGrpcAsyncIOTransport,
)
from google.pubsub_v1.services.subscriber.transports.grpc_asyncio import (
    SubscriberGrpcAsyncIOTransport,
)
from google.pubsub_v1.types import (
    DeadLetterPolicy,
    PubsubMessage,
    ReceivedMessage,
    RetryPolicy,
    Subscription,
)

from genaipocs_core.adapters.gcp_auth import gcp_credentials
from genaipocs_core.application.telemetry import metrics
from genaipocs_core.config import GcpSettings, PubSubSettings
from genaipocs_core.ports import Message, MessageHandler, PermanentMessageError
from genaipocs_core.ports.messaging import propagated_headers

logger = logging.getLogger(__name__)

# El emulador acepta cualquier proyecto; no hace falta que exista en GCP.
LOCAL_PROJECT = "genaipocs-local"
DEAD_LETTER_SUFFIX = "dlq"


def topic_path(project: str, topic: str) -> str:
    # Pub/Sub admite puntos en los IDs: el tema se llama igual que en RabbitMQ y Service Bus.
    return f"projects/{project}/topics/{topic}"


def subscription_path(project: str, topic: str, suffix: str) -> str:
    return f"projects/{project}/subscriptions/{topic}.{suffix}"


@dataclass(frozen=True, slots=True)
class EmulatorTopology:
    """Lo que en la nube declara `pubsub.tf`. Solo se usa contra el emulador."""

    max_delivery_attempts: int = 5
    ack_deadline_s: int = 60
    # Backoff entre entregas de la retry policy (en la nube, de 10 a 600 s por defecto).
    min_backoff_s: int = 0
    max_backoff_s: int = 1
    exactly_once: bool = False


# Con exactly-once, el servidor explica cada `ack_id` fallido en los metadatos del error:
# `PERMANENT_FAILURE_INVALID_ACK_ID` (caducado o ya confirmado) o `TRANSIENT_FAILURE_*`.
EXACTLY_ONCE_ACK_FAILURE = "EXACTLY_ONCE_ACKID_FAILURE"
# `messaging.system` de la convención de OTel (el mismo que `messaging_factory.MESSAGING_SYSTEMS`).
MESSAGING_SYSTEM = "gcp_pubsub"
TRANSIENT_ACK_FAILURE = "TRANSIENT_FAILURE"
ACK_ATTEMPTS = 3
# Plazo de ack por defecto de Pub/Sub si la suscripción no trae uno.
DEFAULT_ACK_DEADLINE_S = 10


class PubSubMessageBus:
    def __init__(
        self,
        publisher: PublisherAsyncClient,
        subscriber: SubscriberAsyncClient,
        *,
        project: str,
        subscription_suffix: str = "worker",
        max_messages: int = 10,
        pull_timeout_s: float = 10.0,
        topology: EmulatorTopology | None = None,
        max_lease_s: float = 3600.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._publisher = publisher
        self._subscriber = subscriber
        self._project = project
        self._suffix = subscription_suffix
        self._max_messages = max_messages
        self._pull_timeout_s = pull_timeout_s
        self._topology = topology
        self._max_lease_s = max_lease_s
        self._sleep = sleep
        self._clock = clock
        self._declared: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        # `ack`/`nack` que Pub/Sub no aceptó: el mensaje puede volver a entregarse.
        self.ack_failures = 0
        # Renovaciones del plazo hechas y rechazadas (latido, 10.G1).
        self.lease_extensions = 0
        self.lease_failures = 0

    @classmethod
    def from_settings(
        cls,
        settings: PubSubSettings,
        gcp: GcpSettings,
        *,
        project: str,
        max_messages: int = 10,
    ) -> Self:
        if settings.emulator_host:
            # Los clientes generados no leen PUBSUB_EMULATOR_HOST (solo la librería de alto
            # nivel): el canal sin TLS ni credenciales se construye a mano.
            host = settings.emulator_host
            return cls(
                PublisherAsyncClient(
                    transport=PublisherGrpcAsyncIOTransport(channel=grpc.aio.insecure_channel(host))
                ),
                SubscriberAsyncClient(
                    transport=SubscriberGrpcAsyncIOTransport(
                        channel=grpc.aio.insecure_channel(host)
                    )
                ),
                project=project or LOCAL_PROJECT,
                subscription_suffix=settings.subscription_suffix,
                max_messages=max_messages,
                topology=EmulatorTopology(
                    max_delivery_attempts=settings.max_delivery_attempts,
                    ack_deadline_s=settings.ack_deadline_s,
                    exactly_once=settings.exactly_once,
                ),
                max_lease_s=settings.max_lease_s,
            )
        if not project:
            raise ValueError("Falta GCP__PROJECT (o PUBSUB__PROJECT) para Pub/Sub en la nube.")
        credentials = gcp_credentials(gcp)
        return cls(
            PublisherAsyncClient(credentials=credentials),
            SubscriberAsyncClient(credentials=credentials),
            project=project,
            subscription_suffix=settings.subscription_suffix,
            max_messages=max_messages,
            max_lease_s=settings.max_lease_s,
        )

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Message:
        message = Message(topic=topic, payload=payload, headers=propagated_headers(headers))
        await self._declare(topic)
        # Nuestro id y la fecha viajan como atributos (Pub/Sub pone los suyos, pero el contrato
        # exige el id que devolvió `publish`); también el contexto de traza W3C (fase 11.G1).
        attributes = {
            "message_id": message.id,
            "published_at": message.published_at.isoformat(),
            **message.headers,
        }
        await self._publisher.publish(
            topic=topic_path(self._project, topic),
            messages=[
                PubsubMessage(
                    data=json.dumps(dict(payload), ensure_ascii=False).encode(),
                    attributes=attributes,
                )
            ],
        )
        return message

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        await self._declare(topic)
        subscription = subscription_path(self._project, topic, self._suffix)
        config = await self._subscriber.get_subscription(subscription=subscription)
        dead_letter_topic = _dead_letter_topic(subscription, config)
        ack_deadline_s = config.ack_deadline_seconds or DEFAULT_ACK_DEADLINE_S
        task = asyncio.create_task(
            self._poll(topic, subscription, dead_letter_topic, handler, ack_deadline_s)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        # El transporte generado no anota `close`.
        await self._publisher.transport.close()  # type: ignore[no-untyped-call]
        await self._subscriber.transport.close()  # type: ignore[no-untyped-call]

    async def _poll(
        self,
        topic: str,
        subscription: str,
        dead_letter_topic: str,
        handler: MessageHandler,
        ack_deadline_s: int,
    ) -> None:
        while True:
            try:
                response = await self._subscriber.pull(
                    subscription=subscription,
                    max_messages=self._max_messages,
                    timeout=self._pull_timeout_s,
                )
            except exceptions.DeadlineExceeded:
                # *Long polling* sin mensajes: se vuelve a pedir.
                continue
            await asyncio.gather(
                *(
                    self._handle(
                        topic,
                        subscription,
                        dead_letter_topic,
                        raw,
                        handler,
                        ack_deadline_s=ack_deadline_s,
                    )
                    for raw in response.received_messages
                )
            )

    async def _handle(
        self,
        topic: str,
        subscription: str,
        dead_letter_topic: str,
        raw: ReceivedMessage,
        handler: MessageHandler,
        *,
        ack_deadline_s: int | None = None,
    ) -> None:
        # Con dead-letter policy, `delivery_attempt` empieza en 1 (es aproximado en la nube).
        attempt = raw.delivery_attempt or 1
        attributes = dict(raw.message.attributes)
        message_id = attributes.get("message_id") or raw.message.message_id
        try:
            payload = json.loads(raw.message.data)
        except ValueError as exc:
            await self._dead_letter(subscription, dead_letter_topic, raw, message_id, exc)
            return
        if not isinstance(payload, dict):
            await self._dead_letter(
                subscription, dead_letter_topic, raw, message_id, "no es un objeto JSON"
            )
            return
        published = attributes.get("published_at")
        try:
            # El latido se para antes de confirmar: el `ack` nunca compite con una renovación.
            async with self._lease(subscription, raw, message_id, ack_deadline_s):
                await handler(
                    Message(
                        topic=topic,
                        payload=payload,
                        id=message_id,
                        published_at=datetime.fromisoformat(published)
                        if published
                        else raw.message.publish_time or datetime.now(UTC),
                        delivery_count=attempt,
                        headers=propagated_headers(attributes),
                    )
                )
        except PermanentMessageError as exc:
            await self._dead_letter(subscription, dead_letter_topic, raw, message_id, exc)
        except Exception:
            logger.exception("Fallo transitorio en %s (entrega %d)", message_id, attempt)
            # Nack: vuelve a entregarse tras el backoff de la retry policy. Al superar
            # max_delivery_attempts, Pub/Sub lo reenvía solo al dead-letter topic.
            await self._nack(subscription, raw, message_id)
        else:
            await self._ack(subscription, raw, message_id)

    @contextlib.asynccontextmanager
    async def _lease(
        self,
        subscription: str,
        raw: ReceivedMessage,
        message_id: str,
        ack_deadline_s: int | None,
    ) -> AsyncIterator[None]:
        """Renueva el plazo de ack mientras dura el bloque (sin plazo conocido, no hace nada)."""
        if not ack_deadline_s or self._max_lease_s <= 0:
            yield
            return
        task = asyncio.create_task(self._keep_lease(subscription, raw, message_id, ack_deadline_s))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _keep_lease(
        self, subscription: str, raw: ReceivedMessage, message_id: str, ack_deadline_s: int
    ) -> None:
        # A mitad del plazo: deja margen para una renovación lenta o un reintento.
        interval = ack_deadline_s / 2
        started = self._clock()
        while True:
            await self._sleep(interval)
            if self._clock() - started >= self._max_lease_s:
                logger.warning(
                    "%s supera el latido máximo (%.0f s): se deja caducar y se reentregará.",
                    message_id,
                    self._max_lease_s,
                )
                return
            try:
                await self._subscriber.modify_ack_deadline(
                    subscription=subscription,
                    ack_ids=[raw.ack_id],
                    ack_deadline_seconds=ack_deadline_s,
                )
            except exceptions.GoogleAPICallError as exc:
                self.lease_failures += 1
                _settle_failed(subscription, "modack")
                if exc.reason == EXACTLY_ONCE_ACK_FAILURE and not _transient_ack_failure(exc):
                    # El `ack_id` ya no vale (caducó antes del latido): renovar no sirve, el
                    # mensaje volverá y lo absorbe la idempotencia del handler.
                    logger.warning("Latido de %s rechazado (%s): se deja.", message_id, exc.reason)
                    return
                # Transitorio (red, TRANSIENT_FAILURE_*): se reintenta en el siguiente periodo.
                logger.warning("Latido de %s fallido (%s): se reintenta.", message_id, exc)
            else:
                self.lease_extensions += 1

    async def _ack(self, subscription: str, raw: ReceivedMessage, message_id: str) -> None:
        await self._settle(
            "ack",
            subscription,
            message_id,
            lambda: self._subscriber.acknowledge(subscription=subscription, ack_ids=[raw.ack_id]),
        )

    async def _nack(self, subscription: str, raw: ReceivedMessage, message_id: str) -> None:
        await self._settle(
            "nack",
            subscription,
            message_id,
            lambda: self._subscriber.modify_ack_deadline(
                subscription=subscription, ack_ids=[raw.ack_id], ack_deadline_seconds=0
            ),
        )

    async def _settle(
        self,
        operation: str,
        subscription: str,
        message_id: str,
        call: Callable[[], Awaitable[object]],
    ) -> None:
        """Confirma o devuelve un mensaje sin tumbar el bucle de *pull* si Pub/Sub lo rechaza."""
        for attempt in range(1, ACK_ATTEMPTS + 1):
            try:
                await call()
                return
            except exceptions.GoogleAPICallError as exc:
                if _transient_ack_failure(exc) and attempt < ACK_ATTEMPTS:
                    continue
                self.ack_failures += 1
                _settle_failed(subscription, operation)
                # Caducado o ya confirmado (exactly-once), o la red: puede volver a entregarse.
                # Lo absorbe la idempotencia del handler (p. ej. las transiciones del CaseStore).
                logger.warning(
                    "%s de %s no aceptado (%s): puede volver a entregarse.",
                    operation,
                    message_id,
                    exc.reason or type(exc).__name__,
                )
                return

    async def _dead_letter(
        self,
        subscription: str,
        dead_letter_topic: str,
        raw: ReceivedMessage,
        message_id: str,
        reason: object,
    ) -> None:
        logger.error("Mensaje %s irrecuperable, a la DLQ: %s", message_id, reason)
        attributes = dict(raw.message.attributes)
        attributes |= {"message_id": message_id, "dead_letter_reason": str(reason)[:1024]}
        await self._publisher.publish(
            topic=dead_letter_topic,
            messages=[PubsubMessage(data=raw.message.data, attributes=attributes)],
        )
        # Primero se publica y después se confirma: si algo falla entre medias, se duplica en
        # la DLQ (at-least-once), nunca se pierde.
        await self._ack(subscription, raw, message_id)

    async def _declare(self, topic: str) -> None:
        """Emulador: tema, DLQ con su suscripción y suscripción de los workers (idempotente)."""
        if self._topology is None or topic in self._declared:
            return
        topology = self._topology
        main = topic_path(self._project, topic)
        dead_letter = topic_path(self._project, f"{topic}.{DEAD_LETTER_SUFFIX}")
        await self._ignore_existing(self._publisher.create_topic(name=main))
        await self._ignore_existing(self._publisher.create_topic(name=dead_letter))
        await self._ignore_existing(
            self._subscriber.create_subscription(
                name=subscription_path(self._project, topic, DEAD_LETTER_SUFFIX),
                topic=dead_letter,
            )
        )
        await self._ignore_existing(
            self._subscriber.create_subscription(
                request=Subscription(
                    name=subscription_path(self._project, topic, self._suffix),
                    topic=main,
                    ack_deadline_seconds=topology.ack_deadline_s,
                    dead_letter_policy=DeadLetterPolicy(
                        dead_letter_topic=dead_letter,
                        max_delivery_attempts=topology.max_delivery_attempts,
                    ),
                    retry_policy=RetryPolicy(
                        minimum_backoff=timedelta(seconds=topology.min_backoff_s),
                        maximum_backoff=timedelta(seconds=topology.max_backoff_s),
                    ),
                    enable_exactly_once_delivery=topology.exactly_once,
                )
            )
        )
        self._declared.add(topic)

    @staticmethod
    async def _ignore_existing(call: Awaitable[object]) -> None:
        with contextlib.suppress(exceptions.AlreadyExists):
            await call


def _dead_letter_topic(subscription: str, config: Subscription) -> str:
    topic = config.dead_letter_policy.dead_letter_topic
    if not topic:
        raise ValueError(
            f"La suscripción {subscription} no tiene dead-letter policy: sin ella, los "
            "mensajes envenenados se reintentan para siempre."
        )
    return topic


def _transient_ack_failure(exc: exceptions.GoogleAPICallError) -> bool:
    """Exactly-once: el servidor marca cada `ack_id` como fallo permanente o transitorio."""
    if exc.reason != EXACTLY_ONCE_ACK_FAILURE:
        return False
    metadata = exc.metadata or {}
    return any(str(value).startswith(TRANSIENT_ACK_FAILURE) for value in metadata.values())


def _settle_failed(subscription: str, operation: str) -> None:
    """Métrica de la 11.G2 (alerta `MessageSettleFailures`): la suscripción sin el proyecto."""
    metrics.message_settle_failed(
        system=MESSAGING_SYSTEM, subscription=subscription.rsplit("/", 1)[-1], operation=operation
    )
