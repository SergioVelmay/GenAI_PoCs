"""Juez DeepEval 4.x sobre nuestro `LLMPort` (modelo propio via `DeepEvalBaseLLM`)."""

import os

# `import deepeval` vuelca el `.env` en `os.environ` de todo el proceso (como litellm en modo
# DEV): pisaba la configuracion y rompia el aislamiento de los tests de otros paquetes.
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")

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

# Los prompts de DeepEval piden "reason" solo para los "no", pero structured outputs en modo
# strict exige todas las claves: gpt-4.1 no puede cerrar el objeto y degenera en espacios en
# blanco hasta `max_tokens` (medido en Azure). Se le dice explicitamente como rellenarlas.
STRICT_SCHEMA_NOTE = (
    "\n\nIMPORTANT: your output is validated against a strict JSON schema. Always include "
    'every key of the schema in every object; when a "reason" is not needed, set it to null '
    '(or "" if null is not allowed). Never pad the output with whitespace.'
)


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
        if schema is not None:
            result = await complete_structured(
                self._llm,
                [ChatMessage(Role.USER, prompt + STRICT_SCHEMA_NOTE)],
                schema,
                max_tokens=self._max_tokens,
            )
            return result.value
        messages = [ChatMessage(Role.USER, prompt)]
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
