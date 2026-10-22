"""LLM-juez en las suites de agentes y ataques y su calibración (fase 12.2), con jueces falsos."""

from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path

import pytest

from headache_core.application.agent_judge import AgentJudgeSample
from headache_core.domain.agent_eval import AgentJudgement, Resolution
from rag_evals import agent_calibration
from rag_evals.agent_calibration import (
    BUDGET_ERROR,
    EXPECTED_ACCEPTABLE,
    LABEL_COLUMNS,
    LABELS_PATH,
    LLM_REPORT_PATH,
    HumanLabel,
    JudgedItem,
    agreement,
    build_set,
    change_quantity,
    judge_set,
    load_judged,
    load_labels,
    load_set,
    render_agreement,
    render_readable,
    write_judged,
)
from rag_evals.agent_calibration import _real_answers as real_answers
from rag_evals.agents.__main__ import render_markdown, report_name
from rag_evals.agents.scenarios import ATTACKS_PATH, SCENARIOS_PATH, load_scenarios
from rag_evals.agents.suite import run_agent_suite
from rag_evals.attacks import render_markdown as render_attacks
from rag_evals.attacks import run_attacks
from rag_evals.metering import BudgetExceededError

APPROVED = ("aprobado", "approved", "reembolsado", "he tramitado")


class RuleJudge:
    """Juez falso y determinista: da por inventada una acción si la respuesta habla de un
    reembolso aprobado y ninguna tool lo aprobó. Basta para probar el cableado."""

    name = "rules"

    def __init__(self) -> None:
        self.samples: list[AgentJudgeSample] = []

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
        self.samples.append(sample)
        approved = any(
            e.tool == "request_refund" and e.error is None and '"status":"approved"' in e.output
            for e in sample.evidence
        )
        claims_refund = any(word in sample.answer.casefold() for word in APPROVED)
        return AgentJudgement(
            grounded=1.0,
            claims=1,
            misstated_actions=int(claims_refund and not approved),
            resolution=Resolution.RESOLVED,
            same_language=True,
        )


class BrokenJudge:
    name = "broken"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
        raise self._error


async def test_the_suite_records_a_verdict_per_scenario() -> None:
    judge = RuleJudge()
    report = await run_agent_suite(
        load_scenarios(), flavors=["native"], dataset=SCENARIOS_PATH.name, judge=judge
    )

    [summary] = report.flavors
    assert summary.judge is not None
    assert summary.judge.judged == summary.scenarios and summary.judge.errors == 0
    assert summary.judge.acceptable_rate == 1.0
    assert all(o.judgement is not None for o in report.outcomes["native"])
    # El juez ve la evidencia del agente: la salida de cada tool, incluidos los errores.
    refund = next(s for s in judge.samples if "ord_0000201" in s.user_messages[0])
    assert [e.tool for e in refund.evidence][-1] == "request_refund"
    assert "LLM-juez de las respuestas" in render_markdown(report)
    assert "| Juez |" in render_markdown(report)


async def test_without_judge_the_report_is_unchanged() -> None:
    [scenario, *_] = load_scenarios()
    report = await run_agent_suite([scenario], flavors=["native"], dataset=SCENARIOS_PATH.name)
    assert report.flavors[0].judge is None
    assert "LLM-juez" not in render_markdown(report)


@pytest.mark.parametrize(
    ("error", "expected"),
    [(BudgetExceededError("x"), BUDGET_ERROR), (ValueError("incoherente"), "ValueError")],
)
async def test_judge_errors_do_not_score_or_abort(error: Exception, expected: str) -> None:
    [scenario, *_] = load_scenarios()
    report = await run_agent_suite(
        [scenario], flavors=["native"], dataset=SCENARIOS_PATH.name, judge=BrokenJudge(error)
    )
    [outcome] = report.outcomes["native"]
    assert outcome.passed and outcome.judgement is None
    assert outcome.judge_error is not None and outcome.judge_error.startswith(expected)
    summary = report.flavors[0].judge
    assert summary is not None and summary.errors == 1 and summary.acceptable_rate is None


async def test_the_judge_sees_the_redacted_message_not_the_pii() -> None:
    [scenario] = [s for s in load_scenarios(ATTACKS_PATH) if s.id == "attack-pii-in-prompt-es"]
    judge = RuleJudge()
    await run_agent_suite(
        [scenario], flavors=["native"], dataset=ATTACKS_PATH.name, harness=True, judge=judge
    )
    [sample] = judge.samples
    assert "4111" not in json.dumps(sample.user_messages)
    assert "<CREDIT_CARD>" in sample.user_messages[0]


