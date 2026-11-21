from __future__ import annotations

import pytest
from pydantic import ValidationError

from genaipocs_core.config import LLMProvider, Settings


def test_settings_read_nested_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "aws")
    monkeypatch.setenv("BEDROCK__REGION", "us-east-1")
    monkeypatch.setenv("POSTGRES__PASSWORD", "p@ss/word")

    settings = Settings(_env_file=None)

    assert settings.llm_provider is LLMProvider.AWS
    assert settings.bedrock.region == "us-east-1"
    assert settings.postgres.dsn().endswith("genaipocs:p%40ss%2Fword@127.0.0.1:5432/genaipocs")


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES__PASSWORD", "supersecret")

    settings = Settings(_env_file=None)

    assert "supersecret" not in repr(settings)


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI__API_KEY", "")
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDERS", "")

    settings = Settings(_env_file=None)

    assert settings.azure_openai.api_key is None
    assert settings.llm_gateway.fallback_providers == ()


def test_a2a_settings_have_one_audience_per_specialist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A__URLS", '{"refunds": "http://agent-refunds:8000"}')

    settings = Settings(_env_file=None).a2a

    assert settings.audience("refunds") == "api://genaipocs-agents/refunds"
    assert settings.audience("refunds") != settings.audience("creative")
    assert settings.urls == {"refunds": "http://agent-refunds:8000"}
    assert settings.allowed_callers == ["svc-orchestrator"]


def test_mcp_gateway_is_local_by_default_and_gcp_overrides_its_inbound_idp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = Settings(_env_file=None).mcp
    assert (default.exchanger, default.authorization_server) == ("dev", "")
    assert default.gateway_auth.require_scopes
    assert default.gateway_customer_claim == "sub"

    monkeypatch.setenv("MCP__EXCHANGER", "gcp")
    monkeypatch.setenv("MCP__SIGNER_SERVICE_ACCOUNT", "gw@genaipocs-dev.iam.gserviceaccount.com")
    monkeypatch.setenv("MCP__GATEWAY_AUTH__AUDIENCE", "genaipocs-dev")
    monkeypatch.setenv("MCP__GATEWAY_AUTH__CUSTOMER_CLAIM", "customer_id")
    monkeypatch.setenv("MCP__GATEWAY_AUTH__REQUIRE_SCOPES", "false")

    settings = Settings(_env_file=None).mcp

    assert settings.exchanger == "gcp"
    assert settings.gateway_auth.audience == "genaipocs-dev"
    assert not settings.gateway_auth.require_scopes
    # El claim del cliente solo cambia en la entrada del gateway; los servidores siguen con `sub`.
    assert (settings.gateway_customer_claim, settings.customer_claim) == ("customer_id", "sub")
    monkeypatch.setenv("MCP__EXCHANGE_TTL_S", str(13 * 3600))
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_gateway_resilience_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Settings(_env_file=None).llm_gateway
    assert defaults.max_queue_seconds == 10.0
    assert defaults.circuit_failure_threshold == 5

    monkeypatch.setenv("LLM_GATEWAY__MAX_CONCURRENCY", "4")
    monkeypatch.setenv("LLM_GATEWAY__CIRCUIT_RESET_SECONDS", "2.5")

    gateway = Settings(_env_file=None).llm_gateway
    assert gateway.max_concurrency == 4
    assert gateway.circuit_reset_seconds == 2.5


def test_telemetry_is_off_by_default_and_never_captures_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings(_env_file=None).telemetry
    assert defaults.exporter == "none"
    assert defaults.capture_content is False
    assert defaults.otlp_endpoint == "http://127.0.0.1:4318"
    assert defaults.metrics_interval_seconds == 15.0

    monkeypatch.setenv("TELEMETRY__EXPORTER", "otlp")
    monkeypatch.setenv("TELEMETRY__SAMPLE_RATIO", "0.25")
    monkeypatch.setenv("TELEMETRY__LOG_FORMAT", "json")

    telemetry = Settings(_env_file=None).telemetry
    assert telemetry.exporter == "otlp"
    assert telemetry.sample_ratio == 0.25
    assert telemetry.log_format == "json"

    monkeypatch.setenv("TELEMETRY__SAMPLE_RATIO", "1.5")
    with pytest.raises(ValueError, match="sample_ratio"):
        Settings(_env_file=None)


def test_rate_limit_is_local_by_default_and_redis_is_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings(_env_file=None)
    assert defaults.rate_limit.backend == "memory"
    assert defaults.scale_redis.port == 6380  # No es el Redis de Langfuse.

    monkeypatch.setenv("RATE_LIMIT__BACKEND", "redis")
    monkeypatch.setenv("RATE_LIMIT__PUBLIC_PER_MINUTE", "7")
    monkeypatch.setenv("SCALE_REDIS__PASSWORD", "secreto")

    settings = Settings(_env_file=None)
    assert settings.rate_limit.backend == "redis"
    assert settings.rate_limit.public_per_minute == 7
    assert "secreto" not in repr(settings.scale_redis)


def test_semantic_cache_threshold_is_calibrated_unless_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings(_env_file=None).semantic_cache
    assert (defaults.enabled, defaults.backend, defaults.threshold) == (True, "memory", None)

    monkeypatch.setenv("SEMANTIC_CACHE__THRESHOLD", "0.87")
    monkeypatch.setenv("SEMANTIC_CACHE__INDEX_VERSION", "v2")

    settings = Settings(_env_file=None).semantic_cache
    assert (settings.threshold, settings.index_version) == (0.87, "v2")


def test_provider_chain_puts_fallback_after_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDERS", "aws")

    assert Settings(_env_file=None).provider_chain() == [LLMProvider.AZURE, LLMProvider.AWS]


