"""Fase 12.G4: publicación de prompts en la gestión de prompts de Agent Platform (doble).

El doble imita `client.prompts` sobre Vertex Datasets: `prompt_id` numérico, búsqueda por
nombre con el filtro de `list`, `create_version` que crea un prompt nuevo y `update` que añade
versiones. Usa los tipos reales de `agentplatform`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from agentplatform import types

from genaipocs_core.adapters.agentplatform_prompts import AgentPlatformPromptStore, version_name
from genaipocs_core.application.prompts import Prompt
from rag_evals.llmops.prompts import Entry, apply_publish, plan_publish

MODEL = "gemini-4-hydrogen"


@dataclass
class _Stored:
    display_name: str
    versions: list[tuple[str, types.Prompt]] = field(default_factory=list)


class FakePrompts:
    def __init__(self) -> None:
        self.datasets: dict[str, _Stored] = {}
        self.calls: list[str] = []

    def list(self, *, config: Any = None) -> Iterator[types.PromptRef]:
        match = re.fullmatch(r'display_name="(.+)"', config.filter or "")
        assert match, "el adaptador debe filtrar por nombre"
        for prompt_id, stored in self.datasets.items():
            if stored.display_name == match.group(1):
                yield types.PromptRef(prompt_id=prompt_id, model=MODEL)

    def list_versions(self, *, prompt_id: str, config: Any = None) -> Iterator[Any]:
        # Desordenadas a propósito: el adaptador ordena por número.
        for version_id, _ in reversed(self.datasets[prompt_id].versions):
            yield types.PromptVersionRef(prompt_id=prompt_id, version_id=version_id, model=MODEL)

    def get_version(self, *, prompt_id: str, version_id: str, config: Any = None) -> types.Prompt:
        return dict(self.datasets[prompt_id].versions)[version_id]

    def create_version(self, *, prompt: types.Prompt, config: Any = None) -> types.Prompt:
        self.calls.append(f"create_version {config.version_display_name}")
        prompt_id = str(1000 + len(self.datasets))
        self.datasets[prompt_id] = _Stored(config.prompt_display_name, [("1", prompt)])
        return prompt

    def update(self, *, prompt_id: str, prompt: types.Prompt, config: Any = None) -> types.Prompt:
        self.calls.append(f"update {prompt_id} {config.version_display_name}")
        versions = self.datasets[prompt_id].versions
        versions.append((str(len(versions) + 1), prompt))
        return prompt


def test_publish_creates_then_adds_versions_with_the_git_number() -> None:
    fake = FakePrompts()
    store = AgentPlatformPromptStore(fake, model=MODEL)
    v1 = Prompt("demo-agent", 1, "Demo.", "Eres el agente de demo de Genaipocs.\n")
    v2 = Prompt("demo-agent", 2, "Demo.", "Eres el agente de demo de Genaipocs, v2.\n")

    for prompt in (v1, v2):
        entries = [Entry("demo", prompt)]
        steps = plan_publish(entries, store)
        assert [s.action for s in steps] == ["create"]
        assert apply_publish(entries, steps, store, "abc1234def") == 1

    assert fake.calls == ["create_version git-v1-abc1234", "update 1000 git-v2-abc1234"]
    latest = store.latest("demo-agent")
    assert latest is not None and (latest.version, latest.text) == (2, v2.text)
    stored = fake.datasets["1000"].versions[-1][1].prompt_data
    assert stored.model == MODEL and stored.contents[0].role == "user"
    # Volver a publicar no hace nada; otro texto con la misma versión es un conflicto.
    assert [s.action for s in plan_publish([Entry("demo", v2)], store)] == ["up_to_date"]
    changed = Prompt("demo-agent", 2, "Demo.", "Otro texto.\n")
    assert [s.action for s in plan_publish([Entry("demo", changed)], store)] == ["conflict"]


def test_an_unpublished_prompt_has_no_latest_version() -> None:
    assert AgentPlatformPromptStore(FakePrompts(), model=MODEL).latest("nuevo") is None


def test_two_prompts_with_the_same_name_are_an_error() -> None:
    fake = FakePrompts()
    fake.datasets = {"1": _Stored("dup"), "2": _Stored("dup")}

    with pytest.raises(ValueError, match="2 prompts se llaman dup"):
        AgentPlatformPromptStore(fake, model=MODEL).latest("dup")


def test_version_name_carries_git_version_and_short_commit() -> None:
    assert version_name(3, "1a2b3c4d5e") == "git-v3-1a2b3c4"
