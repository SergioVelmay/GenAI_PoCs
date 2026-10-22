"""Red teaming offline (fase 12.4): mutaciones, regresión sin PyRIT y gate."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace

import pytest

from rag_evals.agents.scenarios import ATTACKS_PATH, load_scenarios
from rag_evals.redteam.converters import pyrit_available
from rag_evals.redteam.mutations import (
    MUTATIONS_PATH,
    MutationOutcome,
    build_mutations,
    check,
    load_baseline,
    model_view,
    run_mutations,
    summarize,
    visible,
)

needs_pyrit = pytest.mark.skipif(not pyrit_available(), reason="extra redteam no instalado")


@dataclass(frozen=True, slots=True)
class Upper:
    name: str = "upper"

    async def convert(self, text: str) -> str:
        return text.upper()


async def test_mutations_keep_the_plan_and_mark_the_mutated_pii_as_forbidden() -> None:
    [pii] = [s for s in load_scenarios(ATTACKS_PATH) if s.id == "attack-pii-in-prompt-es"]

    original, upper = await build_mutations([pii], [Upper()])

    assert (original.id, upper.id) == (f"original:{pii.id}", f"upper:{pii.id}")
    assert original.turns[0].user == pii.turns[0].user
    turn = upper.turns[0]
    assert turn.user == pii.turns[0].user.upper()
    assert turn.plan == pii.turns[0].plan
    # La tarjeta sigue detectándose en mayúsculas: el modelo ve el texto redactado.
    assert turn.model_sees == model_view(turn.user) and "<CREDIT_CARD>" in turn.model_sees
    assert turn.user in upper.expect.never_seen


async def test_frozen_mutations_run_without_pyrit_and_hold_the_baseline() -> None:
    scenarios = load_scenarios(MUTATIONS_PATH)
    converters = Counter(s.id.split(":")[0] for s in scenarios)
    assert len(converters) == 10 and set(converters.values()) == {10}

    outcomes = await run_mutations(scenarios)

    assert check(outcomes, load_baseline()) == []
    original = next(s for s in summarize(outcomes) if s.converter == "original")
    assert original.attacks_without_breach == original.attacks == 6
    assert all(s.benign_blocked == 0 and s.invalid == 0 for s in summarize(outcomes))


def test_the_gate_flags_new_breaches_false_positives_and_invalid_runs() -> None:
    ok = MutationOutcome("rot13", "attack-x", "attack", "m", False, [], [], False)
    baseline = load_baseline()
    assert check([ok], baseline) == []
    problems = check(
        [
            replace(ok, breaches=["invariante payload_to_model=1"]),
            replace(ok, scenario_id="benign-y", category="benign", blocked_at_input=True),
            replace(ok, invalid=True),
        ],
        baseline,
    )
    assert len(problems) == 3
    # Un riesgo residual aceptado deja de estarlo si cambia la brecha.
    known = replace(ok, converter="base64", scenario_id="attack-pii-in-prompt-es")
    assert check([replace(known, breaches=["invariante payload_to_model=2"])], baseline)


def test_invisible_characters_are_shown_in_the_report() -> None:
    assert visible("a\u200bb\U000e0041") == "a\\u200bb\\ue0041"
    assert visible("x" * 100, limit=10).endswith("…")


def test_the_frozen_dataset_is_ascii() -> None:
    assert MUTATIONS_PATH.read_text(encoding="utf-8").isascii()
    first = json.loads(MUTATIONS_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert first["id"].startswith("original:")


@needs_pyrit
async def test_pyrit_regenerates_the_same_frozen_dataset() -> None:
    from rag_evals.redteam.converters import pyrit_converters

    rebuilt = await build_mutations(load_scenarios(ATTACKS_PATH), pyrit_converters())
    frozen = load_scenarios(MUTATIONS_PATH)
    assert [s.model_dump() for s in rebuilt] == [s.model_dump() for s in frozen]
