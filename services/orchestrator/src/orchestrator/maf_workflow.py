"""El mismo flujo con Microsoft Agent Framework (workflows), para comparar con LangGraph.

    triage ─switch-case─┬─▶ refunds ───┐
                        ├─▶ creative ──┼─▶ review ─(pendiente)─▶ request_info ─▶ decisión
                        ├─▶ knowledge ─┘        └─(resto)──────▶ output
                        └─▶ (default) unroutable

Diferencias con LangGraph que importan en una entrevista:
- Mensajes tipados en lugar de un estado compartido: cada `Executor` declara qué tipo recibe y
  qué tipo envía (`WorkflowContext[Out, WorkflowOut]`); el enrutado por tipo se valida al
  construir el grafo. LangGraph comparte un `TypedDict` y cada nodo devuelve actualizaciones.
- Aristas *switch-case* con `Case`/`Default` (aquí el `Default` es una red de seguridad: `ROUTES`
  es total) frente a `add_conditional_edges` con una función de enrutado.
- Human-in-the-loop con `request_info` + `@response_handler` (petición y respuesta tipadas) frente
  a `interrupt()` + `Command(resume=...)`.
- Checkpoints por *superstep* en un `CheckpointStorage` (memoria, fichero; Cosmos DB en Azure)
  frente a un checkpointer por `thread_id`. MAF no trae `RetryPolicy` por nodo: los reintentos
  quedan en el gateway (fase 2) o en el ejecutor de tools.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_framework import (
    Case,
    CheckpointStorage,
    Default,
    Executor,
    FileCheckpointStorage,
    InMemoryCheckpointStorage,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowRunResult,
    handler,
    response_handler,
)

from harness.approvals import ApprovalService
from headache_core.application.orchestration import SpecialistAgent, TriageAgent, case_id_for
from headache_core.domain import (
    CaseEnvelope,
    CaseOutcome,
    CaseStatus,
    Specialist,
    SupportEmail,
    SupportIntent,
)
from orchestrator.review import ReviewDecision, ReviewError, apply_review, parse_decision

UNROUTABLE = "unroutable"


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Lo que ve la persona que revisa (petición tipada de `request_info`)."""

    outcome: CaseOutcome
    customer_id: str
    error: str = ""


class TriageExecutor(Executor):
    def __init__(self, triage: TriageAgent) -> None:
        super().__init__(id="triage")
        self._triage = triage

    @handler
    async def triage(self, email: SupportEmail, ctx: WorkflowContext[CaseEnvelope]) -> None:
        result = await self._triage.triage(email)
        await ctx.send_message(result.envelope)


class SpecialistExecutor(Executor):
    def __init__(self, agent: SpecialistAgent) -> None:
        super().__init__(id=agent.specialist.value)
        self._agent = agent

    @handler
    async def handle(
        self, envelope: CaseEnvelope, ctx: WorkflowContext[tuple[CaseOutcome, str]]
    ) -> None:
        outcome = await self._agent.handle(envelope)
        # La revisión necesita saber quién es el cliente (separación de funciones).
        await ctx.send_message((outcome, envelope.customer_id))


class UnroutableExecutor(Executor):
    """Red de seguridad del switch-case: un sobre sin especialista va a una persona."""

    def __init__(self) -> None:
        super().__init__(id=UNROUTABLE)

    @handler
    async def escalate(
        self, envelope: CaseEnvelope, ctx: WorkflowContext[None, CaseOutcome]
    ) -> None:
        await ctx.yield_output(
            CaseOutcome(
                envelope.case_id,
                envelope.specialist,
                CaseStatus.ESCALATED,
                "Caso sin especialista: lo atiende una persona.",
                (UNROUTABLE,),
            )
        )


class ReviewExecutor(Executor):
    def __init__(self, approvals: ApprovalService) -> None:
        super().__init__(id="review")
        self._approvals = approvals

    @handler
    async def review(
        self, result: tuple[CaseOutcome, str], ctx: WorkflowContext[None, CaseOutcome]
    ) -> None:
        outcome, customer_id = result
        if outcome.status is CaseStatus.PENDING_APPROVAL:
            await ctx.request_info(ReviewRequest(outcome, customer_id), ReviewDecision)
            return
        await ctx.yield_output(outcome)

    @response_handler
    async def decided(
        self,
        request: ReviewRequest,
        decision: ReviewDecision,
        ctx: WorkflowContext[None, CaseOutcome],
    ) -> None:
        try:
            valid = parse_decision(decision, request.customer_id)
        except ReviewError as exc:
            # Igual que en LangGraph: una decisión inválida vuelve a pausar el caso.
            retry = ReviewRequest(request.outcome, request.customer_id, str(exc))
            await ctx.request_info(retry, ReviewDecision)
            return
        await ctx.yield_output(await apply_review(self._approvals, request.outcome, valid))