def test_provider_chain_spans_three_clouds_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """2.G2: una cadena, no un único respaldo. El principal o un repetido no se duplican."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDERS", "aws, gcp,azure,aws")

    assert Settings(_env_file=None).provider_chain() == [
        LLMProvider.AZURE,
        LLMProvider.AWS,
        LLMProvider.GCP,
    ]


def test_unknown_fallback_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDERS", "aws,nube-x")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_gcp_is_a_provider_like_the_other_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """La pasada GCP empieza por aquí: el enum y el fallback ya admiten la tercera nube."""
    monkeypatch.setenv("LLM_PROVIDER", "gcp")
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDERS", "aws")
    monkeypatch.setenv("GCP__PROJECT", "genaipocs-dev")
    monkeypatch.setenv("GEMINI__CHEAP_CHAT_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("GEMINI__THINKING_LEVEL", "medium")

    settings = Settings(_env_file=None)

    assert settings.provider_chain() == [LLMProvider.GCP, LLMProvider.AWS]
    assert (settings.gcp.project, settings.gcp.region) == ("genaipocs-dev", "europe-west4")
    gemini = settings.gemini
    assert gemini.cheap_chat_model == "gemini-3.1-flash-lite"
    assert (gemini.thinking_level, gemini.cheap_thinking_level) == ("medium", "low")
    # Agent Platform en el endpoint `global` por defecto; la clave solo para la Developer API.
    assert (gemini.enterprise, gemini.location, gemini.api_key) == (True, "global", None)
    # El juez no puede ser el mismo modelo que genera (sesgo de autopreferencia).
    assert gemini.judge_model not in {gemini.chat_model, gemini.cheap_chat_model}


def test_gemini_cache_is_implicit_by_default_and_its_ttl_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        Settings(_env_file=None).gemini.cache,
        Settings(_env_file=None).gemini.cache_ttl_seconds,
    ) == (
        "implicit",
        600,
    )

    monkeypatch.setenv("GEMINI__CACHE", "explicit")
    monkeypatch.setenv("GEMINI__CACHE_TTL_SECONDS", "30")  # Por debajo del mínimo de la API.

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_gemini_rejects_an_unknown_thinking_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI__THINKING_LEVEL", "xhigh")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_pubsub_falls_back_to_the_gcp_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP__PROJECT", "genaipocs-dev")

    assert Settings(_env_file=None).pubsub_project() == "genaipocs-dev"

    monkeypatch.setenv("PUBSUB__PROJECT", "genaipocs-messaging")

    assert Settings(_env_file=None).pubsub_project() == "genaipocs-messaging"


def test_pubsub_rejects_fewer_than_five_delivery_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pub/Sub no admite un dead-letter policy con menos de 5 entregas (contrato: 3 en el resto)."""
    monkeypatch.setenv("PUBSUB__MAX_DELIVERY_ATTEMPTS", "3")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_gcp_lane_b_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP_VECTOR_SEARCH__COLLECTION", "knowledge-v2")
    monkeypatch.setenv("GEMINI__EMBEDDING_BATCH_SIZE", "1")

    settings = Settings(_env_file=None)

    assert settings.gcp_vector_search.collection == "knowledge-v2"
    assert settings.gcp_vector_search.location == ""  # = GCP__REGION
    assert settings.gemini.embedding_batch_size == 1

    monkeypatch.setenv("GEMINI__EMBEDDING_BATCH_SIZE", "251")  # La API admite 250 como mucho.
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_gcp_ranking_is_pinned_and_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.G1: modelo fijado (no `@latest`) y configuración por defecto del proyecto."""
    ranking = Settings(_env_file=None).gcp_ranking
    assert (ranking.model, ranking.location, ranking.ranking_config) == (
        "semantic-ranker-default-004",
        "global",
        "default_ranking_config",
    )

    monkeypatch.setenv("GCP_RANKING__MODEL", "semantic-ranker-fast-004")
    assert Settings(_env_file=None).gcp_ranking.model == "semantic-ranker-fast-004"


def test_gcp_lane_a_has_one_corpus_per_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP_RAG_ENGINE__PUBLIC_CORPUS", "projects/p/locations/l/ragCorpora/1")

    rag = Settings(_env_file=None).gcp_rag_engine

    assert rag.corpora() == {"public": "projects/p/locations/l/ragCorpora/1", "internal": ""}
    assert (rag.chunk_size, rag.chunk_overlap) == (300, 60)
    # 4.G2: sin modelo de generación ni reranker por defecto (solo `retrieve`, sin coste extra).
    assert (rag.generation_model, rag.generation_location, rag.rerank_model) == ("", "", "")


def test_gcp_agent_search_defaults_match_terraform() -> None:
    """4.G3: los nombres por defecto son los de `agent_search.tf` y la ubicación, multirregión."""
    search = Settings(_env_file=None).gcp_agent_search
    assert (search.location, search.data_store, search.engine) == (
        "eu",
        "knowledge",
        "knowledge-search",
    )
    assert (search.bucket, search.prefix, search.answer_model) == ("", "agent-search", "stable")


def test_gcp_agent_platform_is_local_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """7.G3: sin motores, memoria en el proceso y el sabor `agentplatform` con su emulador."""
    platform = Settings(_env_file=None).gcp_agent_platform
    assert (platform.memory_engine, platform.runtime_engine) == ("", "")

    engine = "projects/p/locations/europe-west4/reasoningEngines/1"
    monkeypatch.setenv("GCP_AGENT_PLATFORM__MEMORY_ENGINE", engine)
    assert Settings(_env_file=None).gcp_agent_platform.memory_engine == engine
