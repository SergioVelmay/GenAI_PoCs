from __future__ import annotations

import pytest

from headache_core.config import LLMProvider, Settings


def test_settings_read_nested_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "aws")
    monkeypatch.setenv("BEDROCK__REGION", "us-east-1")
    monkeypatch.setenv("POSTGRES__PASSWORD", "p@ss/word")

    settings = Settings(_env_file=None)

    assert settings.llm_provider is LLMProvider.AWS
    assert settings.bedrock.region == "us-east-1"
    assert settings.postgres.dsn().endswith("headache:p%40ss%2Fword@127.0.0.1:5432/headache")


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES__PASSWORD", "supersecret")

    settings = Settings(_env_file=None)

    assert "supersecret" not in repr(settings)


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI__API_KEY", "")
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDER", "")

    settings = Settings(_env_file=None)

    assert settings.azure_openai.api_key is None
    assert settings.llm_gateway.fallback_provider is None


def test_a2a_settings_have_one_audience_per_specialist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A__URLS", '{"refunds": "http://agent-refunds:8000"}')

    settings = Settings(_env_file=None).a2a

    assert settings.audience("refunds") == "api://headache-agents/refunds"
    assert settings.audience("refunds") != settings.audience("creative")
    assert settings.urls == {"refunds": "http://agent-refunds:8000"}
    assert settings.allowed_callers == ["svc-orchestrator"]


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
    monkeypatch.setenv("LLM_GATEWAY__FALLBACK_PROVIDER", "aws")

    assert Settings(_env_file=None).provider_chain() == [LLMProvider.AZURE, LLMProvider.AWS]
