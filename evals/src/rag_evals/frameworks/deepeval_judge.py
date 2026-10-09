"""Juez DeepEval 4.x sobre nuestro `LLMPort` (modelo propio via `DeepEvalBaseLLM`)."""

from typing import Any

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    FaithfulnessMetric,
)
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from pydantic import BaseModel

from headache_core.application.llm.structured import complete_structured
from headache_core.ports.llm import ChatMessage, LLMPort, Role
from rag_evals.judges import JudgeSample, JudgeScores


class PortDeepEvalLLM(DeepEvalBaseLLM):
    """DeepEval pasa `schema=`; devolver una instancia evita su parseo JSON tolerante."""

    def __init__(self, llm: LLMPort, *, name: str = "headache-gateway", max_tokens: int = 1500):
        self._llm = llm
        self._max_tokens = max_tokens
        super().__init__(name)

    def load_model(self, *args: Any, **kwargs: Any) -> "PortDeepEvalLLM":
        return self

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return str(self.name)

    def generate(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("Solo async: usa a_measure() con async_mode=True")

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        messages = [ChatMessage(Role.USER, prompt)]
        if schema is not None:
            result = await complete_structured(
                self._llm, messages, schema, max_tokens=self._max_tokens
            )
            return result.value
        return (await self._llm.complete(messages, max_tokens=self._max_tokens)).content


class DeepEvalJudge:
    name = "deepeval"

    def __init__(self, llm: LLMPort) -> None:
        self._model = PortDeepEvalLLM(llm)

    def _options(self) -> dict[str, Any]:
        # Sin "reason": ahorra una llamada por metrica; la puntuacion no depende de ella.
        return {"model": self._model, "include_reason": False, "async_mode": True}

    async def _measure(self, metric: Any, case: LLMTestCase) -> float | None:
        value = await metric.a_measure(case, _show_indicator=False)
        return None if value is None else float(value)

    async def score(self, sample: JudgeSample) -> JudgeScores:
        case = LLMTestCase(
            input=sample.question,
            actual_output=sample.answer,
            expected_output=sample.reference,
            retrieval_context=list(sample.contexts),
        )
        options = self._options()
        precision = recall = None
        if sample.reference:
            precision = await self._measure(ContextualPrecisionMetric(**options), case)
            recall = await self._measure(ContextualRecallMetric(**options), case)
        return JudgeScores(
            faithfulness=await self._measure(FaithfulnessMetric(**options), case),
            answer_relevancy=await self._measure(AnswerRelevancyMetric(**options), case),
            context_precision=precision,
            context_recall=recall,
        )
