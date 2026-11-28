"""El mismo flujo con un workflow de grafo de Google ADK (fase 9.G1): cuarto motor.

    START → triage ─route─┬─▶ refunds ───┐
                          ├─▶ creative ──┼─▶ review ─(pendiente)─▶ RequestInput ─▶ decisión
                          ├─▶ knowledge ─┘        └─(resto)──────▶ salida
                          └─▶ (DEFAULT_ROUTE) unroutable

Por qué un `Workflow` de grafo y no un `LlmAgent` con `sub_agents`: con `sub_agents` es el LLM
quien decide el traspaso (*agent transfer*). Aquí el triaje clasifica y la ruta la fija el
DOMINIO (`ctx.route = envelope.specialist`), igual que en LangGraph y MAF.

Diferencias con LangGraph y MAF que conviene saber explicar:
- Rutas como valores: cada nodo marca `ctx.route` y el mapa de la arista elige el destino
  (`{"refunds": nodo, ...}`); `DEFAULT_ROUTE` es la red de seguridad, como el `Default` de MAF.
- Human-in-the-loop con `RequestInput`: el nodo cede una petición (una *function call*
  `adk_request_input` de larga duración) y la invocación termina. Se reanuda con una
  `FunctionResponse` con el mismo id y la misma `invocation_id`, desde cualquier réplica.
- El estado es la **sesión** de ADK (`session_id = case_id`): eventos + `state`. Memoria en los
  tests, `DatabaseSessionService` (PostgreSQL) o Sessions de Agent Platform en la nube. Frente a
  LangGraph (checkpoint por `thread_id`) y MAF (checkpoint por *superstep*).
- Los nodos completados se memorizan: al reanudar no se repite el triaje. Un nodo que falló se
  reintenta reanudando la misma invocación (sin mensaje nuevo).
- `RetryConfig` por nodo con lista de excepciones (por nombre de clase en la MRO), como la
  `RetryPolicy` de LangGraph; MAF no la tiene. El contador de reintentos no se persiste.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from typing import Any

from google.adk import Context, Runner, Workflow
from google.adk.events import RequestInput
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session
from google.adk.workflow import DEFAULT_ROUTE, FunctionNode, RetryConfig
from google.genai import types

from genaipocs_core.application.orchestration import (
    SpecialistAgent,
    TriageAgent,
    case_id_for,
    email_from_payload,
    email_to_payload,
    envelope_from_payload,
    envelope_to_payload,
    outcome_from_payload,
    outcome_to_payload,
)
from genaipocs_core.domain import CaseOutcome, CaseStatus, Specialist, SupportEmail
from genaipocs_core.ports import LLMTransientError
from harness.approvals import ApprovalService
from orchestrator.graph import CaseRun
from orchestrator.review import ReviewError, apply_review, parse_decision

__all__ = ["APP_NAME", "AdkCaseWorkflow", "ReviewError", "review_answer"]

APP_NAME = "genaipocs_orchestrator"
# Los casos no son de un usuario final: la sesión es del orquestador y su id, el del caso.
USER_ID = "orchestrator"
UNROUTABLE = "unroutable"
REQUEST_INPUT = "adk_request_input"
# Solo errores transitorios del modelo: reintentar un error de lógica no lo arregla.
SPECIALIST_RETRY = RetryConfig(max_attempts=3, exceptions=[LLMTransientError])

# Claves del `state` de la sesión (JSON plano: se guarda igual en memoria o en PostgreSQL).
EMAIL, ENVELOPE, OUTCOME, TRIAGE_CALLS = "email", "envelope", "outcome", "triage_calls"
# {"interrupt_id", "invocation_id"} mientras el caso espera a una persona; None después.
PENDING_REVIEW, REVIEW = "pending_review", "review"


def review_answer(interrupt_id: str, decision: Mapping[str, Any]) -> types.Content:
    """La respuesta a un `RequestInput`: una `FunctionResponse` con el id de la interrupción."""
    response = types.FunctionResponse(id=interrupt_id, name=REQUEST_INPUT, response=dict(decision))
    return types.Content(role="user", parts=[types.Part(function_response=response)])


class AdkCaseWorkflow:
    def __init__(
        self,
        triage: TriageAgent,
        specialists: Mapping[Specialist, SpecialistAgent],
        approvals: ApprovalService,
        *,
        session_service: BaseSessionService | None = None,
        retry_config: RetryConfig = SPECIALIST_RETRY,
    ) -> None:
        missing = set(Specialist) - set(specialists)
        if missing:
            raise ValueError(f"Faltan especialistas: {sorted(missing)}.")
        self._triage = triage
        self._specialists = specialists
        self._approvals = approvals
        self._retry = retry_config
        self.sessions = session_service or InMemorySessionService()
        self.workflow = self._build()
        self.runner = Runner(app_name=APP_NAME, node=self.workflow, session_service=self.sessions)

    def _build(self) -> Workflow:
        triage = FunctionNode(func=self._triage_node, name="triage", retry_config=self._retry)
        review = FunctionNode(func=self._review_node, name="review", rerun_on_resume=True)
        routes: dict[str | int | bool, Any] = {
            DEFAULT_ROUTE: FunctionNode(func=_unroutable, name=UNROUTABLE)
        }
        edges: list[Any] = []
        for specialist, agent in self._specialists.items():
            node = FunctionNode(
                func=_specialist_node(agent), name=specialist.value, retry_config=self._retry
            )
            routes[specialist.value] = node
            edges.append((node, review))
        return Workflow(name="genaipocs_case_router_adk", edges=[("START", triage, routes), *edges])

    # --- Nodos --------------------------------------------------------------------------------

    async def _triage_node(self, ctx: Context) -> dict[str, Any]:
        result = await self._triage.triage(email_from_payload(ctx.state[EMAIL]))
        envelope = envelope_to_payload(result.envelope)
        ctx.state[ENVELOPE] = envelope
        ctx.state[TRIAGE_CALLS] = len(result.responses)
        # La ruta la fija el dominio (`ROUTES`), no el modelo.
        ctx.route = result.envelope.specialist.value
        return envelope

    async def _review_node(
        self, ctx: Context, node_input: dict[str, Any]
    ) -> AsyncGenerator[Any, None]:
        outcome = outcome_from_payload(node_input)
        if outcome.status is not CaseStatus.PENDING_APPROVAL:
            yield node_input
            return
        assert outcome.approval_id is not None
        # Con `rerun_on_resume` el nodo se repite desde arriba al reanudar: nada con efectos
        # antes de tener una decisión válida. `resume_inputs` acumula todas las respuestas.
        answers = list((ctx.resume_inputs or {}).values())
        error = ""
        if answers:
            try:
                decision = parse_decision(answers[-1], ctx.state[ENVELOPE]["customer_id"])
            except ReviewError as exc:
                # Igual que en LangGraph y MAF: una decisión inválida vuelve a pausar el caso.
                error = str(exc)
            else:
                reviewed = await apply_review(self._approvals, outcome, decision)
                payload = outcome_to_payload(reviewed)
                ctx.state[OUTCOME] = payload
                ctx.state[PENDING_REVIEW] = None
                ctx.state[REVIEW] = {"reviewer": decision.reviewer, "approve": decision.approve}
                yield payload
                return
        interrupt_id = f"review-{len(answers) + 1}"
        ctx.state[PENDING_REVIEW] = {
            "interrupt_id": interrupt_id,
            "invocation_id": ctx.invocation_id,
        }
        pause = {
            "case_id": outcome.case_id,
            "approval_id": outcome.approval_id,
            "specialist": outcome.specialist.value,
            "draft": outcome.reply,
            "error": error,
        }
        yield RequestInput(
            interrupt_id=interrupt_id, message="Revisión humana del caso.", payload=pause
        )

    # --- API del orquestador -----------------------------------------------------------------

    async def start(self, email: SupportEmail) -> CaseRun:
        """Idempotente por email: un duplicado devuelve el estado del caso, no lo reprocesa."""
        case_id = case_id_for(email)
        session = await self._session(case_id)
        if session is None:
            await self.sessions.create_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=case_id,
                state={EMAIL: email_to_payload(email)},
            )
            message = types.Content(role="user", parts=[types.Part(text=case_id)])
            await self._run(case_id, message)
        elif not _finished(session) and session.events:
            # Un fallo anterior lo dejó a medias: se reanuda la misma invocación, sin mensaje.
            # Los nodos completados (el triaje) no se repiten.
            await self._run(case_id, None, session.events[-1].invocation_id)
        run = await self.get(case_id)
        assert run is not None
        return run

    async def review(
        self, case_id: str, *, reviewer: str, approve: bool, note: str = ""
    ) -> CaseRun:
        session = await self._session(case_id)
        pending = session.state.get(PENDING_REVIEW) if session else None
        if session is None or not pending:
            raise ReviewError(f"El caso {case_id} no espera revisión.")
        resume = {"reviewer": reviewer, "approve": approve, "note": note}
        # Error inmediato para quien llama; el nodo vuelve a validar (defensa en profundidad).
        parse_decision(resume, session.state[ENVELOPE]["customer_id"])
        answer = review_answer(pending["interrupt_id"], resume)
        await self._run(case_id, answer, pending["invocation_id"])
        run = await self.get(case_id)
        assert run is not None
        return run

    async def get(self, case_id: str) -> CaseRun | None:
        session = await self._session(case_id)
        if session is None:
            return None
        state = session.state
        outcome = state.get(OUTCOME)
        return {
            "case_id": case_id,
            "outcome": outcome_from_payload(outcome) if outcome else None,
            "awaiting_review": bool(state.get(PENDING_REVIEW)),
            "triage_calls": int(state.get(TRIAGE_CALLS, 0)),
        }

    async def _session(self, case_id: str) -> Session | None:
        return await self.sessions.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=case_id
        )

    async def _run(
        self, case_id: str, message: types.Content | None, invocation_id: str | None = None
    ) -> None:
        # Un error no reintentable sale de aquí (como en LangGraph): el caso queda a medias en la
        # sesión y `start` lo reanuda.
        async for _event in self.runner.run_async(
            user_id=USER_ID, session_id=case_id, new_message=message, invocation_id=invocation_id
        ):
            pass


def _specialist_node(agent: SpecialistAgent) -> Any:
    async def handle(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        # Reintentar es seguro: las aprobaciones son idempotentes (misma acción = misma solicitud).
        outcome = await agent.handle(envelope_from_payload(node_input))
        payload = outcome_to_payload(outcome)
        ctx.state[OUTCOME] = payload
        return payload

    return handle


async def _unroutable(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    """Red de seguridad: `ROUTES` es total, pero un sobre sin especialista va a una persona."""
    envelope = envelope_from_payload(node_input)
    outcome = CaseOutcome(
        envelope.case_id,
        envelope.specialist,
        CaseStatus.ESCALATED,
        "Caso sin especialista: lo atiende una persona.",
        (UNROUTABLE,),
    )
    payload = outcome_to_payload(outcome)
    ctx.state[OUTCOME] = payload
    return payload


def _finished(session: Session) -> bool:
    """Terminado o en pausa: no hay nada que reanudar sin una decisión."""
    if session.state.get(PENDING_REVIEW):
        return True
    outcome = session.state.get(OUTCOME)
    return outcome is not None and outcome["status"] != CaseStatus.PENDING_APPROVAL
