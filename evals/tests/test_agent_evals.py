from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from booking_agent.spec import SKILLS_DIR
from headache_core.application.agents import SkillCatalog
from headache_core.application.retrieval import RetrievalService
from headache_core.ports import ChatMessage, Role, ToolCall
from rag_evals.agents.__main__ import render_markdown, write_report
from rag_evals.agents.scenario_llm import UNRESOLVED, ScenarioLLM, resolve
from rag_evals.agents.scenarios import ContextOverride, load_scenarios
from rag_evals.agents.suite import build_knowledge, run_agent_suite, run_scenario


@pytest.fixture(scope="module")
def knowledge() -> tuple[RetrievalService, frozenset[str]]:
    # El servicio local no depende del bucle de eventos: se construye una vez por módulo.
    return asyncio.run(build_knowledge())


def test_scenarios_cover_every_category() -> None:
    scenarios = load_scenarios()
    assert {s.category for s in scenarios} == {
        "knowledge",
        "booking",
        "refund",
        "security",
        "memory",
        "harness",
    }


def test_markers_only_resolve_with_what_the_model_received() -> None:
    call = ToolCall("c1", "search_events", "{}")
    messages = [
        ChatMessage(Role.SYSTEM, "<contexto>\n- home_city: Lisbon\n</contexto>"),
        ChatMessage(Role.USER, "hola"),
        ChatMessage(Role.ASSISTANT, "", tool_calls=(call,)),
        ChatMessage(Role.TOOL, '[{"id": "evt_1", "title": "Tour"}]', tool_call_id="c1"),
    ]

    assert resolve("{tool:search_events.0.title} en {memory:home_city}", messages) == (
        "Tour en Lisbon"
    )
    assert resolve("{seen:hola}/{seen:adiós}", messages) == f"hola/{UNRESOLVED}"
    assert resolve("{tool:get_order.status}", messages) == UNRESOLVED


async def test_scenario_llm_simulates_prefix_caching() -> None:
    [scenario] = [s for s in load_scenarios() if s.id == "knowledge-faq-lisbon-en"]
    llm = ScenarioLLM(scenario)
    prompt = [ChatMessage(Role.SYSTEM, "x" * 400), ChatMessage(Role.USER, scenario.turns[0].user)]

    first = await llm.complete(prompt)
    second = await llm.complete(prompt)

    assert first.tool_calls[0].name == "search_knowledge"
    assert first.usage.cached_input_tokens == 0
    assert second.usage.cached_input_tokens == second.usage.input_tokens


async def test_native_flavor_passes_the_whole_suite(tmp_path: Path) -> None:
    report = await run_agent_suite(load_scenarios(), flavors=["native"], dataset="v1")

    [native] = report.flavors
    failures = {o.scenario_id: o.failures for o in report.outcomes["native"] if o.failures}
    assert failures == {}
    assert native.pass_rate == 1.0
    assert native.invariants == dict.fromkeys(native.invariants, 0)
    assert 0 < native.prefix_reuse < 1
    json_path, md_path = write_report(report, tmp_path, "agents")
    assert json.loads(json_path.read_text(encoding="utf-8"))["flavors"][0]["flavor"] == "native"
    assert "| native | 14/14 |" in md_path.read_text(encoding="utf-8")
    assert render_markdown(report).startswith("# Evals del Knowledge & Booking Agent")


@pytest.mark.parametrize("flavor", ["langgraph", "llamaindex", "maf", "strands", "agentcore"])
async def test_framework_flavors_match_the_native_reference(flavor: str) -> None:
    report = await run_agent_suite(load_scenarios(), flavors=["native", flavor], dataset="v1")

    native, other = report.outcomes["native"], report.outcomes[flavor]
    assert {o.scenario_id: o.failures for o in other if o.failures} == {}
    # Paridad de comportamiento: mismas tools, en el mismo orden, en cada escenario.
    assert [o.tools for o in other] == [o.tools for o in native]
    summary = next(f for f in report.flavors if f.flavor == flavor)
    assert summary.invariants == dict.fromkeys(summary.invariants, 0)


async def test_foundry_cannot_compact_a_service_side_conversation() -> None:
    report = await run_agent_suite(load_scenarios(), flavors=["foundry"], dataset="v1")

    failures = {o.scenario_id: o.failures for o in report.outcomes["foundry"] if o.failures}
    # El historial es del servicio: responde bien, pero no hay compactación nuestra que medir.
    assert failures == {"memory-summary-long-thread-es": ["compactaciones 0 < 1"]}
    [summary] = report.flavors
    assert summary.invariants == dict.fromkeys(summary.invariants, 0)


async def test_trimming_without_summary_loses_the_early_fact(
    knowledge: tuple[RetrievalService, frozenset[str]],
) -> None:
    [scenario] = [s for s in load_scenarios() if s.id == "memory-summary-long-thread-es"]
    assert scenario.context is not None
    no_summary = scenario.model_copy(
        update={
            "context": ContextOverride(
                trigger_tokens=scenario.context.trigger_tokens,
                target_tokens=scenario.context.target_tokens,
                summarize=False,
            )
        }
    )
    retrieval, internal = knowledge

    outcome = await run_scenario(
        "native",
        no_summary,
        retrieval=retrieval,
        skills=SkillCatalog.from_directory(SKILLS_DIR),
        internal_documents=internal,
    )

    assert not outcome.passed
    assert "contexto perdido" in " ".join(outcome.failures)
