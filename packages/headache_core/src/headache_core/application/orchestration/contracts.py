"""Contratos entre agentes: el especialista, el mensaje de traspaso y su serialización.

El mismo `CaseEnvelope` viaja en memoria (grafo de LangGraph o MAF), por cola (asíncrono) o por
A2A (microservicios): por eso se serializa a un diccionario plano y se valida al recibirlo.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, get_args

from headache_core.application.tools.registry import ToolContext
from headache_core.domain.models import SupportIntent
from headache_core.domain.orchestration import (
    CaseEnvelope,
    CaseOutcome,
    Specialist,
    Urgency,
    route,
)

_URGENCIES: frozenset[str] = frozenset(get_args(Urgency.__value__))
_REQUIRED = ("case_id", "email_id", "customer_id", "language", "summary")


class InvalidHandoffError(ValueError):
    """Traspaso mal formado: no se reintenta (en una cola va directo a la DLQ)."""


class SpecialistAgent(Protocol):
    """Lo que el orquestador ve de un especialista, se ejecute en proceso, por cola o por A2A."""

    @property
    def specialist(self) -> Specialist: ...

    async def handle(self, envelope: CaseEnvelope) -> CaseOutcome: ...


def tool_context(envelope: CaseEnvelope) -> ToolContext:
    """La identidad viaja en el contexto de las tools, no en el prompt."""
    return ToolContext(customer_id=envelope.customer_id, request_id=envelope.case_id)


def render_handoff(envelope: CaseEnvelope) -> str:
    """Mensaje que recibe el especialista: hechos del triaje, sin el email ni el customer_id."""
    lines = [
        f"Caso: {envelope.case_id}",
        f"Intención: {envelope.intent.value}",
        f"Idioma del cliente: {envelope.language}",
        f"Urgencia: {envelope.urgency}",
        f"Pedido: {envelope.order_id or 'no indicado'}",
        f"Resumen del triaje: {envelope.summary}",
    ]
    return "\n".join(lines)


def envelope_to_payload(envelope: CaseEnvelope) -> dict[str, str | None]:
    return {
        "case_id": envelope.case_id,
        "email_id": envelope.email_id,
        "customer_id": envelope.customer_id,
        "specialist": envelope.specialist.value,
        "intent": envelope.intent.value,
        "language": envelope.language,
        "urgency": envelope.urgency,
        "summary": envelope.summary,
        "order_id": envelope.order_id,
    }


def envelope_from_payload(payload: Mapping[str, Any]) -> CaseEnvelope:
    missing = [key for key in _REQUIRED if not _is_text(payload.get(key))]
    if missing:
        raise InvalidHandoffError(f"Faltan campos del traspaso: {', '.join(missing)}.")
    urgency = payload.get("urgency")
    if urgency not in _URGENCIES:
        raise InvalidHandoffError(f"Urgencia no válida: {urgency!r}.")
    order_id = payload.get("order_id")
    if order_id is not None and not isinstance(order_id, str):
        raise InvalidHandoffError("order_id debe ser texto o null.")
    try:
        specialist = Specialist(str(payload.get("specialist")))
        intent = SupportIntent(str(payload.get("intent")))
    except ValueError as exc:
        raise InvalidHandoffError(str(exc)) from None
    # La ruta la fija el dominio: un mensaje que la contradiga está manipulado o desfasado.
    if route(intent) is not specialist:
        raise InvalidHandoffError(f"La intención {intent} no corresponde a {specialist}.")
    return CaseEnvelope(
        case_id=payload["case_id"],
        email_id=payload["email_id"],
        customer_id=payload["customer_id"],
        specialist=specialist,
        intent=intent,
        language=payload["language"],
        urgency=urgency,
        summary=payload["summary"],
        order_id=order_id,
    )


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
