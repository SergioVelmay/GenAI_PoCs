"""Fase 12.6: candado de versiones de prompts y publicación en Langfuse (almacén falso)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from headache_core.application.prompts import Prompt
from headache_core.ports.prompts import PublishedPrompt
from orchestrator.prompts import PROMPTS as ORCHESTRATOR
from rag_evals.llmops.prompts import (
    LOCKABLE,
    Entry,
    apply_publish,
    catalog_dirs,
    check,
    discover,
    load_lock,
    lock,
    lock_data,
    plan_publish,
    prompt_dirs,
)

PROMPT = Prompt("demo-agent", 2, "Demo.", "Eres el agente de demo de Headache.\n")


class FakeStore:
    def __init__(self, published: Mapping[str, PublishedPrompt] | None = None) -> None:
        self.published = dict(published or {})
        self.created: list[tuple[str, int, list[str], dict[str, object]]] = []

    def latest(self, name: str) -> PublishedPrompt | None:
        return self.published.get(name)

    def create(
        self,
        name: str,
        text: str,
        *,
        labels: Sequence[str],
        config: Mapping[str, object],
        commit_message: str,
    ) -> int:
        version = (self.published[name].version if name in self.published else 0) + 1
        self.published[name] = PublishedPrompt(name, version, text)
        self.created.append((name, version, list(labels), dict(config)))
        return version


def test_every_prompt_folder_is_a_catalog_and_the_lock_is_up_to_date() -> None:
    assert prompt_dirs() == catalog_dirs()
    entries = discover()
    assert len(entries) >= 19
    assert check(entries, load_lock()) == []


def test_the_shared_case_rules_are_included_verbatim() -> None:
    rules = ORCHESTRATOR.get("case-rules").text
    for name in ("refunds-specialist", "knowledge-specialist"):
        assert rules in ORCHESTRATOR.get(name).text
        assert "{{>" not in ORCHESTRATOR.get(name).text


def _locked(prompt: Prompt = PROMPT) -> dict[str, dict[str, object]]:
    return lock_data([Entry("demo", prompt)])


@pytest.mark.parametrize(
    ("now", "fragment"),
    [
        (replace(PROMPT, text="Otro texto.\n"), "cambia sin subir la versión"),
        (replace(PROMPT, version=1), "la versión baja"),
        (replace(PROMPT, version=3), "sin cambiar el texto"),
        (replace(PROMPT, version=3, text="Otro texto.\n"), "sin candado"),
    ],
)
def test_the_lock_catches_every_kind_of_drift(now: Prompt, fragment: str) -> None:
    [problem] = check([Entry("demo", now)], _locked())
    assert fragment in problem


def test_new_deleted_and_moved_prompts_need_a_new_lock() -> None:
    other = Prompt("other-agent", 1, "", "x")
    problems = check([Entry("demo", other)], _locked())
    assert len(problems) == 2 and all(p.endswith(LOCKABLE) for p in problems)
    [moved] = check([Entry("otro_paquete", PROMPT)], _locked())
    assert moved.endswith(LOCKABLE)


def test_lock_refuses_to_absorb_a_silent_change(tmp_path: Path) -> None:
    path = tmp_path / "prompts.lock.json"
    assert lock([Entry("demo", PROMPT)], path) == []
    silent = replace(PROMPT, text="Cambio sin versión.\n")

    assert lock([Entry("demo", silent)], path)
    assert load_lock(path) == _locked()  # El candado no se ha tocado.
    bumped = replace(silent, version=3)
    assert lock([Entry("demo", bumped)], path) == []
    assert load_lock(path)["demo-agent"]["version"] == 3


def test_publish_creates_the_same_version_number_in_langfuse() -> None:
    store = FakeStore({"demo-agent": PublishedPrompt("demo-agent", 1, "v1\n")})
    fragment = Prompt("demo-rules", 1, "", "reglas", fragment=True)
    entries = [Entry("demo", PROMPT), Entry("demo", fragment)]

    steps = plan_publish(entries, store)

    assert [(s.name, s.action) for s in steps] == [("demo-agent", "create")]
    assert apply_publish(entries, steps, store, "abc1234def") == 1
    [(name, version, labels, config)] = store.created
    assert (name, version) == ("demo-agent", 2)
    assert labels == ["production", "git-v2"]
    assert config["sha256"] == PROMPT.sha256 and config["git_commit"] == "abc1234def"
    # Volver a publicar no hace nada: la versión ya está y el texto coincide.
    assert [s.action for s in plan_publish(entries, store)] == ["up_to_date"]


@pytest.mark.parametrize(
    "published",
    [
        PublishedPrompt("demo-agent", 2, "Otro texto en Langfuse.\n"),  # misma versión, otro texto
        PublishedPrompt("demo-agent", 5, "x"),  # Langfuse va por delante de Git
    ],
)
def test_publish_never_rewrites_or_skips_versions(published: PublishedPrompt) -> None:
    [step] = plan_publish([Entry("demo", PROMPT)], FakeStore({"demo-agent": published}))
    assert step.action == "conflict"


def test_a_prompt_that_is_not_published_yet_starts_at_version_one() -> None:
    first = replace(PROMPT, version=1)
    assert [s.action for s in plan_publish([Entry("demo", first)], FakeStore())] == ["create"]
    assert [s.action for s in plan_publish([Entry("demo", PROMPT)], FakeStore())] == ["conflict"]
