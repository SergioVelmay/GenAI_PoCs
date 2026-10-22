"""Fase 12.6: prompts versionados en ficheros, inclusiones y enlace con la traza."""

from __future__ import annotations

from pathlib import Path

import pytest

from headache_core.adapters.memory import FakeLLM
from headache_core.adapters.otel import recording_telemetry
from headache_core.application.llm import ObservedLLM, PriceTable
from headache_core.application.prompts import (
    PromptCatalog,
    PromptError,
    current_prompt,
    prompt_scope,
)
from headache_core.application.telemetry import TelemetryLLMTracer, genai
from headache_core.ports import ChatMessage, Role
from headache_core.prompts import PROMPTS


def _write(directory: Path, name: str, body: str, **meta: str) -> None:
    header = {"name": name, "version": "1", "description": "d", **meta}
    lines = ["---", *(f"{k}: {v}" for k, v in header.items()), "---"]
    (directory / f"{name}.md").write_text("\n".join(lines) + "\n" + body, encoding="utf-8")


def test_includes_are_resolved_and_final_newline_is_respected(tmp_path: Path) -> None:
    _write(tmp_path, "rules", "- Regla A.\n- Regla B.\n", fragment="true", final_newline="false")
    _write(tmp_path, "alpha-agent", "Eres un agente de prueba de Headache.\n{{> rules}}\n- Fin.\n")
    _write(tmp_path, "beta-judge", "Sin salto final.\n", final_newline="false")

    catalog = PromptCatalog.from_directory(tmp_path)

    assert catalog.get("alpha-agent").text == (
        "Eres un agente de prueba de Headache.\n- Regla A.\n- Regla B.\n- Fin.\n"
    )
    assert catalog.get("beta-judge").text == "Sin salto final."
    assert catalog.get("rules").fragment
    assert len(catalog.get("alpha-agent").sha256) == 64


def test_python_format_prompts_render_and_plain_ones_reject_variables(tmp_path: Path) -> None:
    _write(
        tmp_path, "gamma-mail", "Escribe en {language}, con llaves {{literal}}.\n", format="python"
    )
    _write(tmp_path, "delta-plain", "Texto fijo.\n")
    catalog = PromptCatalog.from_directory(tmp_path)

    assert catalog.get("gamma-mail").render(language="español") == (
        "Escribe en español, con llaves {literal}.\n"
    )
    assert catalog.get("gamma-mail").static_prefix == "Escribe en "
    with pytest.raises(PromptError):
        catalog.get("delta-plain").render(language="es")


@pytest.mark.parametrize(
    ("files", "error"),
    [
        ({"a": "{{> missing}}\n"}, "desconocida"),
        ({"a": "{{> b}}\n", "b": "{{> a}}\n"}, "circular"),
    ],
)
def test_broken_includes_fail_on_load(tmp_path: Path, files: dict[str, str], error: str) -> None:
    for name, body in files.items():
        _write(tmp_path, f"x-{name}", body.replace("{{> ", "{{> x-"))
    with pytest.raises(PromptError, match=error):
        PromptCatalog.from_directory(tmp_path)


def test_header_and_name_are_validated(tmp_path: Path) -> None:
    (tmp_path / "sin-cabecera.md").write_text("Hola\n", encoding="utf-8")
    with pytest.raises(PromptError, match="cabecera"):
        PromptCatalog.from_directory(tmp_path)
    (tmp_path / "sin-cabecera.md").unlink()
    _write(tmp_path, "otro-nombre", "Hola\n")
    (tmp_path / "otro-nombre.md").rename(tmp_path / "fichero.md")
    with pytest.raises(PromptError, match="coincidir"):
        PromptCatalog.from_directory(tmp_path)


def test_the_system_message_identifies_its_prompt() -> None:
    triage = PROMPTS.get("triage")
    judge = PROMPTS.get("agent-judge")

    assert current_prompt(triage.text) == triage
    # Un agente añade el índice de skills detrás de sus instrucciones.
    assert current_prompt(judge.text + "\nSkills: ...") == judge
    assert current_prompt("Eres un asistente cualquiera.") is None
    assert current_prompt(None) is None
    with prompt_scope(judge):
        assert current_prompt(triage.text) == judge  # El bloque explícito manda.


async def test_each_generation_carries_its_prompt_version_for_langfuse() -> None:
    triage = PROMPTS.get("triage")
    llm = ObservedLLM(FakeLLM(["{}"]), tracer=TelemetryLLMTracer(), prices=PriceTable(), name="x")

    with recording_telemetry() as recorded:
        await llm.complete([ChatMessage(Role.SYSTEM, triage.text), ChatMessage(Role.USER, "hola")])
        await llm.complete([ChatMessage(Role.USER, "sin prompt de sistema")])

    spans = [s for s in recorded.exporter.get_finished_spans() if s.name.startswith("chat")]
    attributes = [dict(s.attributes or {}) for s in spans]
    assert attributes[0][genai.LANGFUSE_PROMPT_NAME] == "triage"
    assert attributes[0][genai.LANGFUSE_PROMPT_VERSION] == triage.version
    assert attributes[0][genai.HEADACHE_PROMPT_NAME] == "triage"
    assert genai.LANGFUSE_PROMPT_NAME not in attributes[1]
