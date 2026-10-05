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
    assert settings.postgres.dsn().endswith("headache:p%40ss%2Fword@localhost:5432/headache")


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES__PASSWORD", "supersecret")

    settings = Settings(_env_file=None)

    assert "supersecret" not in repr(settings)
