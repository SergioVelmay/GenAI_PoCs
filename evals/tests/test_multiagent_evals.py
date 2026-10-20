from __future__ import annotations

from pathlib import Path

from headache_core.domain import ROUTES, Specialist, SupportIntent
from rag_evals.multiagent import (
    ENGINES,
    INVARIANTS,
    check,
    load_scenarios,
    render_markdown,
    run_case,
    run_suite,
    script_responses,
    write_report,
)


def test_dataset_covers_every_specialist_and_category() -> None:
    scenarios = load_scenarios()

    assert len({s.id for s in scenarios}) == len(scenarios)
    assert {s.expect.specialist for s in scenarios} == set(Specialist)
    assert {s.category for s in scenarios} == {"happy", "defense", "resilience", "multi_intent"}
    assert {s.expected_intent for s in scenarios} == set(SupportIntent)
    # Cada guion empieza por el triaje y la ruta esperada sale del triaje guionizado.
    for scenario in scenarios:
        first = scenario.script[0]
        assert "triage" in first
        assert ROUTES[SupportIntent(first["triage"])] is scenario.expect.specialist


def test_script_is_translated_to_fake_llm_responses() -> None:
    scenario = next(s for s in load_scenarios() if s.id == "resilience-transient-error")

    responses = script_responses(scenario, scenario.script)

    assert isinstance(responses[0], str) and '"intent": "event_info"' in responses[0]
    assert isinstance(responses[1], Exception)
    assert responses[2] == "Las puertas abren 30 minutos antes."


async def test_suite_passes_with_zero_invariants_on_every_engine(tmp_path: Path) -> None:
    report = await run_suite(ENGINES)

    assert check(report) == []
    assert [s.engine for s in report.engines] == list(ENGINES)
    for summary in report.engines:
        assert summary.invariants == dict.fromkeys(INVARIANTS, 0)
        assert summary.handoff_tokens < summary.handoff_tokens + summary.email_tokens
    [comparison] = report.supervisor
    assert comparison.supervisor_intents == comparison.expected_intents
    assert comparison.supervisor_calls > comparison.router_calls
    md = write_report(report, tmp_path)
    assert md.read_text(encoding="utf-8") == render_markdown(report)


async def test_a_broken_expectation_is_reported() -> None:
    scenario = next(s for s in load_scenarios() if s.id == "happy-event-info")
    wrong = scenario.model_copy(
        update={"expect": scenario.expect.model_copy(update={"specialist": Specialist.REFUNDS})}
    )

    result = await run_case("langgraph", wrong)

    assert not result.passed
    assert any("especialista" in f for f in result.failures)
    assert result.routing_ok  # El sistema enrutó bien: la expectativa era la errónea.
