"""Evaluación online de agentes (fase 12.3): el juez de la fase 12.2 sobre una muestra real.

Misma maquinaria que la faithfulness del edge (fase 11.6): fuera del camino de la petición,
muestreo, cola acotada y presupuesto por minuto. Cambia qué se juzga: la respuesta de un turno de
agente con la evidencia de ESE turno (las tools que ejecutó).

    turno ─▶ EvaluatedAgent ─▶ ¿muestreado? ─▶ cola ─▶ juez ─▶ métrica + scores en Langfuse

- Solo turnos que terminan en una respuesta del modelo (`answer`): un bloqueo del harness o el
  fallback por `max_iterations` son textos fijos, no hay nada que juzgar.
- El juez nunca ve más que el agente: el mensaje del cliente pasa por el mismo redactor de PII
  que usa el harness antes de encolarse.
- `EvaluatedAgent` va dentro de `TracedAgent`: el span del juez cuelga del `invoke_agent`.
"""

from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from headache_core.application.agent_judge import AgentJudge, AgentJudgeSample, Evidence
from headache_core.application.agents.runtime import AgentRuntime, AgentTurn
from headache_core.application.online_eval import SampledEvaluator, Score, ScoreSink
from headache_core.application.telemetry import get_telemetry, metrics
from headache_core.application.tools.loop import ToolExecution
from headache_core.application.tools.registry import ToolContext
from headache_core.ports.telemetry import TraceCarrier

EVALUATOR = "online-agent"

type Redactor = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class AgentEvalSample:
    agent: str
    judged: AgentJudgeSample
    trace: TraceCarrier = field(default_factory=dict)


class AgentOnlineEvaluator(SampledEvaluator[AgentEvalSample]):
    name = EVALUATOR

    def __init__(
        self,
        judge: AgentJudge,
        *,
        sample_rate: float,
        max_per_minute: int = 10,
        queue_size: int = 100,
        sinks: Sequence[ScoreSink] = (),
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            max_per_minute=max_per_minute,
            queue_size=queue_size,
            sinks=sinks,
            rng=rng,
            clock=clock,
        )
        self._judge = judge

    def offer(self, sample: AgentEvalSample, *, stop_reason: str) -> bool:
        if stop_reason != "answer" or not sample.judged.answer.strip():
            return False
        return self._enqueue(sample)

    async def _score(self, sample: AgentEvalSample) -> list[Score]:
        judgement = await self._judge.judge(sample.judged)
        note = f"LLM-juez de agentes ({self._judge.name}): {judgement.resolution.value}"
        scores = [
            Score("agent_acceptable", float(judgement.acceptable), note),
            Score("agent_misstated_actions", float(judgement.misstated_actions)),
            Score("agent_same_language", float(judgement.same_language)),
        ]
        if judgement.grounded is not None:
            scores.append(Score("agent_grounded", judgement.grounded))
        metrics.agent_verdict(
            agent=sample.agent,
            acceptable=judgement.acceptable,
            resolution=judgement.resolution.value,
        )
        return scores


class EvaluatedAgent:
    """Decorador de `AgentRuntime`: tras cada turno ofrece la respuesta al evaluador online.

    `journal` es el diario vivo del ejecutor del agente: la evidencia del turno es lo que se
    añadió durante él. Nunca bloquea ni cambia el turno."""

    def __init__(
        self,
        inner: AgentRuntime,
        *,
        name: str,
        evaluator: AgentOnlineEvaluator,
        journal: Sequence[ToolExecution],
        redact: Redactor | None = None,
    ) -> None:
        self._inner = inner
        self._name = name
        self._evaluator = evaluator
        self._journal = journal
        self._redact = redact

    @property
    def flavor(self) -> str:
        return self._inner.flavor

    @property
    def inner(self) -> AgentRuntime:
        return self._inner

    async def respond(self, thread_id: str, message: str, context: ToolContext) -> AgentTurn:
        seen = len(self._journal)
        turn = await self._inner.respond(thread_id, message, context)
        if turn.stop_reason != "answer":
            return turn
        redacted = await self._redact(message) if self._redact else message
        evidence = tuple(
            Evidence(e.call.name, e.call.arguments, e.output, e.error) for e in self._journal[seen:]
        )
        self._evaluator.offer(
            AgentEvalSample(
                agent=self._name,
                judged=AgentJudgeSample((redacted,), turn.answer, evidence),
                trace=get_telemetry().inject(),
            ),
            stop_reason=turn.stop_reason,
        )
        return turn
