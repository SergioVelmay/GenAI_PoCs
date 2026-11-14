"""Configuración tipada por entorno. Lee variables de entorno y `.env` (p. ej. BEDROCK__REGION)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    AZURE = "azure"
    AWS = "aws"
    # Pasada GCP: Gemini desde la 2.G1. Lo que aún no tiene adaptador (juez, embeddings...)
    # falla con un mensaje explícito en vez de caer de forma silenciosa en otro proveedor.
    GCP = "gcp"
    # Fase 10.7: proveedor simulado para pruebas de carga (latencia y cuota realistas, coste 0).
    SIMULATED = "simulated"


class SimulatedLLMSettings(BaseModel):
    """El proveedor simulado imita un despliegue real: tiempo hasta el primer token, tokens por
    segundo y una cuota TPM/RPM que devuelve 429 con `retry_after` al agotarse."""

    model: str = "gpt-4.1-mini-simulated"  # Precio de gpt-4.1-mini: coste equivalente.
    ttft_ms: int = 450
    tokens_per_second: float = 80.0
    output_tokens: int = 90
    # Cuota del "despliegue" simulado. Con RATE_LIMIT__BACKEND=redis la comparten las réplicas.
    tokens_per_minute: int = 200_000
    requests_per_minute: int = 1_200
    # Fallos 5xx aleatorios (0 = ninguno) para ver el breaker y los reintentos.
    error_rate: float = 0.0


class AzureOpenAISettings(BaseModel):
    endpoint: str = ""
    # None => autenticación sin secretos con Entra ID (az login / Managed Identity).
    api_key: SecretStr | None = None
    api_version: str = "2024-10-21"
    # Modelo potente (por defecto) y modelo barato para el model routing.
    chat_deployment: str = "gpt-4.1-mini"
    cheap_chat_deployment: str = "gpt-4.1-nano"
    embedding_deployment: str = "text-embedding-3-small"
    # Juez de evals: modelo distinto y más capaz que el generador (sesgo de autopreferencia).
    judge_deployment: str = "gpt-4.1"


class BedrockSettings(BaseModel):
    region: str = "eu-west-1"
    profile: str | None = None
    chat_model_id: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    cheap_chat_model_id: str = "eu.amazon.nova-lite-v1:0"
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    judge_model_id: str = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    # Bedrock Rerank (p. ej. Cohere Rerank 3.5); la disponibilidad depende de la region.
    rerank_model_arn: str = ""


class GcpSettings(BaseModel):
    """Proyecto, región y credenciales de GCP (fase 0.G1).

    El proyecto es la unidad de aislamiento y de facturación: el equivalente de un resource
    group de Azure, pero con su propia cuota y sus APIs habilitadas una a una. Nunca hay
    claves JSON de cuenta de servicio: en local, ADC de usuario (`gcloud auth
    application-default login`); en la nube, la cuenta de servicio adjunta al recurso; en la
    CI, Workload Identity Federation.
    """

    project: str = ""
    # Región de los recursos (Pub/Sub, Cloud Storage, Vector Search, RAG Engine).
    region: str = "europe-west4"
    # Cuenta de servicio a suplantar desde local (impersonation). Vacío = la identidad de ADC.
    impersonate_service_account: str = ""


# Profundidad de razonamiento de Gemini 3+/4 (sustituye a `thinking_budget`). Vacío = el valor
# por defecto del modelo. Cada modelo admite un subconjunto: `minimal` da 400 en Gemini 4.
type ThinkingLevel = Literal["", "minimal", "low", "medium", "high"]


class GeminiSettings(BaseModel):
    """Modelos de Gemini (fase 2.G1). El acceso es con `google-genai`, nunca con
    `vertexai.generative_models` (retirado el 24-06-2026)."""

    # True = Gemini Enterprise Agent Platform (antes Vertex AI): el proyecto paga y manda IAM.
    # False = la Gemini Developer API con clave (solo desarrollo).
    enterprise: bool = True
    # `global` sirve todos los modelos y es el más barato; `eu` (multirregión, ~+10 %) garantiza
    # residencia de datos en la UE. Gemini 4 no se sirve en regiones sueltas como europe-west4.
    location: str = "global"
    # Solo para la Gemini Developer API (enterprise=false); con Agent Platform se deja vacío.
    api_key: SecretStr | None = None
    # Modelo potente (por defecto) y modelo barato para el model routing (verificado 14-11-2026).
    chat_model: str = "gemini-4-hydrogen"
    cheap_chat_model: str = "gemini-3.5-flash-lite"
    # Por defecto, Hydrogen piensa en `high` (salida más cara y lenta) y Flash-Lite en `minimal`
    # (puede cortar antes de tiempo un bucle de tools). `low` en los dos: se calibra en la nube.
    thinking_level: ThinkingLevel = "low"
    cheap_thinking_level: ThinkingLevel = "low"
    embedding_model: str = "gemini-embedding-001"
    # Juez de evals: distinto y más capaz que el generador (sesgo de autopreferencia); 5.G1.
    judge_model: str = "gemini-4-argon"
    # Tiempo máximo de una llamada (el SDK no reintenta: los reintentos son del gateway).
    timeout_seconds: float = 120.0


class PubSubSettings(BaseModel):
    """Pub/Sub (fase 3.G1). El emulador local se activa con `PUBSUB__EMULATOR_HOST`."""

    # Vacío = Pub/Sub real con ADC. En local, 127.0.0.1:8085 (el emulador escucha por defecto
    # en el localhost del contenedor: se arranca con --host-port=0.0.0.0:8085).
    emulator_host: str = ""
    # Vacío = el proyecto de `GCP__PROJECT`; el emulador acepta cualquiera.
    project: str = ""
    ack_deadline_s: int = 60
    max_messages: int = 10


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
    # Resiliencia bajo carga (fase 10.1), por despliegue y por proceso.
    max_concurrency: int = 16
    # Espera máxima por cuota o por hueco antes de rechazar (backpressure). None = sin límite.
    max_queue_seconds: float | None = 10.0
    circuit_failure_threshold: int = 5
    circuit_reset_seconds: float = 30.0
    # Un Retry-After más largo no se espera: se salta de proveedor o se devuelve al cliente.
    max_retry_after_seconds: float = 5.0


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
    host: str = "127.0.0.1"
    port: int = 5672
    user: str = "headache"
    password: SecretStr = SecretStr("")

    def url(self) -> str:
        password = quote(self.password.get_secret_value(), safe="")
        return f"amqp://{self.user}:{password}@{self.host}:{self.port}/"


class ScaleRedisSettings(BaseModel):
    """Redis de la fase 10 (rate limiting y caché semántica), distinto del de Langfuse."""

    host: str = "127.0.0.1"
    port: int = 6380
    db: int = 0
    password: SecretStr = SecretStr("")
    timeout_seconds: float = 0.25
    max_connections: int = 64


class RateLimitSettings(BaseModel):
    # memory: por proceso (tests y una réplica). redis: compartido por todas las réplicas.
    backend: Literal["memory", "redis"] = "memory"
    # Hash tag: todas las claves en el mismo slot de un Redis en clúster (scripts multiclave).
    key_prefix: str = "{headache-rl}"
    # Peticiones por minuto y usuario según el plan (ráfaga = el mismo número).
    anonymous_per_minute: int = 5
    public_per_minute: int = 20
    internal_per_minute: int = 120


class SemanticCacheSettings(BaseModel):
    enabled: bool = True
    # memory: por proceso. redis: compartida entre réplicas (Redis 8, índice vectorial).
    backend: Literal["memory", "redis"] = "memory"
    # Similitud coseno mínima. Depende del modelo de embeddings: vacío = la calibrada para el
    # que se use (`CALIBRATED_THRESHOLDS`, medida con `rag_evals.semantic_cache`).
    threshold: float | None = None
    ttl_seconds: int = 3600
    candidates: int = 5
    # Subirla (al reindexar o cambiar una política) invalida toda la caché sin recorrerla.
    index_version: str = "v1"
    key_prefix: str = "{headache-sc}"


class LangfuseSettings(BaseModel):
    host: str = "http://127.0.0.1:3000"
    public_key: str = ""
    secret_key: SecretStr = SecretStr("")


class TelemetrySettings(BaseModel):
    """OpenTelemetry (fase 11). Por defecto no se exporta nada."""

    # none: sin trazas. console: a stdout (depurar). otlp: al collector (OTLP/HTTP).
    exporter: Literal["none", "console", "otlp"] = "none"
    # Collector local (perfil `telemetry`); en la nube, el sidecar de ADOT o del Azure Monitor.
    otlp_endpoint: str = "http://127.0.0.1:4318"
    # Vacío = el nombre que pase el servicio al arrancar.
    service_name: str = ""
    # Muestreo en la cabeza, respetando la decisión del padre (ParentBased). 1.0 = todo.
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    # Guardar prompts y respuestas en los spans (redactados). Más PII y más volumen: opt-in.
    capture_content: bool = False
    # Cada cuánto se envían las métricas al collector (fase 11.4).
    metrics_interval_seconds: float = Field(default=15.0, gt=0)
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"


class SqsSettings(BaseModel):
    region: str = "eu-west-1"
    profile: str | None = None
    # Solo en local (ElasticMQ). Vacío = AWS real con credenciales del perfil/rol IAM.
    endpoint_url: str | None = None
    wait_time_s: int = 10  # Long polling: menos peticiones vacías (= menos coste).


class ServiceBusSettings(BaseModel):
    # Nube: namespace + Entra ID (Managed Identity), sin claves.
    fully_qualified_namespace: str = ""
    # Solo para el emulador local (su clave es pública y fija).
    connection_string: SecretStr | None = None
    max_wait_s: int = 5


class AzureSearchSettings(BaseModel):
    endpoint: str = ""  # https://<servicio>.search.windows.net
    index_name: str = "knowledge"
    # None => Entra ID (rol "Search Index Data Contributor"), sin claves.
    api_key: SecretStr | None = None


class S3VectorsSettings(BaseModel):
    region: str = "eu-west-1"
    profile: str | None = None
    bucket_name: str = "headache-knowledge-vectors"
    index_name: str = "knowledge"


class AzureManagedIngestionSettings(BaseModel):
    """Carril A en Azure: indexer + skillset de AI Search que leen de Blob Storage."""

    # Con identidad gestionada (tier Basic o superior): "/subscriptions/.../storageAccounts/x".
    storage_resource_id: str = ""
    # Tier Free (sin identidad gestionada): connection string con clave de la cuenta.
    storage_connection_string: SecretStr | None = None
    storage_account_url: str = ""  # https://<cuenta>.blob.core.windows.net (subida del corpus)
    container: str = "knowledge-docs"
    prefix: str = "knowledge-managed"


class BedrockKnowledgeBaseSettings(BaseModel):
    """Carril A en AWS: Knowledge Base que sincroniza desde S3 hacia S3 Vectors."""

    region: str = "eu-west-1"
    profile: str | None = None
    docs_bucket: str = ""
    knowledge_base_id: str = ""
    data_source_id: str = ""
    # Modelo de RetrieveAndGenerate (ARN o inference profile); vacio = solo Retrieve.
    generation_model_arn: str = ""


class FoundryIQSettings(BaseModel):
    """Carril A en Azure: knowledge base de AI Search + agente de Foundry Agent Service."""

    # https://<recurso>.services.ai.azure.com/api/projects/<proyecto>
    project_endpoint: str = ""
    # Para crear la conexion MCP (ARM): /subscriptions/.../accounts/<recurso>/projects/<p>
    project_resource_id: str = ""
    knowledge_base: str = "knowledge-public-kb"
    knowledge_source: str = "knowledge-public-ks"
    connection: str = "knowledge-public-mcp"
    agent_name: str = "headache-public-agent"
    agent_model: str = "gpt-4.1-mini"
    # Audiencia fija de la knowledge source (baseFilter) y del agente vinculado a ella.
    audience: tuple[str, ...] = ("public",)


class TicketingApiSettings(BaseModel):
    """API de la fase 1 consumida por tools y servidores MCP."""

    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 5.0


class McpSettings(BaseModel):
    """Servidores MCP (fase 6) como resource servers OAuth 2.1 y URLs que usa el agente."""

    host: str = "127.0.0.1"
    # "none": identidad fija (dev_customer_id), solo en local. "jwt": bearer tokens validados.
    auth: Literal["none", "jwt"] = "none"
    dev_customer_id: str = ""
    # Authorization server: Entra ID / Cognito en la nube; emisor de desarrollo en local.
    issuer: str = "https://auth.headache.local"
    jwks_uri: str = ""
    public_key_file: str = ""
    # Un audience por servidor: un token de `ticketing` no sirve en `refunds`.
    audience_template: str = "api://headache-mcp/{server}"
    customer_claim: str = "sub"
    # URL pública para los metadatos RFC 9728 (/.well-known/oauth-protected-resource).
    public_url_template: str = "http://127.0.0.1:{port}"
    dev_private_key_file: str = ".secrets/mcp-dev-key.pem"
    servers: dict[str, str] = {
        "catalog": "http://127.0.0.1:8101/mcp",
        "ticketing": "http://127.0.0.1:8102/mcp",
        "refunds": "http://127.0.0.1:8103/mcp",
    }
    gateway_url: str = "http://127.0.0.1:8100/mcp"

    def audience(self, server: str) -> str:
        return self.audience_template.format(server=server)


class A2ASettings(BaseModel):
    """Especialistas como microservicios A2A (fase 9.5): mismo modelo OAuth que MCP.

    Los tokens son de servicio a servicio: el `sub` es quien llama (el orquestador), no el
    cliente. La identidad del cliente viaja en el traspaso, que solo acepta llamantes de la lista.
    """

    issuer: str = "https://auth.headache.local"
    jwks_uri: str = ""
    public_key_file: str = ""
    # Un audience por especialista: un token para `creative` no sirve en `refunds`.
    audience_template: str = "api://headache-agents/{specialist}"
    scope: str = "cases:handle"
    allowed_callers: list[str] = ["svc-orchestrator"]
    # Identidad del orquestador en los tokens que emite el emisor de desarrollo.
    caller_id: str = "svc-orchestrator"
    dev_private_key_file: str = ".secrets/mcp-dev-key.pem"
    urls: dict[str, str] = {
        "refunds": "http://127.0.0.1:8201",
        "creative": "http://127.0.0.1:8202",
        "knowledge": "http://127.0.0.1:8203",
    }
    timeout_s: float = 120.0

    def audience(self, specialist: str) -> str:
        return self.audience_template.format(specialist=specialist)


class GuardrailsSettings(BaseModel):
    """Fase 8.4: escudo de contenido y detector de PII del harness."""

    # heuristic: local y sin coste. azure: Content Safety. bedrock: Bedrock Guardrails.
    shield: Literal["heuristic", "azure", "bedrock"] = "heuristic"
    # presidio exige `uv sync --all-packages --extra pii`.
    pii: Literal["regex", "presidio"] = "regex"
    # False = si el escudo no responde, se bloquea (falla cerrado).
    fail_open: bool = False


class ContentSafetySettings(BaseModel):
    endpoint: str = ""  # https://<recurso>.cognitiveservices.azure.com
    # None => Entra ID (rol "Cognitive Services User"), sin claves.
    api_key: SecretStr | None = None


class BedrockGuardrailSettings(BaseModel):
    region: str = "eu-west-1"
    profile: str | None = None
    guardrail_id: str = ""
    version: str = "DRAFT"


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
    gcp: GcpSettings = GcpSettings()
    gemini: GeminiSettings = GeminiSettings()
    llm_gateway: LLMGatewaySettings = LLMGatewaySettings()
    postgres: PostgresSettings = PostgresSettings()
    rabbitmq: RabbitMQSettings = RabbitMQSettings()
    sqs: SqsSettings = SqsSettings()
    service_bus: ServiceBusSettings = ServiceBusSettings()
    pubsub: PubSubSettings = PubSubSettings()
    azure_search: AzureSearchSettings = AzureSearchSettings()
    s3_vectors: S3VectorsSettings = S3VectorsSettings()
    azure_managed: AzureManagedIngestionSettings = AzureManagedIngestionSettings()
    bedrock_kb: BedrockKnowledgeBaseSettings = BedrockKnowledgeBaseSettings()
    foundry_iq: FoundryIQSettings = FoundryIQSettings()
    ticketing_api: TicketingApiSettings = TicketingApiSettings()
    mcp: McpSettings = McpSettings()
    a2a: A2ASettings = A2ASettings()
    guardrails: GuardrailsSettings = GuardrailsSettings()
    content_safety: ContentSafetySettings = ContentSafetySettings()
    bedrock_guardrail: BedrockGuardrailSettings = BedrockGuardrailSettings()
    langfuse: LangfuseSettings = LangfuseSettings()
    telemetry: TelemetrySettings = TelemetrySettings()
    scale_redis: ScaleRedisSettings = ScaleRedisSettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    semantic_cache: SemanticCacheSettings = SemanticCacheSettings()
    simulated_llm: SimulatedLLMSettings = SimulatedLLMSettings()

    def pubsub_project(self) -> str:
        """Proyecto de Pub/Sub: el suyo si se fija, si no el del proyecto de GCP."""
        return self.pubsub.project or self.gcp.project

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
