"""Configuración del edge (`EDGE__*`). Por defecto, local y sin coste."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EdgeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE__",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )
    # Peticiones en curso por réplica. Por encima de su parte (dominio), cada plan recibe 503.
    capacity: int = Field(default=64, ge=1)
    # Tiempo máximo de una petición: acota reintentos, esperas de cuota y la llamada al modelo.
    request_budget_seconds: float = Field(default=20.0, gt=0)
    overloaded_retry_after_seconds: float = Field(default=1.0, gt=0)
    # jwt: bearer opcional (sin token = anónimo por IP). none: todos anónimos (solo pruebas).
    auth: Literal["jwt", "none"] = "jwt"
    issuer: str = "https://auth.headache.local"
    audience: str = "api://headache-edge"
    plan_claim: str = "plan"
    # Nube: JWKS de Entra ID / Cognito. Local: clave pública o la del emisor de desarrollo.
    jwks_uri: str = ""
    public_key_file: str = ""
    dev_private_key_file: str = ".secrets/mcp-dev-key.pem"
    # extractive: citas literales sin modelo (coste 0). llm-extractive: el modelo elige citas
    # (coste). generative: el modelo redacta en streaming con citas verificadas al final (coste).
    answer_mode: Literal["extractive", "llm-extractive", "generative"] = "extractive"
    # Plan B (10.7): con el modelo saturado, responder con citas literales en vez de un 503.
    degrade_on_saturation: bool = True
    # Cada cuánto se manda un comentario SSE si no hay datos (proxies que cortan conexiones
    # inactivas, típicamente a los 60 s).
    heartbeat_seconds: float = Field(default=15.0, gt=0)
    # Casos (fase 10.5). postgres: altas idempotentes y estado compartidos con los workers
    # (conecta al primer uso). memory: solo pruebas. disabled: /cases responde 503.
    cases_backend: Literal["postgres", "memory", "disabled"] = "postgres"
    cases_topic: str = "support.emails"
    # Bulkhead propio de /cases (fase 10.7): un alta es barata (una transacción) y no debe
    # quedarse sin hueco porque las preguntas al modelo ocupen toda la capacidad.
    cases_capacity: int = Field(default=32, ge=1)
    case_poll_seconds: float = Field(default=1.0, gt=0)
    # Tiempo máximo de un stream de seguimiento; después, `event: timeout` y el cliente reconecta.
    case_stream_seconds: float = Field(default=300.0, gt=0)
