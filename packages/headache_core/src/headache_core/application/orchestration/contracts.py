"""Contratos entre agentes: el especialista, el mensaje de traspaso y su serialización.

El mismo `CaseEnvelope` viaja en memoria (grafo de LangGraph o MAF), por cola (asíncrono) o por
A2A (microservicios): por eso se serializa a un diccionario plano y se valida al recibirlo.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, get_args

from headache_core.application.tools.registry import ToolContext
from headache_core.domain.models import SupportEmail, SupportIntent
from headache_core.domain.orchestration import (
    CaseEnvelope,
    CaseOutcome,
    CaseStatus,
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


def outcome_to_payload(outcome: CaseOutcome) -> dict[str, Any]:
    return {
        "case_id": outcome.case_id,
        "specialist": outcome.specialist.value,
        "status": outcome.status.value,
        "reply": outcome.reply,
        "guardrails": list(outcome.guardrails),
        "approval_id": outcome.approval_id,
    }


def outcome_from_payload(payload: Mapping[str, Any]) -> CaseOutcome:
    try:
        return CaseOutcome(
            case_id=str(payload["case_id"]),
            specialist=Specialist(str(payload["specialist"])),
            status=CaseStatus(str(payload["status"])),
            reply=str(payload["reply"]),
            guardrails=tuple(str(code) for code in payload.get("guardrails", ())),
            approval_id=payload.get("approval_id"),
        )
    except (KeyError, ValueError) as exc:
        raise InvalidHandoffError(f"Resultado mal formado: {exc}") from None


def email_to_payload(email: SupportEmail) -> dict[str, str | None]:
    return {
        "id": email.id,
        "customer_id": email.customer_id,
        "language": email.language,
        "subject": email.subject,
        "body": email.body,
        "received_at": email.received_at.isoformat(),
        "order_id": email.order_id,
    }


def email_from_payload(payload: Mapping[str, Any]) -> SupportEmail:
    missing = [k for k in ("id", "customer_id", "language", "body") if not _is_text(payload.get(k))]
    if missing:
        raise InvalidHandoffError(f"Faltan campos del email: {', '.join(missing)}.")
    try:
        received_at = datetime.fromisoformat(str(payload["received_at"]))
    except (KeyError, ValueError):
        raise InvalidHandoffError("received_at debe ser una fecha ISO 8601.") from None
    order_id = payload.get("order_id")
    return SupportEmail(
        id=payload["id"],
        customer_id=payload["customer_id"],
        language=payload["language"],
        subject=str(payload.get("subject") or ""),
        body=payload["body"],
        received_at=received_at,
        order_id=order_id if isinstance(order_id, str) and order_id else None,
    )


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