# Los checkpoints de MAF en disco van en pickle y solo se deserializan tipos permitidos:
# sin esta lista, un fichero manipulado podría ejecutar código al restaurar.
CHECKPOINT_TYPES = [
    f"{cls.__module__}:{cls.__qualname__}"
    for cls in (
        SupportEmail,
        CaseEnvelope,
        CaseOutcome,
        ReviewRequest,
        ReviewDecision,
        Specialist,
        SupportIntent,
        CaseStatus,
    )
]


def file_checkpoint_storage(path: str | Path) -> FileCheckpointStorage:
    return FileCheckpointStorage(path, allowed_checkpoint_types=CHECKPOINT_TYPES)


class CheckpointNotSavedError(RuntimeError):
    """MAF solo registra un aviso si no puede guardar un checkpoint: aquí se falla en alto."""


@dataclass(frozen=True, slots=True)
class MafRun:
    case_id: str
    outcome: CaseOutcome | None
    # Petición pendiente de revisión y checkpoint desde el que reanudar (otra réplica incluida).
    request_id: str | None
    checkpoint_id: str | None
    error: str = ""

    @property
    def awaiting_review(self) -> bool:
        return self.request_id is not None


class MafCaseWorkflow:
    def __init__(
        self,
        triage: TriageAgent,
        specialists: Mapping[Specialist, SpecialistAgent],
        approvals: ApprovalService,
        *,
        checkpoint_storage: CheckpointStorage | None = None,
    ) -> None:
        missing = set(Specialist) - set(specialists)
        if missing:
            raise ValueError(f"Faltan especialistas: {sorted(missing)}.")
        self._triage = triage
        self._specialists = specialists
        self._approvals = approvals
        self._storage = checkpoint_storage or InMemoryCheckpointStorage()

    def build(self) -> Workflow:
        """Un `Workflow` por ejecución: guarda el estado de la ejecución en curso."""
        triage = TriageExecutor(self._triage)
        review = ReviewExecutor(self._approvals)
        executors = {s: SpecialistExecutor(agent) for s, agent in self._specialists.items()}
        cases: list[Case | Default] = [
            Case(_is_for(specialist), executor) for specialist, executor in executors.items()
        ]
        cases.append(Default(UnroutableExecutor()))
        builder = WorkflowBuilder(
            name="headache_case_router_maf",
            start_executor=triage,
            checkpoint_storage=self._storage,
        ).add_switch_case_edge_group(triage, cases)
        for executor in executors.values():
            builder.add_edge(executor, review)
        return builder.build()

    async def start(self, email: SupportEmail) -> MafRun:
        workflow = self.build()
        result = await workflow.run(email)
        return _run(workflow, result, case_id_for(email))

    async def review(self, run: MafRun, *, reviewer: str, approve: bool, note: str = "") -> MafRun:
        if run.request_id is None or run.checkpoint_id is None:
            raise ReviewError(f"El caso {run.case_id} no espera revisión.")
        # Workflow nuevo restaurado desde el checkpoint: no hace falta la instancia original.
        workflow = self.build()
        await workflow.run(checkpoint_id=run.checkpoint_id, checkpoint_storage=self._storage)
        decision = ReviewDecision(reviewer, approve, note)
        result = await workflow.run(responses={run.request_id: decision})
        return _run(workflow, result, run.case_id)


def _is_for(specialist: Specialist) -> Callable[[object], bool]:
    def condition(envelope: object) -> bool:
        return isinstance(envelope, CaseEnvelope) and envelope.specialist is specialist

    return condition


def _run(workflow: Workflow, result: WorkflowRunResult, case_id: str) -> MafRun:
    outputs = [o for o in result.get_outputs() if isinstance(o, CaseOutcome)]
    requests = result.get_request_info_events()
    if requests:
        request = requests[-1]
        pending = request.data
        assert isinstance(pending, ReviewRequest)
        checkpoint = workflow.get_last_checkpoint_id()
        if checkpoint is None:
            raise CheckpointNotSavedError(f"El caso {case_id} está en pausa sin checkpoint.")
        return MafRun(case_id, pending.outcome, request.request_id, checkpoint, pending.error)
    return MafRun(case_id, outputs[-1] if outputs else None, None, None)
