"""Jueces LLM de RAG (carril B): el modelo etiqueta, el dominio calcula la puntuacion.

Las cuatro metricas clasicas de RAG (las mismas que RAGAS y DeepEval):

- faithfulness: fraccion de afirmaciones de la respuesta respaldadas por los contextos.
- answer_relevancy: cuanto responde la respuesta a la pregunta (escala 0-3 normalizada).
- context_precision: los contextos utiles estan arriba del ranking (precision media).
- context_recall: fraccion de hechos de la referencia presentes en los contextos.

El juez nativo hace UNA llamada estructurada por muestra; las puntuaciones se calculan en
Python con funciones puras, asi el veredicto es auditable y el coste por muestra es fijo.
"""

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.llm.structured import complete_structured
from headache_core.domain.evals import average_precision, ratio
from headache_core.ports.llm import ChatMessage, LLMPort, Role
from rag_evals.prompts import PROMPTS

MAX_CONTEXT_CHARS = 4000


class JudgeError(ValueError):
    """El veredicto del juez es incoherente con la muestra: no se puntua a ciegas."""


@dataclass(frozen=True, slots=True)
class JudgeSample:
    question: str
    answer: str
    contexts: tuple[str, ...]
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class JudgeScores:
    faithfulness: float | None
    answer_relevancy: float | None
    context_precision: float | None
    context_recall: float | None


class RagJudge(Protocol):
    name: str

    async def score(self, sample: JudgeSample) -> JudgeScores: ...


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(max_length=500)
    supported: bool


class ContextVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    relevant: bool


class JudgeVerdict(BaseModel):
    """Etiquetas del juez de RAG; las puntuaciones las calcula el codigo, no el modelo."""

    model_config = ConfigDict(extra="forbid")
    answer_claims: list[ClaimVerdict] = Field(max_length=30)
    relevance: Literal[0, 1, 2, 3]
    contexts: list[ContextVerdict] = Field(max_length=20)
    reference_claims: list[ClaimVerdict] = Field(max_length=30)


JUDGE_INSTRUCTIONS = PROMPTS.get("rag-judge").text


def scores_from_verdict(verdict: JudgeVerdict, sample: JudgeSample) -> JudgeScores:
    indexes = sorted(context.index for context in verdict.contexts)
    if indexes != list(range(len(sample.contexts))):
        raise JudgeError("El juez debe etiquetar cada contexto exactamente una vez")
    ranked = sorted(verdict.contexts, key=lambda context: context.index)
    return JudgeScores(
        faithfulness=ratio([claim.supported for claim in verdict.answer_claims]),
        answer_relevancy=verdict.relevance / 3,
        context_precision=average_precision([context.relevant for context in ranked]),
        context_recall=(
            ratio([claim.supported for claim in verdict.reference_claims])
            if sample.reference
            else None
        ),
    )


class NativeJudge:
    """Juez del carril B: nuestro `LLMPort` + structured outputs, sin framework."""

    name = "native"

    def __init__(self, llm: LLMPort, *, max_tokens: int = 1500) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    async def score(self, sample: JudgeSample) -> JudgeScores:
        payload = {
            "question": sample.question,
            "answer": sample.answer,
            "contexts": [
                {"index": index, "text": text[:MAX_CONTEXT_CHARS]}
                for index, text in enumerate(sample.contexts)
            ],
            "reference": sample.reference,
        }
        result = await complete_structured(
            self._llm,
            [
                ChatMessage(Role.SYSTEM, JUDGE_INSTRUCTIONS, cache_checkpoint=True),
                ChatMessage(Role.USER, json.dumps(payload)),
            ],
            JudgeVerdict,
            max_tokens=self._max_tokens,
        )
        return scores_from_verdict(result.value, sample)
