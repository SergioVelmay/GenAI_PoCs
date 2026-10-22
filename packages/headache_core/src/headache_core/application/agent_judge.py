"""LLM-juez de respuestas de agentes (fase 12.2): una llamada estructurada por respuesta.

Mide lo que los comprobadores literales no pueden: si la respuesta resuelve lo que pidió el
cliente, si cada hecho que afirma (pedido, importe, fecha, política) sale de la evidencia que vio
el agente (salidas de sus tools), si da por hecha una acción que no ocurrió («reembolso aprobado»
cuando quedó pendiente) y si contesta en el idioma del cliente.

El modelo etiqueta y el dominio decide (`domain/agent_eval.py`). Sirve igual en offline (suite de
agentes y ataques) y en online (fase 12.3). El juez va fijo y fuera del router
(`build_judge_llm`): nunca el mismo modelo que genera.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.llm.structured import complete_structured
from headache_core.domain.agent_eval import AgentJudgement, Resolution, judgement_from_labels
from headache_core.ports.llm import ChatMessage, LLMPort, Role
from headache_core.prompts import PROMPTS

MAX_EVIDENCE_CHARS = 2000
MAX_EVIDENCE_ITEMS = 12


@dataclass(frozen=True, slots=True)
class Evidence:
    """Lo que el agente vio de verdad: una llamada a tool y su resultado (o su error)."""

    tool: str
    arguments: str
    output: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentJudgeSample:
    # Mensajes del cliente en orden (el último es el que se responde).
    user_messages: tuple[str, ...]
    answer: str
    evidence: tuple[Evidence, ...]


class AgentJudge(Protocol):
    name: str

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement: ...


class _Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(max_length=300)
    supported: bool


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(max_length=300)
    completed_in_evidence: bool


class AgentVerdict(BaseModel):
    """Solo etiquetas: la aceptación la calcula el dominio."""

    model_config = ConfigDict(extra="forbid")
    claims: list[_Claim] = Field(max_length=20)
    actions: list[_Action] = Field(max_length=10)
    resolution: Resolution
    same_language: bool


JUDGE_INSTRUCTIONS = PROMPTS.get("agent-judge").text


def _payload(sample: AgentJudgeSample) -> str:
    evidence = [
        {
            "tool": item.tool,
            "arguments": item.arguments,
            "result": item.output[:MAX_EVIDENCE_CHARS],
            **({"error": item.error} if item.error else {}),
        }
        for item in sample.evidence[-MAX_EVIDENCE_ITEMS:]
    ]
    return json.dumps(
        {
            "customer_messages": list(sample.user_messages),
            "agent_answer": sample.answer,
            "evidence": evidence,
        },
        ensure_ascii=False,
    )


class LLMAgentJudge:
    """Juez del carril B: nuestro `LLMPort` + structured outputs, sin framework."""

    name = "native"

    def __init__(self, llm: LLMPort, *, max_tokens: int = 1200) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    async def judge(self, sample: AgentJudgeSample) -> AgentJudgement:
        result = await complete_structured(
            self._llm,
            [
                ChatMessage(Role.SYSTEM, JUDGE_INSTRUCTIONS, cache_checkpoint=True),
                ChatMessage(Role.USER, _payload(sample)),
            ],
            AgentVerdict,
            max_tokens=self._max_tokens,
        )
        return verdict_to_judgement(result.value)


def verdict_to_judgement(verdict: AgentVerdict) -> AgentJudgement:
    return judgement_from_labels(
        supported_claims=[claim.supported for claim in verdict.claims],
        completed_actions=[action.completed_in_evidence for action in verdict.actions],
        resolution=verdict.resolution,
        same_language=verdict.same_language,
    )
