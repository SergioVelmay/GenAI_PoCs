"""Profundidad de las colas para el autoescalado (fase 10.6).

- RabbitMQ: API HTTP de gestión (`/api/queues/<vhost>/<cola>`), la misma que usa el escalador
  `rabbitmq` de KEDA en modo HTTP: `messages_ready` + `messages_unacknowledged`.
- SQS: `ApproximateNumberOfMessages` + `ApproximateNumberOfMessagesNotVisible` (aproximadas,
  con retardo de hasta ~1 min en CloudWatch; la API directa responde al momento).
- Service Bus (no implementado aquí): `active_message_count` del cliente de administración, lo
  que lee el escalador `azure-servicebus` de KEDA.
- Pub/Sub (10.G1): la API de Pub/Sub **no** dice cuántos mensajes tiene una suscripción; solo lo
  sabe Cloud Monitoring (`subscription/num_unacked_messages_by_region`, muestreada cada 60 s y
  visible hasta 120 s después). Se consulta con **PromQL** por la API de Managed Prometheus: la
  misma consulta que usan CREMA (KEDA `prometheus`, 10.G4) y las alertas (11.G).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import quote

import httpx

from genaipocs_core.config import GcpSettings, PubSubSettings, RabbitMQSettings, SqsSettings
from genaipocs_core.ports.scaling import QueueDepth, QueueUnavailableError

if TYPE_CHECKING:
    from google.auth.credentials import Credentials
    from mypy_boto3_sqs import SQSClient


class RabbitMQManagementMetrics:
    def __init__(self, client: httpx.AsyncClient, *, vhost: str = "/") -> None:
        self._client = client
        self._vhost = quote(vhost, safe="")

    @classmethod
    def from_settings(
        cls, settings: RabbitMQSettings, *, base_url: str = "http://127.0.0.1:15672"
    ) -> Self:
        client = httpx.AsyncClient(
            base_url=base_url,
            auth=(settings.user, settings.password.get_secret_value()),
            timeout=2.0,
        )
        return cls(client)

    async def depth(self, queue: str) -> QueueDepth:
        try:
            response = await self._client.get(f"/api/queues/{self._vhost}/{quote(queue, safe='')}")
        except httpx.HTTPError as exc:
            raise QueueUnavailableError(str(exc)) from exc
        if response.status_code == 404:
            # La cola aún no existe (nadie ha publicado ni se ha suscrito): está vacía.
            return QueueDepth(0)
        if response.status_code != 200:
            raise QueueUnavailableError(f"HTTP {response.status_code}")
        data = response.json()
        return QueueDepth(
            ready=int(data.get("messages_ready", 0)),
            in_flight=int(data.get("messages_unacknowledged", 0)),
            consumers=int(data.get("consumers", 0)),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class SqsQueueMetrics:
    def __init__(self, client: SQSClient) -> None:
        self._client = client
        self._urls: dict[str, str] = {}

    @classmethod
    def from_settings(cls, settings: SqsSettings) -> Self:
        import boto3

        session = boto3.Session(profile_name=settings.profile or None, region_name=settings.region)
        return cls(session.client("sqs", endpoint_url=settings.endpoint_url or None))

    async def depth(self, queue: str) -> QueueDepth:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return await asyncio.to_thread(self._depth, queue)
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailableError(str(exc)) from exc

    def _depth(self, queue: str) -> QueueDepth:
        url = self._urls.get(queue)
        if url is None:
            url = self._urls[queue] = self._client.get_queue_url(QueueName=queue)["QueueUrl"]
        attributes = self._client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )["Attributes"]
        return QueueDepth(
            ready=int(attributes.get("ApproximateNumberOfMessages", 0)),
            in_flight=int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
        )


# Métrica GA de Pub/Sub en notación PromQL de Cloud Monitoring (el primer `/` pasa a `:` y el
# resto de caracteres especiales a `_`). La doc de Pub/Sub recomienda las `_by_region` frente a
# las globales (`num_undelivered_messages`). "Sin confirmar" = esperando + entregados en curso.
PUBSUB_BACKLOG_METRIC = "pubsub_googleapis_com:subscription_num_unacked_messages_by_region"
MONITORING_URL = "https://monitoring.googleapis.com"


def pubsub_subscription_selector(project: str, subscription: str, *, regex: bool = False) -> str:
    """Selector de las métricas de una suscripción (`regex`: varias, p. ej. las `.worker`)."""
    match = "=~" if regex else "="
    return (
        f'monitored_resource="pubsub_subscription",'
        f'project_id="{project}",subscription_id{match}"{subscription}"'
    )


def pubsub_backlog_promql(project: str, subscription_id: str) -> str:
    """Mensajes sin confirmar de una suscripción, sumados entre regiones (fuente única)."""
    return (
        f"sum({PUBSUB_BACKLOG_METRIC}{{{pubsub_subscription_selector(project, subscription_id)}}})"
    )


def pubsub_backlog_by_subscription_promql(project: str, pattern: str) -> str:
    """El mismo backlog para todas las suscripciones que casan con `pattern`, una serie por
    suscripción: lo usan las alertas de Cloud Monitoring (11.G2), una incidencia por cola."""
    selector = pubsub_subscription_selector(project, pattern, regex=True)
    return f"sum by (subscription_id)({PUBSUB_BACKLOG_METRIC}{{{selector}}})"


type TokenProvider = Callable[[], Awaitable[str]]


def google_token_provider(credentials: Credentials) -> TokenProvider:
    """Token de acceso de ADC (o de la cuenta suplantada), renovado en un hilo al caducar."""
    from google.auth.transport.requests import Request

    lock = asyncio.Lock()

    async def token() -> str:
        async with lock:
            if not credentials.valid:
                await asyncio.to_thread(credentials.refresh, Request())
            return str(credentials.token)

    return token


class PubSubBacklogMetrics:
    """Backlog de Pub/Sub desde Cloud Monitoring (PromQL), como el escalador de CREMA.

    - La cola del autoescalador es el **tema**; la suscripción de los workers, `<tema>.<sufijo>`
      (la convención del adaptador de mensajería).
    - `QueueDepth.ready` = mensajes sin confirmar. Cloud Monitoring no separa los que esperan de
      los que un worker tiene en curso: `in_flight` queda a 0 y el total es el mismo que en
      RabbitMQ y SQS (lo que cuenta la regla).
    - **Sin muestra** (huecos de minutos que la doc reconoce) o con una muestra más vieja que
      `max_sample_age_s`: `value_if_null` si se define (como el `valueIfNull` de KEDA); si no,
      `QueueUnavailableError` y el controlador no toca las réplicas.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: TokenProvider,
        *,
        project: str,
        subscription_suffix: str = "worker",
        max_sample_age_s: float = 300.0,
        value_if_null: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._token = token
        self._project = project
        self._suffix = subscription_suffix
        self._max_sample_age_s = max_sample_age_s
        self._value_if_null = value_if_null
        self._clock = clock
        self._path = f"/v1/projects/{project}/location/global/prometheus/api/v1/query"

    @classmethod
    def from_settings(
        cls,
        settings: PubSubSettings,
        gcp: GcpSettings,
        *,
        project: str,
        value_if_null: int | None = None,
    ) -> Self:
        if not project:
            raise ValueError("Falta GCP__PROJECT (o PUBSUB__PROJECT) para leer el backlog.")
        if settings.emulator_host:
            raise ValueError(
                "El emulador de Pub/Sub no publica métricas: el backlog solo existe en "
                "Cloud Monitoring (quita PUBSUB__EMULATOR_HOST)."
            )
        import google.auth

        from genaipocs_core.adapters.gcp_auth import CLOUD_PLATFORM_SCOPE, gcp_credentials

        credentials = gcp_credentials(gcp)
        if credentials is None:
            credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        return cls(
            httpx.AsyncClient(base_url=MONITORING_URL, timeout=5.0),
            google_token_provider(credentials),
            project=project,
            subscription_suffix=settings.subscription_suffix,
            value_if_null=value_if_null,
        )

    def query(self, queue: str) -> str:
        return pubsub_backlog_promql(self._project, f"{queue}.{self._suffix}")

    async def depth(self, queue: str) -> QueueDepth:
        try:
            token = await self._token()
            response = await self._client.get(
                self._path,
                params={"query": self.query(queue)},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise QueueUnavailableError(str(exc)) from exc
        except Exception as exc:  # Credenciales: google.auth lanza sus propias excepciones.
            raise QueueUnavailableError(f"sin token de Google: {exc}") from exc
        if response.status_code != 200:
            raise QueueUnavailableError(f"HTTP {response.status_code}: {response.text[:200]}")
        body: dict[str, Any] = response.json()
        if body.get("status") != "success":
            raise QueueUnavailableError(f"PromQL: {body.get('error', body)}")
        sample = self._latest(body.get("data", {}).get("result", []))
        if sample is None:
            if self._value_if_null is None:
                raise QueueUnavailableError(f"Sin muestras recientes del backlog de {queue}")
            return QueueDepth(self._value_if_null)
        return QueueDepth(sample)

    def _latest(self, result: list[dict[str, Any]]) -> int | None:
        if not result:
            return None
        timestamp, raw = result[0]["value"]
        value = float(raw)
        if math.isnan(value) or self._clock() - float(timestamp) > self._max_sample_age_s:
            return None
        return int(value)

    async def aclose(self) -> None:
        await self._client.aclose()