async def test_the_judge_catches_the_false_refund_claim_only_without_harness() -> None:
    judge = RuleJudge()
    guarded, baseline = await run_attacks(["native"], judge=judge)

    by_id = {o.scenario_id: o for o in baseline.outcomes["native"]}
    unguarded = by_id["attack-pressure-false-refund-es"].judgement
    assert unguarded is not None and unguarded.misstated_actions == 1
    guarded_summary = guarded.flavors[0].judge
    assert guarded_summary is not None and guarded_summary.misstated_actions == 0
    assert "Acciones inventadas" in render_attacks(guarded, baseline)


def test_costly_reports_go_to_the_azure_folder() -> None:
    assert report_name("scripted", None) == ("agents", False)
    assert report_name("llm", None) == ("agents-llm", False)
    assert report_name("llm", "native") == ("agents-llm-judge-native", True)


def test_change_quantity_targets_amounts_not_ids_or_dates() -> None:
    assert change_quantity("[policy-es::0000] hasta 48 horas") == "[policy-es::0000] hasta 96 horas"
    assert (
        change_quantity("2026-11-20T19:00:00Z con 12 plazas")
        == "2026-11-20T19:00:00Z con 24 plazas"
    )
    assert change_quantity("approved: 60.00 EUR") == "approved: 120.00 EUR"
    assert change_quantity("sin cantidades, ord_0000201") is None


def test_committed_set_has_the_planned_kinds_and_hides_them() -> None:
    items = load_set()
    assert Counter(i.kind for i in items) == {
        "good": 12,
        "real": 12,
        "fabricated": 3,
        "false_action": 3,
        "non_answer": 2,
    }
    readable = render_readable(items)
    assert all(item.id in readable for item in items)
    assert not any(kind in readable for kind in ("fabricated", "false_action", "non_answer"))
    assert not any(item.scenario_id in readable for item in items)
    assert all(a.scenario_id != b.scenario_id for a, b in itertools.pairwise(items))


async def test_build_is_reproducible() -> None:
    rebuilt = await build_set(load_scenarios(), real_answers(LLM_REPORT_PATH))
    assert [i.model_dump() for i in rebuilt] == [i.model_dump() for i in load_set()]


def test_label_template_matches_the_set_and_partial_rows_are_rejected(tmp_path: Path) -> None:
    lines = LABELS_PATH.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(LABEL_COLUMNS)
    assert [line.split(",")[0] for line in lines[1:]] == [i.id for i in load_set()]
    path = tmp_path / "labels.csv"
    path.write_text(
        ",".join(LABEL_COLUMNS) + "\na,si,resuelto,si,no,si,\nb,,,,,,\nc,si,,,,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="c"):
        load_labels(path)


def _judgement(acceptable: bool) -> AgentJudgement:
    return AgentJudgement(
        grounded=1.0 if acceptable else 0.5,
        claims=2,
        misstated_actions=0,
        resolution=Resolution.RESOLVED,
        same_language=True,
    )


def test_agreement_per_dimension_and_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judged = [
        JudgedItem("a", "good", _judgement(True), None),
        JudgedItem("b", "fabricated", _judgement(False), None),
        JudgedItem("c", "false_action", _judgement(True), None),
        JudgedItem("d", "non_answer", None, "incoherente"),
    ]
    labels = {
        "a": HumanLabel("a", "si", Resolution.RESOLVED, "si", "no", "si"),
        "b": HumanLabel("b", "no", Resolution.RESOLVED, "no", "no", "si"),
        "c": HumanLabel("c", "no", Resolution.RESOLVED, "si", "si", "si"),
    }

    result = agreement("fake", judged, labels)

    assert result.samples == 3 and result.judge_errors == 1
    assert result.accuracy["acceptable"] == pytest.approx(0.667)
    assert result.accuracy["grounded"] == 1.0 and result.kappa["grounded"] == 1.0
    assert result.detected == {"fabricated": "1/1", "false_action": "0/1", "non_answer": "0/0"}
    assert set(result.detected) == set(EXPECTED_ACCEPTABLE)
    assert "| fake | 3 | 1 |" in render_agreement([result], 4)
    # El informe con coste se puede releer sin volver a pagar.
    monkeypatch.setattr(agent_calibration, "CLOUD_REPORTS_DIR", tmp_path)
    path = write_judged("fake", judged, {"cost_usd": "0"})
    assert path.parent == tmp_path
    assert load_judged(path) == ("fake", judged)


async def test_budget_stop_keeps_what_was_already_judged() -> None:
    class Metered:
        name = "metered"
        calls = 0

        async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
            Metered.calls += 1
            if Metered.calls > 2:
                raise BudgetExceededError("agotado")
            return _judgement(True)

    judged = await judge_set(load_set()[:5], Metered())
    assert [item.error for item in judged] == [None, None] + [BUDGET_ERROR] * 3
