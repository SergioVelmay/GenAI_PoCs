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
    # Respuesta con LLM (llm-extractive, coste) o extractiva (sin modelo).
    generate: bool = False
