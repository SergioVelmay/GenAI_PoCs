from collections import Counter
from pathlib import Path

import pytest

from rag_evals.calibration import (
    ADJUDICATED_PATH,
    BUDGET_ERROR,
    KIND_PLAN,
    LABELS_PATH,
    SET_PATH,
    HumanLabel,
    JudgedItem,
    _change_number,
    agreement,
    build_set,
    judge_faithful,
    judge_set,
    load_labels,
    load_set,
    perturb,
    render_agreement,
    render_readable,
)
from rag_evals.golden import DATASET_PATH, GoldenItem, load_golden
from rag_evals.judges import JudgeError, JudgeSample, JudgeScores
from rag_evals.metering import BudgetExceededError


def _item(item_id: str, topic: str, answer: str, language: str = "en") -> GoldenItem:
    return GoldenItem(
        id=item_id,
        question="q",
        language=language,
        question_type="factual",
        topic=topic,
        relevance={"d": 2},
        expected_answer=answer,
        evidence=["x"],
    )


def test_perturbations_are_deterministic_and_language_aware() -> None:
    item = _item("a", "refunds", "Up to 48 hours before.", "es")
    other = _item("b", "doors", "15 minutes before.")
    assert perturb(item, "good", other) == "Up to 48 hours before."
    assert perturb(item, "fabricated", other) == "Up to 96 hours before."
    assert "parking" in (perturb(item, "unsupported", other) or "")
    assert perturb(item, "off_topic", other) == "15 minutes before."
    assert perturb(item, "off_topic", _item("c", "refunds", "z")) is None
    assert "Headache" in (perturb(item, "non_answer", other) or "")
    assert _change_number("100 %") == "50 %"
    assert _change_number("sin cifras") is None


def test_committed_set_matches_the_plan_and_hides_the_kind() -> None:
    items = load_set(SET_PATH)
    assert Counter(item.kind for item in items) == Counter(KIND_PLAN)
    readable = render_readable(items)
    assert all(item.id in readable for item in items)
    assert not any(f"`{kind}`" in readable for kind in set(KIND_PLAN))
    assert "fabricated" not in readable and "off_topic" not in readable


async def test_build_is_reproducible() -> None:
    _, golden = load_golden(DATASET_PATH)
    rebuilt = await build_set(golden)
    assert [item.model_dump() for item in rebuilt] == [i.model_dump() for i in load_set()]


def test_labels_skip_empty_rows_and_reject_invalid_ones(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("id,faithful,relevance,notes\na,si,3,\nb,,,\n", encoding="utf-8")
    assert load_labels(path) == {"a": HumanLabel("a", "si", 3)}
    path.write_text("id,faithful,relevance,notes\na,quizas,3,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalida"):
        load_labels(path)


def test_agreement_compares_with_humans_and_reports_detection() -> None:
    faithful = JudgeScores(1.0, 1.0, None, None)
    unfaithful = JudgeScores(0.5, 1 / 3, None, None)
    judged = [
        JudgedItem("1", "good", faithful, None),
        JudgedItem("2", "fabricated", unfaithful, None),
        JudgedItem("3", "unsupported", faithful, None),
        JudgedItem("4", "non_answer", None, "incoherente"),
    ]
    labels = {
        "1": HumanLabel("1", "si", 3),
        "2": HumanLabel("2", "no", 3),
        "3": HumanLabel("3", "no", 2),
    }
    result = agreement("native", judged, labels)
    assert (result.samples, result.judge_errors) == (3, 1)
    assert result.faithful_accuracy == pytest.approx(0.667)
    assert result.relevance_accuracy == pytest.approx(0.667)
    assert result.detected["fabricated"] == "1/1"
    assert result.detected["unsupported"] == "0/1"
    assert result.detected["non_answer"] == "0/0"
    assert judge_faithful(JudgeScores(None, 0.0, None, None)) == "na"
    report = render_agreement({"Etiquetas a ciegas": [result], "Etiquetas adjudicadas": [result]})
    assert "## Etiquetas a ciegas" in report and "## Etiquetas adjudicadas" in report


def test_committed_labels_are_complete_and_valid() -> None:
    ids = {item.id for item in load_set()}
    for path in (LABELS_PATH, ADJUDICATED_PATH):
        assert set(load_labels(path)) == ids


async def test_judge_errors_are_recorded_not_fatal() -> None:
    class Broken:
        name = "broken"

        async def score(self, sample: JudgeSample) -> JudgeScores:
            raise JudgeError("incoherente")

    [first, *_] = await judge_set(load_set()[:2], Broken())
    assert first.scores is None and first.error == "incoherente"


async def test_budget_stop_keeps_what_was_already_judged() -> None:
    class Metered:
        name = "metered"
        calls = 0

        async def score(self, sample: JudgeSample) -> JudgeScores:
            self.calls += 1
            if self.calls > 2:
                raise BudgetExceededError("agotado")
            return JudgeScores(1.0, 1.0, None, None)

    judged = await judge_set(load_set()[:5], Metered())
    assert [item.error for item in judged] == [None, None] + [BUDGET_ERROR] * 3
