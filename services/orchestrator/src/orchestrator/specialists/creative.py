"""Especialista Creativo: un *workflow*, no un agente.

Quejas y propuestas de partners siguen un camino fijo (redactar → validar → aprobar), así que no
necesita un bucle con tools: una llamada estructurada con autocorrección (`draft_case_reply`) y
la solicitud de aprobación. Más barato, más predecible y más fácil de evaluar. El mismo contrato
`SpecialistAgent` oculta al orquestador si detrás hay un agente o un workflow.
"""

from __future__ import annotations

from harness.approvals import APPROVAL_REQUESTED, ApprovalService
from harness.rules.case_reply import ReplyCase, draft_case_reply, submit_case_reply
from harness.rules.partner_email import Language
from harness.security import prompt_guard
from harness.validation import Validator, run_validators
from headache_core.application.orchestration import (
    InvalidHandoffError,
    render_handoff,
    tool_context,
)
from headache_core.application.tools import ToolContext
from headache_core.domain import (
    CaseEnvelope,
    CaseOutcome,
    CaseStatus,
    Specialist,
    SupportIntent,
)
from headache_core.ports import LLMPort, TicketingError, TicketingPort
from headache_core.ports.guardrails import ContentShieldPort

ESCALATION_NOTE = "Borrador no válido tras los reintentos: lo redacta una persona."


class CreativeSpecialist:
    specialist = Specialist.CREATIVE

    def __init__(
        self,
        llm: LLMPort,
        approvals: ApprovalService,
        shield: ContentShieldPort,
        *,
        ticketing: TicketingPort | None = None,
        max_attempts: int = 3,
        fail_open: bool = False,
    ) -> None:
        self._llm = llm
        self._approvals = approvals
        self._ticketing = ticketing
        self._max_attempts = max_attempts
        self._input_guards: tuple[Validator[str, ToolContext], ...] = (
            prompt_guard(shield, fail_open=fail_open),
        )

    async def handle(self, envelope: CaseEnvelope) -> CaseOutcome:
        if envelope.specialist is not self.specialist:
            raise InvalidHandoffError(f"Caso para {envelope.specialist}, no para el Creativo.")
        brief = render_handoff(envelope)
        context = tool_context(envelope)
        # El resumen del triaje deriva de un email de terceros: misma guarda de entrada que los
        # especialistas con tools (GuardedRuntime).
        violations = await run_validators(self._input_guards, brief, context)
        if violations:
            return self._escalate(envelope, tuple(v.code for v in violations), "Entrada bloqueada.")
        case = ReplyCase(
            reference=envelope.case_id,
            audience="partner" if envelope.intent is SupportIntent.PARTNERSHIP else "customer",
            language=_language(envelope.language),
            brief=brief,
            facts=await self._facts(envelope),
            allowed_ids=frozenset({envelope.order_id} if envelope.order_id else ()),
        )
        result = await draft_case_reply(self._llm, case, max_attempts=self._max_attempts)
        if result.email is None:
            codes = tuple(dict.fromkeys(v.code for v in result.correction.violations))
            return self._escalate(envelope, codes, ESCALATION_NOTE)
        request = await submit_case_reply(self._approvals, result, case, context=context)
        return CaseOutcome(
            case_id=envelope.case_id,
            specialist=self.specialist,
            status=CaseStatus.PENDING_APPROVAL,
            reply=result.email.body,
            guardrails=(APPROVAL_REQUESTED,),
            approval_id=request.id,
        )

    async def _facts(self, envelope: CaseEnvelope) -> dict[str, str]:
        if self._ticketing is None or envelope.order_id is None:
            return {}
        try:
            order = await self._ticketing.get_order(envelope.order_id)
        except TicketingError:
            return {}
        # Un pedido de otro cliente no es un hecho de este caso.
        if order.customer_id != envelope.customer_id:
            return {}
        return {
            "pedido": order.id,
            "entradas": str(order.quantity),
            "importe": f"{order.total.amount:.2f} {order.total.currency}",
            "estado": order.status.value,
        }

    def _escalate(self, envelope: CaseEnvelope, codes: tuple[str, ...], note: str) -> CaseOutcome:
        return CaseOutcome(
            case_id=envelope.case_id,
            specialist=self.specialist,
            status=CaseStatus.ESCALATED,
            reply=note,
            guardrails=codes,
        )


def _language(language: str) -> Language:
    return "es" if language == "es" else "en"
