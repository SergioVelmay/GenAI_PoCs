"""Triaje de emails de soporte con structured outputs.

El LLM solo *extrae y clasifica*. Qué se hace después (reembolsar o no) lo decide el dominio.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.llm import StructuredResult, complete_structured
from headache_core.domain import SupportEmail, SupportIntent
from headache_core.ports import ChatMessage, LLMPort, Role


class TicketTriage(BaseModel):
    """Clasificación de un email de soporte de Headache."""

    model_config = ConfigDict(extra="forbid")

    intent: SupportIntent
    language: str = Field(description="Código ISO 639-1 del idioma del email")
    order_id: str | None = Field(description="Identificador ord_XXXXXXXX si aparece; si no, null")
    urgency: Literal["low", "normal", "high"]
    summary: str = Field(description="Resumen de una frase, en español")


TRIAGE_SYSTEM_PROMPT = """Eres el agente de triaje de soporte de Headache, un marketplace de \
experiencias y eventos. Clasifica el email del cliente:
- refund_request: pide que le devuelvan el dinero.
- date_change: quiere cambiar de sesión o de fecha.
- event_info: pregunta por un evento (horario, accesibilidad, edad mínima...).
- lost_tickets: no encuentra o no le han llegado las entradas.
- complaint: queja por la experiencia vivida.
- partnership: propuesta comercial de un organizador o recinto.
urgency=high si el evento es en menos de 48 h o el cliente está muy molesto."""


class SupportTriageService:
    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    async def triage(self, email: SupportEmail) -> StructuredResult[TicketTriage]:
        return await complete_structured(
            self._llm,
            [
                ChatMessage(Role.SYSTEM, TRIAGE_SYSTEM_PROMPT),
                ChatMessage(Role.USER, f"Asunto: {email.subject}\n\n{email.body}"),
            ],
            TicketTriage,
            max_tokens=300,
        )
