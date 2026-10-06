"""Configuración tipada por entorno. Lee variables de entorno y `.env` (p. ej. BEDROCK__REGION)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    AZURE = "azure"
    AWS = "aws"


class AzureOpenAISettings(BaseModel):
    endpoint: str = ""
    # None => autenticación sin secretos con Entra ID (az login / Managed Identity).
    api_key: SecretStr | None = None
    api_version: str = "2024-10-21"
    # Modelo potente (por defecto) y modelo barato para el model routing.
    chat_deployment: str = "gpt-4.1-mini"
    cheap_chat_deployment: str = "gpt-4.1-nano"
    embedding_deployment: str = "text-embedding-3-small"


class BedrockSettings(BaseModel):
    region: str = "eu-west-1"
    profile: str | None = None
    chat_model_id: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    cheap_chat_model_id: str = "eu.amazon.nova-lite-v1:0"
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"


class LLMGatewaySettings(BaseModel):
    # native: gateway propio (carril B). litellm: Router de LiteLLM (carril C).
    engine: Literal["native", "litellm"] = "native"
    router: Literal["heuristic", "llm"] = "heuristic"
    # Proveedor al que saltar si el principal (`LLM_PROVIDER`) falla. None = sin fallback.
    fallback_provider: LLMProvider | None = None
    max_retries: int = 2
    # Límites en cliente por despliegue; ajústalos a la cuota real (TPM) de tu suscripción.
    requests_per_minute: int = 30
    tokens_per_minute: int = 50_000


class PostgresSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5432
    user: str = "headache"
    password: SecretStr = SecretStr("")
    database: str = "headache"

    def dsn(self, driver: str = "postgresql+asyncpg") -> str:
        password = quote(self.password.get_secret_value(), safe="")
        return f"{driver}://{self.user}:{password}@{self.host}:{self.port}/{self.database}"


class RabbitMQSettings(BaseModel):
    host: str = "localhost"
    port: int = 5672
    user: str = "headache"
    password: SecretStr = SecretStr("")

    def url(self) -> str:
        password = quote(self.password.get_secret_value(), safe="")
        return f"amqp://{self.user}:{password}@{self.host}:{self.port}/"


class LangfuseSettings(BaseModel):
    host: str = "http://localhost:3000"
    public_key: str = ""
    secret_key: SecretStr = SecretStr("")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        # `CLAVE=` vacía en .env equivale a no definirla (se usa el valor por defecto).
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Literal["local", "dev", "pro"] = "local"
    llm_provider: LLMProvider = LLMProvider.AZURE
    azure_openai: AzureOpenAISettings = AzureOpenAISettings()
    bedrock: BedrockSettings = BedrockSettings()
    llm_gateway: LLMGatewaySettings = LLMGatewaySettings()
    postgres: PostgresSettings = PostgresSettings()
    rabbitmq: RabbitMQSettings = RabbitMQSettings()
    langfuse: LangfuseSettings = LangfuseSettings()

    def provider_chain(self) -> list[LLMProvider]:
        """Proveedor principal seguido del de respaldo (si lo hay)."""
        fallback = self.llm_gateway.fallback_provider
        chain = [self.llm_provider]
        if fallback is not None and fallback is not self.llm_provider:
            chain.append(fallback)
        return chain


@lru_cache
def get_settings() -> Settings:
    return Settings()
