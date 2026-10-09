"""Juez RAGAS 0.4 (metricas `collections`) sobre nuestro `LLMPort` y `EmbeddingPort`.

No se usa `MultiModalFaithfulness`: su utilidad de carga de imagenes tiene un SSRF abierto
(CVE-2026-6587) y aqui no hace falta.
"""

import math
from typing import Any

from pydantic import BaseModel
from ragas.embeddings.base import BaseRagasEmbedding
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)

from headache_core.application.llm.structured import complete_structured
from headache_core.ports.llm import ChatMessage, LLMPort, Role
from headache_core.ports.vector_store import EmbeddingPort
from rag_evals.judges import JudgeSample, JudgeScores


class PortRagasLLM(InstructorBaseRagasLLM):
    """RAGAS pide `response_model` (Pydantic) y valida con isinstance: structured outputs."""

    def __init__(self, llm: LLMPort, *, max_tokens: int = 1500) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        raise RuntimeError("Solo async: usa ascore() desde un bucle de eventos")

    async def agenerate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        result = await complete_structured(
            self._llm,
            [ChatMessage(Role.USER, prompt)],
            response_model,
            max_tokens=self._max_tokens,
        )
        return result.value


class PortRagasEmbeddings(BaseRagasEmbedding):
    def __init__(self, embeddings: EmbeddingPort) -> None:
        super().__init__()
        self._embeddings = embeddings

    def embed_text(self, text: str, **kwargs: Any) -> list[float]:
        raise RuntimeError("Solo async: usa ascore() desde un bucle de eventos")

    async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
        return await self._embeddings.embed_query(text)


def _value(result: Any) -> float | None:
    value = float(result.value)
    return None if math.isnan(value) else value


class RagasJudge:
    name = "ragas"

    def __init__(self, llm: LLMPort, embeddings: EmbeddingPort) -> None:
        ragas_llm = PortRagasLLM(llm)
        self._faithfulness = Faithfulness(llm=ragas_llm)
        self._relevancy = AnswerRelevancy(llm=ragas_llm, embeddings=PortRagasEmbeddings(embeddings))
        self._precision = ContextPrecision(llm=ragas_llm)
        self._recall = ContextRecall(llm=ragas_llm)

    async def score(self, sample: JudgeSample) -> JudgeScores:
        contexts = list(sample.contexts)
        faithfulness = await self._faithfulness.ascore(
            user_input=sample.question, response=sample.answer, retrieved_contexts=contexts
        )
        relevancy = await self._relevancy.ascore(user_input=sample.question, response=sample.answer)
        precision = recall = None
        if sample.reference:
            precision = _value(
                await self._precision.ascore(
                    user_input=sample.question,
                    reference=sample.reference,
                    retrieved_contexts=contexts,
                )
            )
            recall = _value(
                await self._recall.ascore(
                    user_input=sample.question,
                    retrieved_contexts=contexts,
                    reference=sample.reference,
                )
            )
        return JudgeScores(
            faithfulness=_value(faithfulness),
            answer_relevancy=_value(relevancy),
            context_precision=precision,
            context_recall=recall,
        )
