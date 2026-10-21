"""Configuración de los workers del flujo asíncrono (variables `ORCHESTRATOR__*`)."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # rabbitmq en local; sqs (ElasticMQ/AWS) y servicebus (emulador/Azure) para la nube.
    broker: Literal["rabbitmq", "sqs", "servicebus"] = "rabbitmq"
    # inprocess: el worker ejecuta el especialista. a2a: lo llama en su contenedor (A2A__URLS).
    specialists: Literal["inprocess", "a2a"] = "inprocess"
    # RabbitMQ: forman parte de la declaración de la cola (productor y workers deben coincidir).
    max_deliveries: int = 5
    retry_delay_ms: int = 1_000
    # Casos en paralelo por proceso: cada uno es una o varias llamadas al LLM.
    prefetch: int = 4
    # Relay del outbox (fase 10.5): eventos por lote y espera cuando no hay nada pendiente
    # (latencia añadida en el peor caso).
    relay_batch_size: int = 100
    relay_idle_seconds: float = 0.5
