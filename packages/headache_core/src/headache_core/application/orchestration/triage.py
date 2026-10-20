"""Agente de Triaje: email → `CaseEnvelope` para el especialista.

El LLM extrae y clasifica (`SupportTriageService`); el dominio pone la ruta, la identidad y
valida lo extraído. El triaje no responde al cliente ni llama a tools.
"""

from __future__ import annotations

from dataclasses import dataclass

from headache_core.application.support_triage import SupportTriageService
from headache_core.domain.models import SupportEmail
from headache_core.domain.orchestration import (
    CaseEnvelope,
    resolve_language,
    resolve_order_id,
    route,
)
from headache_core.ports import LLMPort, LLMResponse


def case_id_for(email: SupportEmail) -> str:
    """Caso determinista por email: un reintento o un mensaje duplicado cae en el mismo caso."""
    return f"case_{email.id}"


@dataclass(frozen=True, slots=True)
class TriageResult:
    envelope: CaseEnvelope
    # Todas las llamadas (incluidas las de corrección del esquema): coste real del triaje.
    responses: tuple[LLMResponse, ...]


class TriageAgent:
    def __init__(self, llm: LLMPort) -> None:
        self._service = SupportTriageService(llm)

    async def triage(self, email: SupportEmail, *, case_id: str | None = None) -> TriageResult:
        result = await self._service.triage(email)
        triage = result.value
        envelope = CaseEnvelope(
            case_id=case_id or case_id_for(email),
            email_id=email.id,
            customer_id=email.customer_id,
            specialist=route(triage.intent),
            intent=triage.intent,
            language=resolve_language(triage.language, email.language),
            urgency=triage.urgency,
            summary=triage.summary,
            order_id=resolve_order_id(email.order_id, triage.order_id),
        )
        return TriageResult(envelope, tuple(result.responses))
