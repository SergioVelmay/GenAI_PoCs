from __future__ import annotations

import configparser
import socket
import ssl
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.config import Config

from genaipocs_core.config import ScaleRedisSettings, Settings
from genaipocs_core.domain import (
    Customer,
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    Session,
)

SESSION_START = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)


# Valkey con búsqueda (10.G2): servicio `valkey-scale` del perfil `scale` y de la CI.
VALKEY_PORT = 6381


@pytest.fixture(params=["redis", "valkey"])
def scale_redis(request: pytest.FixtureRequest) -> ScaleRedisSettings:
    """Redis de la fase 10 (`docker compose --profile scale up -d`), una vez con Redis 8 y otra
    con Valkey (el motor de Memorystore). Si no está, el test se salta, como los contratos de
    colas sin su emulador (antes fallaba con `CancelledError`)."""
    settings = Settings().scale_redis
    if request.param == "valkey":
        settings = settings.model_copy(update={"port": VALKEY_PORT})
    try:
        socket.create_connection((settings.host, settings.port), timeout=0.5).close()
    except OSError:
        where = f"{settings.host}:{settings.port}"
        pytest.skip(f"{request.param} de escala no está arrancado en {where}")
    return settings


# AWS Emulator oficial (checkpoint G→13.2): `docker compose --profile aws up -d`. En cada arranque
# genera un certificado y unas claves SigV4 que escribe en `.aws-emulator/9297/` (montado desde el
# contenedor). Los clientes se construyen con esos ficheros de forma explícita: no dependen de
# `AWS_*` del shell, y unas credenciales reales nunca llegan a usarse en estos tests.
AWS_EMULATOR_URL = "https://127.0.0.1:9297"
AWS_EMULATOR_DIR = Path(__file__).resolve().parents[3] / ".aws-emulator" / "9297"
# Una región cualquiera: el emulador es de una región y una cuenta (000000000000).
AWS_EMULATOR_REGION = "eu-west-1"


def _aws_emulator_healthy(ca_file: Path) -> bool:
    context = ssl.create_default_context(cafile=str(ca_file))
    try:
        with urllib.request.urlopen(
            f"{AWS_EMULATOR_URL}/__admin/health", context=context, timeout=2
        ) as response:
            return bool(response.status == 200)
    except OSError:
        return False


@pytest.fixture(scope="session")
def aws_emulator() -> Callable[[str], Any]:
    """Factoría `servicio -> cliente boto3` contra el emulador; salta si no está arrancado."""
    ca_file = AWS_EMULATOR_DIR / "ca.pem"
    credentials_file = AWS_EMULATOR_DIR / "emulator_credentials"
    if not (ca_file.exists() and credentials_file.exists() and _aws_emulator_healthy(ca_file)):
        pytest.skip(f"AWS Emulator no está arrancado en {AWS_EMULATOR_URL}")
    credentials = configparser.ConfigParser()
    credentials.read(credentials_file)
    session = boto3.Session(
        aws_access_key_id=credentials["default"]["aws_access_key_id"],
        aws_secret_access_key=credentials["default"]["aws_secret_access_key"],
        region_name=AWS_EMULATOR_REGION,
    )
    # Un solo endpoint por IP para todos los servicios: sin prefijos de host (`data-`, `sync-`)
    # y S3 en path style (el emulador rechaza el bucket en el nombre de host).
    config = Config(inject_host_prefix=False, s3={"addressing_style": "path"})

    def client(service: str) -> Any:
        return session.client(
            service,  # type: ignore[call-overload]
            endpoint_url=AWS_EMULATOR_URL,
            verify=str(ca_file),
            config=config,
        )

    return client


@pytest.fixture
def customer() -> Customer:
    return Customer("cus_1", "Ana Pérez", "ana@example.com", "ES", "es")


@pytest.fixture
def event() -> Event:
    return Event(
        id="evt_1",
        title="Candlelit Strings: Vivaldi's Four Seasons",
        category=EventCategory.CANDLELIT,
        city="Madrid",
        venue_id="ven_1",
        description="Concierto a la luz de las velas.",
        price=Money(Decimal("30.00"), "EUR"),
        refundable=True,
        refund_window_hours=48,
    )


@pytest.fixture
def session() -> Session:
    return Session(
        id="ses_1", event_id="evt_1", starts_at=SESSION_START, capacity=200, available=50
    )


@pytest.fixture
def order() -> Order:
    return Order(
        id="ord_1",
        customer_id="cus_1",
        session_id="ses_1",
        quantity=2,
        total=Money(Decimal("60.00"), "EUR"),
        status=OrderStatus.PAID,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
