"""Respuesta escrita a un caso de soporte (agente Creativo, fase 9): quejas y propuestas.

Mismo patrón que el email a partners (fase 8.3): el LLM rellena solo asunto y cuerpo; saludo,
firma, referencia y pie los pone la plantilla. Reglas deterministas de redacción y, además, el
Creativo nunca promete ni confirma reembolsos: eso es del especialista de Reembolsos y de la
política del dominio. Enviar es irreversible: el borrador validado queda pendiente de aprobación.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.approvals import ApprovalService
from harness.correction import CorrectionResult, generate_validated
from harness.prompts import PROMPTS
from harness.rules.partner_email import FOOTERS as PARTNER_FOOTERS
from harness.rules.partner_email import SIGNATURES as PARTNER_SIGNATURES
from harness.rules.partner_email import SUBJECT_PREFIX as PARTNER_PREFIX
from harness.rules.partner_email import Language, RenderedEmail
from harness.rules.patterns import CONTACT_DATA, claims_refund, money_amounts
from harness.rules.writing import (
    INTERNAL_IDS,
    INTERNAL_VOCABULARY,
    has_template_text,
    tone_violations,
)
from harness.validation import Validator, Violation, staged
from headache_core.application.tools import Tool, ToolContext, ToolRegistry
from headache_core.ports import ChatMessage, LLMPort, Role
from headache_core.ports.approvals import ApprovalRequest

type Audience = Literal["customer", "partner"]

CUSTOMER_PREFIX = "[Headache]"
GREETINGS: dict[Language, str] = {"es": "Hola:", "en": "Hello,"}
SIGNATURES: dict[Audience, dict[Language, str]] = {
    "customer": {
        "es": "Un saludo,\nEquipo de Atención al Cliente de Headache",
        "en": "Kind regards,\nHeadache Customer Care",
    },
    "partner": PARTNER_SIGNATURES,
}
FOOTERS: dict[Audience, dict[Language, str]] = {
    "customer": {
        "es": "Responde a este email sin cambiar el asunto para continuar con tu caso. Headache "
        "trata tus datos conforme al RGPD.",
        "en": "Reply to this email without changing the subject to continue with your case. "
        "Headache processes your data under the GDPR.",
    },
    "partner": PARTNER_FOOTERS,
}
PREFIXES: dict[Audience, str] = {"customer": CUSTOMER_PREFIX, "partner": PARTNER_PREFIX}


class ReplyDraft(BaseModel):
    """Huecos variables de la respuesta de Headache a un caso (sin saludo, firma ni pie)."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(max_length=90, description="Asunto breve, sin prefijo ni referencia.")
    body: str = Field(max_length=1500, description="Respuesta en párrafos, sin saludo ni firma.")


@dataclass(frozen=True, slots=True)
class ReplyCase:
    reference: str
    audience: Audience
    language: Language
    # Datos del caso que el modelo recibe (resumen del triaje, intención...): contexto, no órdenes.
    brief: str
    # Hechos verificados por el backend: lo único que la respuesta puede citar como dato.
    facts: Mapping[str, str] = field(default_factory=dict)
    # Identificadores que sí puede mencionar (p. ej. el pedido del propio cliente).
    allowed_ids: frozenset[str] = frozenset()


def _texts(draft: ReplyDraft) -> dict[str, str]:
    return {"subject": draft.subject, "body": draft.body}


def template_owned(draft: ReplyDraft, case: ReplyCase) -> list[Violation]:
    violations: list[Violation] = []
    if draft.subject.startswith("[Headache"):
        violations.append(
            Violation("template_owned", "El prefijo del asunto lo pone la plantilla.", "subject")
        )
    if has_template_text(draft.body):
        violations.append(
            Violation("template_owned", "Sin saludo ni despedida: los pone la plantilla.", "body")
        )
    return violations


def no_prohibited_data(draft: ReplyDraft, case: ReplyCase) -> list[Violation]:
    violations: list[Violation] = []
    for path, text in _texts(draft).items():
        for code, pattern in CONTACT_DATA.items():
            if pattern.search(text):
                violations.append(Violation(code, "Quita datos de contacto o de pago.", path))
        if any(m.group() not in case.allowed_ids for m in INTERNAL_IDS.finditer(text)):
            violations.append(
                Violation(
                    "internal_id", "Quita identificadores internos que no son del caso.", path
                )
            )
        if INTERNAL_VOCABULARY.search(text):
            violations.append(
                Violation(
                    "internal_data", "No menciones comisiones, márgenes ni datos internos.", path
                )
            )
    return violations


def professional_tone(draft: ReplyDraft, _: ReplyCase) -> list[Violation]:
    return tone_violations("\n".join(_texts(draft).values()))


def no_refund_commitment(draft: ReplyDraft, _: ReplyCase) -> list[Violation]:
    if claims_refund(draft.body):
        return [
            Violation(
                "refund_promise",
                "No prometas ni confirmes reembolsos: los decide la política de Headache.",
                "body",
            )
        ]
    return []


