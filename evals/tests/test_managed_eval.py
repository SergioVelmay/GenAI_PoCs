from collections.abc import Mapping

from headache_core.application.retrieval import InvalidCitationError
from headache_core.domain.retrieval import AccessScope
from headache_core.ports.llm import LLMContentFilterError
from headache_core.ports.retrieval import (
    ManagedCitation,
    ManagedRagResponse,
    ManagedReference,
    ManagedSearchMode,
)
from headache_core.ports.vector_store import MetadataValue
from rag_evals.golden import DATASET_PATH, GoldenItem, load_golden
from rag_evals.judges import JudgeError, JudgeSample, JudgeScores
from rag_evals.managed_eval import (
    NO_CITATIONS,
    contexts_of,
    evaluate_managed,
    render_markdown,
    sample_items,
    summarize_managed,
)

POLICY = ManagedReference("p::0", "policy", "Policy", "Refundable up to 48 hours.", "u")
SECRET = ManagedReference("h::0", "handbook", "Handbook", "Internal only.", "u")


def _item(item_id: str, *, answerable: bool = True, forbidden: tuple[str, ...] = ()) -> GoldenItem:
    return GoldenItem(
        id=item_id,
        question=item_id,
        language="en",
        question_type="factual" if answerable else "unanswerable",
        topic="t",
        relevance={"policy": 2} if answerable else {},
        expected_answer="48 hours." if answerable else None,
        evidence=["48 hours"] if answerable else [],
        forbidden_document_ids=list(forbidden),
    )


class FakeKnowledge:
    def __init__(self, responses: Mapping[str, ManagedRagResponse | Exception]) -> None:
        self._responses = responses

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> list[ManagedReference]:
        return []

    async def answer(
        self,
        query: str,
        *,
        top_k: int,
        filters: Mapping[str, MetadataValue],
        scope: AccessScope,
        mode: ManagedSearchMode = "SEMANTIC",
    ) -> ManagedRagResponse:
        response = self._responses[query]
        if isinstance(response, Exception):
            raise response
        return response


class ScriptedJudge:
    name = "scripted"

    def __init__(self, faithfulness: float | None, *, fail: bool = False) -> None:
        self.faithfulness = faithfulness
        self.fail = fail
        self.samples: list[JudgeSample] = []

    async def score(self, sample: JudgeSample) -> JudgeScores:
        self.samples.append(sample)
        if self.fail:
            raise JudgeError("incoherente")
        return JudgeScores(self.faithfulness, 1.0, None, None)


def _cited(answer: str, *references: ManagedReference) -> ManagedRagResponse:
    return ManagedRagResponse(answer, (ManagedCitation(answer, 0, 1, references),), False)


def test_contexts_are_the_cited_texts_without_duplicates() -> None:
    response = ManagedRagResponse(
        "a",
        (
            ManagedCitation("a", 0, 1, (POLICY,)),
            ManagedCitation("b", 1, 2, (POLICY, SECRET)),
        ),
        False,
    )
    assert contexts_of(response) == (
        ("Refundable up to 48 hours.", "Internal only."),
        ("policy", "handbook"),
    )


async def test_judges_cited_answers_and_flags_ungrounded_and_over_answers() -> None:
    knowledge = FakeKnowledge(
        {
            "ok": _cited("Up to 48 hours. Parking is free.", POLICY, SECRET),
            "over": _cited("Up to 48 hours. Parking is free.", POLICY, SECRET),
        }
    )
    judge = ScriptedJudge(0.5)
    outcomes = await evaluate_managed(
        knowledge,
        [_item("ok", forbidden=("handbook",)), _item("over", answerable=False)],
        judge,
    )
    assert judge.samples[0].contexts == ("Refundable up to 48 hours.", "Internal only.")
    assert judge.samples[0].reference == "48 hours." and judge.samples[1].reference is None
    summary = summarize_managed("foundry_iq", outcomes)
    assert summary.over_answers == ["over"]
    assert summary.unfaithful == ["ok", "over"] and summary.faithful_rate == 0.0
    assert summary.acl_leaks == 1
    assert "foundry_iq" in render_markdown(summary, outcomes, "0.01")


async def test_answers_without_citations_are_unfaithful_without_spending_judge() -> None:
    knowledge = FakeKnowledge({"bare": ManagedRagResponse("Yes, always.", (), False)})
    judge = ScriptedJudge(1.0)
    [outcome] = await evaluate_managed(knowledge, [_item("bare")], judge)
    assert judge.samples == [] and outcome.judge_error == NO_CITATIONS
    summary = summarize_managed("foundry_iq", [outcome])
    assert summary.unfaithful == ["bare"] and summary.judge_errors == 0


async def test_abstentions_and_judge_errors_are_not_scored() -> None:
    knowledge = FakeKnowledge({"x": _cited("48 hours.", POLICY)})
    [failed] = await evaluate_managed(knowledge, [_item("x")], ScriptedJudge(1.0, fail=True))
    assert failed.scores is None and failed.judge_error == "incoherente"
    abstain = FakeKnowledge({"y": ManagedRagResponse("No lo sé.", (), True)})
    [quiet] = await evaluate_managed(abstain, [_item("y", answerable=False)], ScriptedJudge(1.0))
    summary = summarize_managed("foundry_iq", [failed, quiet])
    assert summary.judge_errors == 1 and summary.correct_abstentions == 1
    assert summary.faithful_rate is None


async def test_service_guards_are_counted_as_non_answers_not_fatal() -> None:
    knowledge = FakeKnowledge(
        {
            "uncited": InvalidCitationError("Respuesta del agente contiene texto sin cita"),
            "jailbreak": LLMContentFilterError("content_filter: jailbreak"),
        }
    )
    judge = ScriptedJudge(1.0)
    outcomes = await evaluate_managed(
        knowledge, [_item("uncited"), _item("jailbreak", answerable=False)], judge
    )
    assert [o.status for o in outcomes] == ["rejected", "blocked"]
    assert judge.samples == []
    summary = summarize_managed("foundry_iq", outcomes)
    assert (summary.rejected, summary.blocked) == (["uncited"], ["jailbreak"])
    assert summary.correct_abstentions == 1 and summary.answered == 0
    assert "rejected" in render_markdown(summary, outcomes, "0")


def test_sample_is_deterministic_and_unfiltered() -> None:
    _, items = load_golden(DATASET_PATH)
    first, second = sample_items(items, 10, 5), sample_items(items, 10, 5)
    assert [i.id for i in first] == [i.id for i in second]
    assert all(not item.filters for item in first)
    assert sum(item.answerable for item in first) == 10
