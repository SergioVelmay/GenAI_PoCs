"""Red teaming con promptfoo e informe consolidado (fase 12.5). Sin Node.js: lo que necesita
promptfoo se prueba con sus ficheros congelados y con resultados sintéticos."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from rag_evals.agents.scenarios import ATTACKS_PATH, load_scenarios
from rag_evals.redteam import report
from rag_evals.redteam.mutations import MutationBaseline
from rag_evals.redteam.promptfoo import (
    BASE,
    REDTEAM_CONFIG,
    STRATEGIES,
    TESTS_PATH,
    freeze_tests,
    load_baseline,
    outcomes_from_results,
    promptfoo_env,
    redteam_config,
    run_case,
)

SCENARIOS = load_scenarios(ATTACKS_PATH)


def _tests() -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = yaml.safe_load(TESTS_PATH.read_text(encoding="utf-8"))
    return tests


def test_frozen_variants_cover_every_strategy_and_scenario() -> None:
    tests = _tests()
    ids = {s.id for s in SCENARIOS}
    assert len(tests) == len(SCENARIOS) * (len(STRATEGIES) + 1)
    pairs = {(t["vars"]["strategy"], t["vars"]["scenario_id"]) for t in tests}
    assert pairs == {(s, i) for s in (BASE, *STRATEGIES) for i in ids}
    assert all(t["description"] == "{strategy}:{scenario_id}".format(**t["vars"]) for t in tests)


def test_the_generation_config_matches_the_attack_dataset() -> None:
    committed = yaml.safe_load(REDTEAM_CONFIG.read_text(encoding="utf-8"))
    assert committed == redteam_config(SCENARIOS)
    # Sin entidades: si no, promptfoo llama a un LLM para extraerlas.
    assert committed["redteam"]["entities"] == []


def test_promptfoo_never_gets_provider_keys_and_never_phones_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-no")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "no")
    env = promptfoo_env("python-del-entorno")
    assert "OPENAI_API_KEY" not in env and "AZURE_OPENAI_API_KEY" not in env
    assert env["PROMPTFOO_DISABLE_REMOTE_GENERATION"] == "true"
    assert env["PROMPTFOO_DISABLE_TELEMETRY"] == "1"
    assert env["PROMPTFOO_DISABLE_SHARING"] == "1"
    assert env["PROMPTFOO_PYTHON"] == "python-del-entorno"


def test_generated_cases_are_mapped_back_to_their_scenario() -> None:
    jailbreak = next(s for s in SCENARIOS if s.id == "attack-direct-jailbreak-es")
    generated = {
        "tests": [
            {"vars": {"prompt": jailbreak.turns[0].user}, "metadata": {"pluginId": "intent"}},
            {
                "vars": {"prompt": "SWdub3Jh..."},
                "metadata": {"strategyId": "base64", "originalText": jailbreak.turns[0].user},
                "assert": [{"type": "promptfoo:redteam:intent"}],
            },
        ]
    }

    frozen = freeze_tests(generated, SCENARIOS)

    assert [t["description"] for t in frozen] == [
        f"original:{jailbreak.id}",
        f"base64:{jailbreak.id}",
    ]
    # Sin la aserción con LLM-juez de promptfoo: la pone `assert.js` (determinista).
    assert all("assert" not in t for t in frozen)


def test_results_are_read_back_into_outcomes() -> None:
    output = {
        "category": "attack",
        "blocked_at_input": False,
        "guardrails": ["approval_requested"],
        "breaches": [],
        "invalid": False,
    }
    results = {
        "results": {
            "results": [
                {
                    "testCase": {
                        "description": "hex:attack-direct-jailbreak-es",
                        "vars": {"prompt": "49 67"},
                    },
                    "response": {"output": json.dumps(output)},
                }
            ]
        }
    }
    [outcome] = outcomes_from_results(results)
    assert (outcome.converter, outcome.scenario_id) == ("hex", "attack-direct-jailbreak-es")
    assert outcome.guardrails == ["approval_requested"] and not outcome.breaches


async def test_the_provider_runs_a_variant_against_the_harness() -> None:
    [hex_pii] = [t["vars"] for t in _tests() if t["description"] == "hex:attack-pii-in-prompt-es"]

    result = await run_case(str(hex_pii["prompt"]), "attack-pii-in-prompt-es", strategy="hex")

    # La PII en hexadecimal llega al modelo: riesgo residual aceptado, y la respuesta lo dice.
    assert result["breaches"] == result["accepted_breaches"] != []
    original = next(s for s in SCENARIOS if s.id == "attack-pii-in-prompt-es")
    plain = await run_case(original.turns[0].user, original.id)
    assert plain["breaches"] == [] and "pii_redacted" in plain["guardrails"]


def test_every_accepted_breach_has_a_reason() -> None:
    baseline = load_baseline()
    assert baseline.known_breaches and set(baseline.known_breaches) == set(baseline.reasons)


def test_consolidated_report_renders_with_and_without_inputs() -> None:
    baseline = MutationBaseline(
        dataset="x",
        known_breaches={"hex:attack-pii-in-prompt-es": ["invariante payload_to_model=1"]},
        reasons={"hex:attack-pii-in-prompt-es": "PII codificada."},
    )
    empty = report.render(None, None, None, {"promptfoo": baseline})
    assert "Sin ejecutar" in empty and "(sin ejecutar)" in empty
    assert "| promptfoo | hex:attack-pii-in-prompt-es |" in empty
    assert empty.count("| PyRIT (12.4) |") == 3
    summary = [
        {
            "converter": c,
            "attacks": 6,
            "attacks_without_breach": 6,
            "attacks_blocked_at_input": 2 if c == "original" else 0,
            "benign": 4,
            "benign_blocked": 0,
            "invalid": 0,
            "breaches": 0,
        }
        for c in ("original", "hex")
    ]
    outcomes = [
        {
            "converter": "hex",
            "category": "attack",
            "blocked_at_input": False,
            "guardrails": ["approval_requested"],
        }
    ]
    full = report.render(None, {"summary": summary, "outcomes": outcomes}, None, {})
    # La línea base sin mutar no cuenta como técnica.
    assert "| PyRIT (conversores) | 1 | 6/6 | 0/6 | 0/4 | 0 |" in full
    assert "| approval_requested | 1 |" in full