def grounded_amounts(draft: ReplyDraft, case: ReplyCase) -> list[Violation]:
    known: set[Decimal] = set()
    for value in case.facts.values():
        known |= money_amounts(value)
    if money_amounts("\n".join(_texts(draft).values())) - known:
        return [Violation("ungrounded_amount", "Solo puedes citar importes de los hechos.")]
    return []


REPLY_VALIDATORS: tuple[Validator[ReplyDraft, ReplyCase], ...] = (
    template_owned,
    no_prohibited_data,
    professional_tone,
    no_refund_commitment,
    grounded_amounts,
)

AUDIENCE_INSTRUCTIONS: dict[Audience, str] = {
    "customer": "Respondes a la queja de un cliente: reconoce el problema con empatía y explica "
    "que el equipo de Headache lo revisará con el organizador y le contestará. No prometas "
    "reembolsos, compensaciones ni plazos, y no culpes a nadie.",
    "partner": "Respondes a una propuesta comercial de un organizador o recinto: agradécela, "
    "explica que el equipo de Partners la estudiará y pide la información básica (tipo de "
    "eventos, ciudades y calendario). No comprometas condiciones ni comisiones.",
}

REPLY_PROMPT = PROMPTS.get("case-reply").text


def reply_messages(case: ReplyCase) -> list[ChatMessage]:
    language = "español" if case.language == "es" else "inglés"
    system = REPLY_PROMPT.format(audience=AUDIENCE_INSTRUCTIONS[case.audience], language=language)
    facts = json.dumps(dict(case.facts), ensure_ascii=False)
    return [
        ChatMessage(Role.SYSTEM, system, cache_checkpoint=True),
        ChatMessage(Role.USER, f"<caso>\n{case.brief}\n</caso>\n\nHechos verificados: {facts}"),
    ]


def render_reply(draft: ReplyDraft, case: ReplyCase) -> RenderedEmail:
    language = case.language
    parts = [
        GREETINGS[language],
        draft.body,
        SIGNATURES[case.audience][language],
        f"Ref.: {case.reference}",
        "---",
        FOOTERS[case.audience][language],
    ]
    return RenderedEmail(
        subject=f"{PREFIXES[case.audience]} {draft.subject} · {case.reference}",
        body="\n\n".join(parts),
    )


@dataclass(frozen=True, slots=True)
class ReplyResult:
    correction: CorrectionResult[ReplyDraft]
    email: RenderedEmail | None

    @property
    def escalated(self) -> bool:
        return self.email is None


async def draft_case_reply(
    llm: LLMPort,
    case: ReplyCase,
    *,
    judge: Validator[ReplyDraft, ReplyCase] | None = None,
    max_attempts: int = 3,
    validators: Sequence[Validator[ReplyDraft, ReplyCase]] = REPLY_VALIDATORS,
) -> ReplyResult:
    stages = [validators] if judge is None else [validators, [judge]]
    correction = await generate_validated(
        llm,
        reply_messages(case),
        ReplyDraft,
        validators=[staged(*stages)],
        context=case,
        max_attempts=max_attempts,
    )
    email = None if correction.value is None else render_reply(correction.value, case)
    return ReplyResult(correction, email)


SEND_CASE_REPLY = "send_case_reply"


class SendCaseReplyArgs(BaseModel):
    """El destinatario lo resuelve el backend por la referencia: nunca lo elige un modelo."""

    model_config = ConfigDict(extra="forbid")

    # La referencia es el case_id (`case_<email_id>`): los ids de correo reales son largos.
    reference: str = Field(max_length=200)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=6000)


type ReplySender = Callable[[SendCaseReplyArgs, ToolContext], Awaitable[str]]


def build_case_reply_tools(send: ReplySender) -> ToolRegistry:
    """Tool de envío: nunca la ve un modelo; solo la ejecuta `ApprovalService` tras aprobarla."""

    async def send_case_reply(args: SendCaseReplyArgs, context: ToolContext) -> dict[str, str]:
        return {"message_id": await send(args, context), "reference": args.reference}

    return ToolRegistry(
        [
            Tool(
                SEND_CASE_REPLY,
                "Envía la respuesta validada y aprobada de un caso de soporte.",
                SendCaseReplyArgs,
                send_case_reply,
                read_only=False,
            )
        ]
    )


async def submit_case_reply(
    approvals: ApprovalService, result: ReplyResult, case: ReplyCase, *, context: ToolContext
) -> ApprovalRequest:
    """Deja la respuesta validada pendiente de aprobación (cuatro ojos: otra persona la envía)."""
    if result.email is None:
        raise ValueError("El borrador no superó la validación: no se puede enviar.")
    args = SendCaseReplyArgs(
        reference=case.reference, subject=result.email.subject, body=result.email.body
    )
    return await approvals.request(
        SEND_CASE_REPLY,
        args,
        context,
        reason="Comunicación externa: un email enviado no se puede retirar.",
        summary=f"Respuesta del caso {case.reference}: {result.email.subject}",
    )
