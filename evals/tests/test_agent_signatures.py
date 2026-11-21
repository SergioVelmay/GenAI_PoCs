"""*Thought signatures* de Gemini a través de cada sabor (7.G1).

Gemini 3/4 devuelve una firma opaca con las llamadas a tools y exige recibirla de vuelta, en el
mismo paso, en las llamadas siguientes (sin ella, `400`). Aquí un modelo que firma como Gemini
(solo la primera llamada de cada paso) envuelve al `ScenarioLLM` y comprueba en cada petición
que cada sabor (bucle propio, puentes de framework, contrato de AgentCore) le devuelve cada
firma intacta. La única excepción permitida es la compactación: el historial conservado de los
turnos anteriores va sin firmas (`ShortTermMemory`), nunca con una firma cambiada.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from booking_agent.spec import SKILLS_DIR
from genaipocs_core.application.agents import SUMMARIZER_PROMPT, SkillCatalog
from genaipocs_core.application.retrieval import RetrievalService
from genaipocs_core.ports import ChatMessage, LLMPort, LLMResponse, ResponseSchema, Role, ToolConfig
from rag_evals.agents.scenario_llm import ScenarioLLM
from rag_evals.agents.scenarios import load_scenarios
from rag_evals.agents.suite import build_knowledge, run_scenario

FLAVORS = ["native", "langgraph", "llamaindex", "maf", "strands", "agentcore"]


class SigningLLM:
    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner
        self.signed: dict[str, str | None] = {}
        self.returned = 0  # firmas que volvieron intactas
        self.dropped = 0  # firmas de turnos anteriores quitadas (solo al compactar)
        self.problems: list[str] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        if not (messages and messages[0].content == SUMMARIZER_PROMPT):
            self._check(messages)
        response = await self._inner.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            tools=tools,
        )
        calls = tuple(
            replace(c, signature=f"sig-{c.id}" if index == 0 else None)
            for index, c in enumerate(response.tool_calls)
        )
        self.signed |= {c.id: c.signature for c in calls}
        return replace(response, tool_calls=calls)

    def _check(self, messages: Sequence[ChatMessage]) -> None:
        last_user = max((i for i, m in enumerate(messages) if m.role is Role.USER), default=-1)
        for index, message in enumerate(messages):
            for call in message.tool_calls:
                if call.id not in self.signed:
                    self.problems.append(f"llamada desconocida {call.id}")
                    continue
                expected = self.signed[call.id]
                if call.signature == expected:
                    self.returned += expected is not None
                elif index < last_user and call.signature is None:
                    self.dropped += 1
                else:
                    self.problems.append(f"{call.id}: {call.signature!r} en vez de {expected!r}")


@pytest.fixture(scope="module")
def knowledge() -> tuple[RetrievalService, frozenset[str]]:
    import asyncio

    return asyncio.run(build_knowledge())


@pytest.mark.parametrize("flavor", FLAVORS)
async def test_every_flavor_returns_gemini_signatures_intact(
    flavor: str, knowledge: tuple[RetrievalService, frozenset[str]]
) -> None:
    retrieval, internal = knowledge
    skills = SkillCatalog.from_directory(SKILLS_DIR)
    returned = dropped = 0
    for scenario in load_scenarios():
        llm = SigningLLM(ScenarioLLM(scenario))
        outcome = await run_scenario(
            flavor,
            scenario,
            retrieval=retrieval,
            skills=skills,
            internal_documents=internal,
            llm=llm,
        )

        assert outcome.failures == [], scenario.id
        assert llm.problems == [], scenario.id
        # Solo se quitan firmas cuando el turno compacta el historial.
        assert llm.dropped == 0 or outcome.compactions > 0, scenario.id
        returned += llm.returned
        dropped += llm.dropped

    # El paso de tools paralelo y los hilos de varios turnos ejercitan la ida y vuelta, y el hilo
    # largo de memoria ejercita la compactación.
    assert returned > 0 and dropped > 0
