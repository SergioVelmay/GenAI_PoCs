"""Fase 12.G2: rúbricas gestionadas del Evaluation Service frente al juez de agentes (coste 0).

Con los tipos reales de `agentplatform` (sin pandas: `client.evals` no se importa).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genaipocs_core.domain.agent_eval import Resolution
from rag_evals.agent_calibration import HumanLabel, load_set
from rag_evals.agents.gcp_rubrics import (
    FINAL_RESPONSE_QUALITY,
    HALLUCINATION,
    RubricScores,
    agreement,
    build_case,
    load_scores,
    predicted_labels,
    render,
    scores_from_outcomes,
    write_scores,
)
from rag_evals.gcp_eval import MetricOutcome


def test_each_sample_carries_the_tool_calls_and_results_the_agent_saw() -> None:
    item = next(i for i in load_set() if len(i.evidence) >= 2)

    case = build_case(item, "Eres el agente de reservas de Genaipocs.")

    assert case.eval_case_id == item.id
    assert case.prompt.parts[0].text == item.user_messages[-1]
    assert case.responses[0].response.parts[0].text == item.answer
    assert "agente de reservas" in case.system_instruction.parts[0].text
    events = case.intermediate_events
    assert len(events) == 2 * len(item.evidence)
    call, result = events[0].content.parts[0], events[1].content.parts[0]
    assert call.function_call.name == item.evidence[0].tool
    assert result.function_response.name == item.evidence[0].tool
    assert result.function_response.response["output"] == item.evidence[0].output


def test_earlier_messages_go_to_the_history() -> None:
    item = next(i for i in load_set() if len(i.user_messages) > 1)

    case = build_case(item, "instrucciones")

    assert [m.content.parts[0].text for m in case.conversation_history] == item.user_messages[:-1]


def _outcome(score: float | None, error: str | None = None) -> MetricOutcome:
    return MetricOutcome(score, None, error)


def test_a_failed_metric_leaves_the_sample_unscored() -> None:
    items = load_set()[:3]
    outcomes = [
        {HALLUCINATION: _outcome(1.0), FINAL_RESPONSE_QUALITY: _outcome(0.9)},
        {HALLUCINATION: _outcome(None, "cuota agotada"), FINAL_RESPONSE_QUALITY: _outcome(0.5)},
        {HALLUCINATION: _outcome(1.2)},
    ]

    scored = scores_from_outcomes(items, outcomes)

    assert scored[0].error is None and scored[0].final_response_quality == 0.9
    assert scored[1].error == "cuota agotada"
    assert "fuera de 0-1" in (scored[2].error or "") and "ausente" in (scored[2].error or "")
    assert predicted_labels(scored[1], 0.8) is None
    with pytest.raises(ValueError, match="2 casos para 3"):
        scores_from_outcomes(items, outcomes[:2])


def test_agreement_with_the_human_labels_and_detection(tmp_path: Path) -> None:
    scored = [
        RubricScores("a", "good", 1.0, 0.9, None),
        RubricScores("b", "fabricated", 0.5, 0.85, None),
        RubricScores("c", "false_action", 1.0, 0.4, None),
        RubricScores("d", "non_answer", None, None, "error"),
    ]
    labels = {
        "a": HumanLabel("a", "si", Resolution.RESOLVED, "si", "no", "si"),
        "b": HumanLabel("b", "no", Resolution.RESOLVED, "no", "no", "si"),
        "c": HumanLabel("c", "no", Resolution.RESOLVED, "na", "si", "si"),
    }

    strict = agreement(scored, labels, 0.8)
    lenient = agreement(scored, labels, 0.3)

    assert strict.samples == 3 and strict.errors == 1
    # grounded: «na» no se compara; a y b coinciden.
    assert strict.accuracy["grounded"] == 1.0
    assert strict.accuracy["acceptable"] == pytest.approx(0.667)
    assert strict.detected == {"fabricated": "0/1", "false_action": "1/1", "non_answer": "0/0"}
    assert lenient.detected["false_action"] == "0/1"
    assert "| 0.8 | 3 | 1 |" in render([strict, lenient], 4)
    path = write_scores(scored, {"samples": 4}, tmp_path)
    assert load_scores(path) == scored
