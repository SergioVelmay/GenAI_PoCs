import json
from collections.abc import Mapping, Sequence
from importlib.util import find_spec

import pytest

from headache_core.adapters.memory import FakeLLM, HashingEmbedding
from headache_core.ports.llm import (
    ChatMessage,
    LLMResponse,
    ResponseSchema,
    TokenUsage,
    ToolConfig,
)
from rag_evals.judges import JudgeError, JudgeSample, NativeJudge

SAMPLE = JudgeSample(
    question="How many hours before can I get a refund?",
    answer="Refunds are allowed up to 48 hours before. Parking is free.",
    contexts=("Refundable: yes, up to 48 hours before the session.", "Venue opens at 7 pm."),
    reference="Up to 48 hours before the session.",
)
# Sin el extra `evals` (CI) se saltan; con el extra instalado, un import roto debe fallar.
needs_ragas = pytest.mark.skipif(find_spec("ragas") is None, reason="extra evals")
needs_deepeval = pytest.mark.skipif(find_spec("deepeval") is None, reason="extra evals")


class SchemaRoutedLLM:
    """Responde segun el nombre del esquema pedido: robusto a llamadas concurrentes."""

    def __init__(self, routes: Mapping[str, str]) -> None:
        self._routes = routes
        self.seen: list[str] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        name = response_schema.name if response_schema is not None else "text"
        self.seen.append(name)
        return LLMResponse(self._routes[name], "gpt-4.1-nano", TokenUsage(10, 5), "fake")


async def test_native_judge_computes_scores_from_labels() -> None:
    verdict = {
        "answer_claims": [
            {"claim": "Refunds up to 48 hours before", "supported": True},
            {"claim": "Parking is free", "supported": False},
        ],
        "relevance": 2,
        "contexts": [{"index": 1, "relevant": False}, {"index": 0, "relevant": True}],
        "reference_claims": [{"claim": "Up to 48 hours before", "supported": True}],
    }
    llm = FakeLLM([json.dumps(verdict)])
    scores = await NativeJudge(llm).score(SAMPLE)
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy == pytest.approx(2 / 3)
    assert scores.context_precision == 1.0
    assert scores.context_recall == 1.0
    assert llm.schemas[0] is not None and llm.schemas[0].name == "JudgeVerdict"
    payload = json.loads(llm.calls[0][-1].content)
    assert [context["index"] for context in payload["contexts"]] == [0, 1]


async def test_native_judge_rejects_verdicts_that_skip_contexts() -> None:
    verdict = {
        "answer_claims": [],
        "relevance": 0,
        "contexts": [{"index": 0, "relevant": True}],
        "reference_claims": [],
    }
    with pytest.raises(JudgeError):
        await NativeJudge(FakeLLM([json.dumps(verdict)])).score(SAMPLE)


async def test_native_judge_without_reference_has_no_recall() -> None:
    verdict = {
        "answer_claims": [],
        "relevance": 3,
        "contexts": [{"index": 0, "relevant": False}, {"index": 1, "relevant": False}],
        "reference_claims": [{"claim": "ignored", "supported": True}],
    }
    sample = JudgeSample(SAMPLE.question, SAMPLE.answer, SAMPLE.contexts)
    scores = await NativeJudge(FakeLLM([json.dumps(verdict)])).score(sample)
    assert scores.faithfulness is None
    assert scores.context_precision == 0.0
    assert scores.context_recall is None


@needs_ragas
async def test_ragas_judge_runs_through_our_llm_port() -> None:
    from rag_evals.frameworks.ragas_judge import RagasJudge

    llm = SchemaRoutedLLM(
        {
            "StatementGeneratorOutput": json.dumps({"statements": ["a", "b"]}),
            "NLIStatementOutput": json.dumps(
                {
                    "statements": [
                        {"statement": "a", "reason": "in context", "verdict": 1},
                        {"statement": "b", "reason": "not in context", "verdict": 0},
                    ]
                }
            ),
            "AnswerRelevanceOutput": json.dumps({"question": SAMPLE.question, "noncommittal": 0}),
        }
    )
    sample = JudgeSample(SAMPLE.question, SAMPLE.answer, SAMPLE.contexts)
    scores = await RagasJudge(llm, HashingEmbedding(64)).score(sample)
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy == pytest.approx(1.0)
    assert scores.context_precision is None and scores.context_recall is None
    assert set(llm.seen) == {
        "StatementGeneratorOutput",
        "NLIStatementOutput",
        "AnswerRelevanceOutput",
    }


@needs_deepeval
async def test_deepeval_judge_runs_through_our_llm_port() -> None:
    from rag_evals.frameworks.deepeval_judge import DeepEvalJudge

    verdicts = {"verdicts": [{"verdict": "yes", "reason": None}, {"verdict": "no", "reason": "x"}]}
    llm = SchemaRoutedLLM(
        {
            "Truths": json.dumps({"truths": ["Refunds up to 48 hours before."]}),
            "Claims": json.dumps({"claims": ["a", "b"]}),
            "Statements": json.dumps({"statements": ["a", "b"]}),
            "Verdicts": json.dumps(verdicts),
        }
    )
    sample = JudgeSample(SAMPLE.question, SAMPLE.answer, SAMPLE.contexts)
    scores = await DeepEvalJudge(llm).score(sample)
    assert scores.faithfulness == 0.5
    assert scores.answer_relevancy == 0.5
    assert scores.context_precision is None and scores.context_recall is None
    assert "text" not in llm.seen
