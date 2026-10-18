from typing import Any

import pytest

from rag_evals.calibration import load_set
from rag_evals.foundry_eval import (
    groundedness_to_faithfulness,
    judged_from_rows,
    normalize,
    to_rows,
    token_cost,
)


def test_rows_carry_query_response_and_joined_contexts() -> None:
    items = load_set()[:2]
    rows = to_rows(items)
    assert [row["id"] for row in rows] == [item.id for item in items]
    assert rows[0]["query"] == items[0].question
    assert rows[0]["context"] == "\n\n".join(items[0].contexts)


def test_scores_are_normalized_and_groundedness_uses_the_pass_threshold() -> None:
    assert normalize(5) == 1.0 and normalize(4) == 0.75 and normalize(1) == 0.0
    assert normalize(None) is None and normalize(7) is None and normalize("5") is None
    assert normalize(True) is None
    # Calibración: Foundry da 3-4 a respuestas correctas pero escuetas; el umbral es su aprobado.
    assert groundedness_to_faithfulness(3) == 1.0 and groundedness_to_faithfulness(4) == 1.0
    assert groundedness_to_faithfulness(2) == 0.25
    assert groundedness_to_faithfulness(4, threshold=5) == 0.75
    assert groundedness_to_faithfulness(None) is None


def test_rows_become_judged_items_in_order() -> None:
    items = load_set()[:3]
    rows: list[dict[str, Any]] = [
        {"outputs.groundedness.groundedness": 5, "outputs.relevance.relevance": 4},
        {"outputs.groundedness.groundedness": 2, "outputs.relevance.relevance": 5},
        {"outputs.groundedness.groundedness": None, "outputs.relevance.relevance": 3},
    ]
    first, second, missing = judged_from_rows(items, rows)
    assert first.scores is not None and first.scores.faithfulness == 1.0
    assert first.scores.answer_relevancy == 0.75
    assert second.scores is not None and second.scores.faithfulness == 0.25
    assert missing.scores is None and missing.error is not None
    with pytest.raises(ValueError):
        judged_from_rows(items, rows[:2])


def test_cost_from_reported_tokens() -> None:
    rows: list[dict[str, Any]] = [
        {"outputs.groundedness.groundedness_prompt_tokens": 1_000_000},
        {"outputs.relevance.relevance_completion_tokens": 0, "outputs.x": "texto"},
        {
            "outputs.relevance.relevance_properties": {
                "prompt_tokens": 500_000,
                "completion_tokens": 125_000,
            }
        },
    ]
    assert token_cost(rows, "gpt-4.1") == {
        "input_tokens": "1500000",
        "output_tokens": "125000",
        "cost_usd": "4.000000",
    }
