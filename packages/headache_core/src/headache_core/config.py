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
    chat_deployment: str = "gpt-4.1-mini"
    embedding_deployment: str = "text-embedding-3-small"


class BedrockSettings(BaseModel):
    region: str = "eu-west-1"
    profile: str | None = None
    chat_model_id: str = "eu.amazon.nova-lite-v1:0"
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"


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
        extra="ignore",
    )

    environment: Literal["local", "dev", "pro"] = "local"
    llm_provider: LLMProvider = LLMProvider.AZURE
    azure_openai: AzureOpenAISettings = AzureOpenAISettings()
    bedrock: BedrockSettings = BedrockSettings()
    postgres: PostgresSettings = PostgresSettings()
    rabbitmq: RabbitMQSettings = RabbitMQSettings()
    langfuse: LangfuseSettings = LangfuseSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
