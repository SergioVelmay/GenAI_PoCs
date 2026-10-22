from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from booking_agent.spec import SKILLS_DIR
from headache_core.application.agents import SkillCatalog
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import ToolExecution
from headache_core.ports import ChatMessage, Role, ToolCall
from rag_evals.agents.__main__ import (
    BASELINE_PATH,
    AgentBaseline,
    check_report,
    render_markdown,
    write_report,
)
from rag_evals.agents.scenario_llm import UNRESOLVED, ScenarioLLM, resolve
from rag_evals.agents.scenarios import (
    SCENARIOS_PATH,
    ArgExpectation,
    ContextOverride,
    load_scenarios,
)
from rag_evals.agents.suite import (
    argument_mismatches,
    build_knowledge,
    run_agent_suite,
    run_scenario,
    trajectory_metrics,
)


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
    report = await run_agent_suite(
        load_scenarios(), flavors=["native"], dataset=SCENARIOS_PATH.name
    )

    [native] = report.flavors
    failures = {o.scenario_id: o.failures for o in report.outcomes["native"] if o.failures}
    assert failures == {}
    assert native.pass_rate == 1.0
    assert native.invariants == dict.fromkeys(native.invariants, 0)
    assert native.trajectory_precision == 1.0
    assert native.trajectory_recall == 1.0
    assert native.trajectory_exact_rate == 1.0
    assert native.argument_accuracy == 1.0
    assert native.resolution_rate == 1.0
    assert native.invalid_argument_calls == 1
    assert 0 < native.prefix_reuse < 1
    json_path, md_path = write_report(report, tmp_path, "agents")
    assert json.loads(json_path.read_text(encoding="utf-8"))["flavors"][0]["flavor"] == "native"
    assert "| native | 14/14 |" in md_path.read_text(encoding="utf-8")
    assert render_markdown(report).startswith("# Evals del Knowledge & Booking Agent")


@pytest.mark.parametrize("flavor", ["langgraph", "llamaindex", "maf", "strands", "agentcore"])
async def test_framework_flavors_match_the_native_reference(flavor: str) -> None:
    report = await run_agent_suite(
        load_scenarios(), flavors=["native", flavor], dataset=SCENARIOS_PATH.name
    )

    native, other = report.outcomes["native"], report.outcomes[flavor]
    assert {o.scenario_id: o.failures for o in other if o.failures} == {}
    # Paridad de comportamiento: mismas tools, en el mismo orden, en cada escenario.
    assert [o.tools for o in other] == [o.tools for o in native]
    summary = next(f for f in report.flavors if f.flavor == flavor)
    assert summary.invariants == dict.fromkeys(summary.invariants, 0)


async def test_foundry_cannot_compact_a_service_side_conversation() -> None:
    report = await run_agent_suite(
        load_scenarios(), flavors=["foundry"], dataset=SCENARIOS_PATH.name
    )

    failures = {o.scenario_id: o.failures for o in report.outcomes["foundry"] if o.failures}
    # El historial es del servicio: responde bien, pero no hay compactación nuestra que medir.
    assert failures == {"memory-summary-long-thread-es": ["compactaciones 0 < 1"]}
    [summary] = report.flavors
    assert summary.invariants == dict.fromkeys(summary.invariants, 0)
    # La respuesta y el estado son correctos: el hueco es de contexto, no de resolución.
    assert summary.resolution_rate == 1.0
    baseline = AgentBaseline.model_validate_json(BASELINE_PATH.read_text(encoding="utf-8"))
    assert check_report(report, baseline).problems == []

    first, *remaining = report.outcomes["foundry"]
    regression = replace(first, failures=["regresión inesperada"], passed=False)
    broken = replace(report, outcomes={"foundry": [regression, *remaining]})
    assert any("regresión inesperada" in p for p in check_report(broken, baseline).problems)


def test_gate_lists_known_gaps_that_are_fixed_without_failing() -> None:
    report = asyncio.run(
        run_agent_suite(
            [s for s in load_scenarios() if s.id == "knowledge-faq-lisbon-en"],
            flavors=["native"],
            dataset="agent-scenarios-v1.jsonl",
        )
    )
    baseline = AgentBaseline(
        dataset="agent-scenarios-v1.jsonl",
        known_gaps={"native": {"knowledge-faq-lisbon-en": ["algo que ya no falla"]}},
    )

    result = check_report(report, baseline)

    assert result.problems == []
    assert result.fixed_gaps == ["native: knowledge-faq-lisbon-en"]
    other = baseline.model_copy(update={"dataset": "agent-scenarios-v2.jsonl"})
    assert check_report(report, other).problems


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


def test_trajectory_metrics_distinguish_order_and_extra_calls() -> None:
    assert trajectory_metrics(["search", "refund"], ["search", "refund"]) == (1.0, 1.0)
    assert trajectory_metrics(["search", "refund"], ["search", "lookup", "refund"]) == (
        2 / 3,
        1.0,
    )
    assert trajectory_metrics(["search", "refund"], ["refund", "search"]) == (0.5, 0.5)
    assert trajectory_metrics([], []) == (1.0, 1.0)
    assert trajectory_metrics([], ["search"]) == (0.0, 0.0)


def _execution(name: str, arguments: str) -> ToolExecution:
    return ToolExecution(ToolCall("c", name, arguments), "{}", None, 1.0)


def test_argument_expectations_are_an_ordered_subset_match() -> None:
    journal = [
        _execution("get_order", '{"order_id": "201"}'),
        _execution("get_order", '{"order_id": "ORD_0000201"}'),
        _execution("request_refund", '{"order_id": "ord_0000201", "reason": "x"}'),
        _execution("request_refund", "no es json"),
    ]
    get = ArgExpectation(tool="get_order", args={"order_id": "ord_0000201"})
    refund = ArgExpectation(tool="request_refund", args={"order_id": "ord_0000201"})
    other = ArgExpectation(tool="request_refund", args={"order_id": "ord_0000202"})

    # Subconjunto, sin distinguir mayúsculas; un intento inválido previo no cuenta como fallo.
    assert argument_mismatches([get, refund], journal) == []
    # El orden importa: el reembolso no puede ir antes de consultar el pedido.
    assert argument_mismatches([refund, get], journal) == [get]
    # Un fallo no consume el diario: la siguiente expectativa se sigue buscando.
    assert argument_mismatches([other, refund], journal) == [other]


async def test_wrong_write_arguments_leave_the_case_unresolved(
    knowledge: tuple[RetrievalService, frozenset[str]],
) -> None:
    [scenario] = [s for s in load_scenarios() if s.id == "refund-approved-es"]
    expect = scenario.expect.model_copy(
        update={"tool_args": [ArgExpectation(tool="request_refund", args={"order_id": "x"})]}
    )
    retrieval, internal = knowledge

    outcome = await run_scenario(
        "native",
        scenario.model_copy(update={"expect": expect}),
        retrieval=retrieval,
        skills=SkillCatalog.from_directory(SKILLS_DIR),
        internal_documents=internal,
    )

    assert not outcome.resolved and not outcome.passed
    assert outcome.argument_accuracy == 0.0
    assert outcome.trajectory_recall == 1.0
