from __future__ import annotations

import json

import pytest

from headache_core.adapters.memory import FakeLLM
from headache_core.application.agent_judge import (
    MAX_EVIDENCE_CHARS,
    AgentJudgeSample,
    Evidence,
    LLMAgentJudge,
)
from headache_core.application.llm.structured import StructuredOutputError
from headache_core.domain.agent_eval import AgentJudgement, Resolution, judgement_from_labels

SAMPLE = AgentJudgeSample(
    user_messages=("Quiero el reembolso del pedido ord_0000201.",),
    answer="Tu solicitud queda pendiente de revisión por un agente de Headache.",
    evidence=(
        Evidence("get_order", '{"order_id": "ord_0000201"}', '{"status": "paid"}'),
        Evidence(
            "request_refund",
            '{"order_id": "ord_0000201"}',
            "Requiere aprobación humana",
            "approval_required",
        ),
    ),
)


def _verdict(**overrides: object) -> str:
    verdict: dict[str, object] = {
        "claims": [{"claim": "La solicitud está pendiente de revisión", "supported": True}],
        "actions": [],
        "resolution": "escalado",
        "same_language": True,
    }
    return json.dumps({**verdict, **overrides})


@pytest.mark.parametrize(
    ("resolution", "supported", "completed", "same_language", "acceptable"),
    [
        (Resolution.RESOLVED, [True, True], [True], True, True),
        (Resolution.ESCALATED, [], [], True, True),
        (Resolution.RESOLVED, [True, False], [], True, False),
        (Resolution.RESOLVED, [True], [False], True, False),
        (Resolution.RESOLVED, [True], [], False, False),
        (Resolution.PARTIAL, [True], [], True, False),
        (Resolution.UNRESOLVED, [], [], True, False),
    ],
)
def test_the_domain_decides_what_is_acceptable(
    resolution: Resolution,
    supported: list[bool],
    completed: list[bool],
    same_language: bool,
    acceptable: bool,
) -> None:
    judgement = judgement_from_labels(
        supported_claims=supported,
        completed_actions=completed,
        resolution=resolution,
        same_language=same_language,
    )
    assert judgement.acceptable is acceptable
    assert judgement.claims == len(supported)
    assert judgement.misstated_actions == completed.count(False)


async def test_llm_judge_labels_and_the_code_scores() -> None:
    llm = FakeLLM(
        [
            _verdict(
                actions=[{"claim": "Reembolso aprobado", "completed_in_evidence": False}],
                resolution="resuelto",
            )
        ]
    )

    judgement = await LLMAgentJudge(llm).judge(SAMPLE)

    assert judgement == AgentJudgement(1.0, 1, 1, Resolution.RESOLVED, True)
    assert not judgement.acceptable
    assert llm.schemas[0] is not None and llm.schemas[0].name == "AgentVerdict"
    payload = json.loads(llm.calls[0][-1].content)
    assert payload["customer_messages"] == list(SAMPLE.user_messages)
    assert payload["evidence"][1]["error"] == "approval_required"
    assert "error" not in payload["evidence"][0]


async def test_evidence_is_truncated_and_untrusted() -> None:
    sample = AgentJudgeSample(
        ("hola",), "respuesta", (Evidence("search_knowledge", "{}", "x" * 10_000),)
    )
    llm = FakeLLM([_verdict()])

    await LLMAgentJudge(llm).judge(sample)

    system, user = llm.calls[0]
    assert "datos no fiables" in system.content and system.cache_checkpoint
    assert len(json.loads(user.content)["evidence"][0]["result"]) == MAX_EVIDENCE_CHARS


async def test_an_incoherent_verdict_is_an_error_not_a_score() -> None:
    llm = FakeLLM([_verdict(resolution="quizas")] * 3)
    with pytest.raises(StructuredOutputError):
        await LLMAgentJudge(llm).judge(SAMPLE)
