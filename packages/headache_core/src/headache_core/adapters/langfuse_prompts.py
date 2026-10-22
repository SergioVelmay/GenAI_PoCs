"""Prompts publicados en Langfuse (fase 12.6). Versiones inmutables numeradas por Langfuse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Self

from langfuse import Langfuse
from langfuse.api import NotFoundError

from headache_core.config import LangfuseSettings
from headache_core.ports.prompts import PublishedPrompt


class LangfusePromptStore:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: LangfuseSettings) -> Self:
        return cls(
            Langfuse(
                public_key=settings.public_key,
                secret_key=settings.secret_key.get_secret_value(),
                host=settings.host,
            )
        )

    def latest(self, name: str) -> PublishedPrompt | None:
        try:
            prompt = self._client.get_prompt(
                name, label="latest", cache_ttl_seconds=0, max_retries=0
            )
        except NotFoundError:
            return None
        return PublishedPrompt(name, prompt.version, str(prompt.prompt))

    def create(
        self,
        name: str,
        text: str,
        *,
        labels: Sequence[str],
        config: Mapping[str, object],
        commit_message: str,
    ) -> int:
        created = self._client.create_prompt(
            name=name,
            prompt=text,
            labels=list(labels),
            type="text",
            config=dict(config),
            commit_message=commit_message,
        )
        return created.version
