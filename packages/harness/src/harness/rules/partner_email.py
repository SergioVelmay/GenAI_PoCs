"""Emails a partners (recintos y organizadores): el LLM redacta, la plantilla y las reglas mandan.

- Plantilla obligatoria POR CONSTRUCCIÓN: el modelo solo rellena huecos (asunto, resumen, cuerpo,
  acciones). Saludo, referencia, firma y pie legal los pone `render_partner_email`; si el modelo
  los escribe, se le pide que los quite.
- Datos prohibidos: datos de contacto o de pago, identificadores de clientes y pedidos, nombres
  de clientes u otros partners que aparezcan en las notas internas y vocabulario interno
  (comisiones, márgenes). Minimización del RGPD: el partner no necesita datos personales.
- Tono: reglas deterministas (exclamaciones, mayúsculas, emojis, culpas, promesas) y, solo si
  pasan, un juez LLM opcional con rúbrica. Los importes citados tienen que estar en los hechos.

Enviar es irreversible: `submit_partner_email` deja el email validado pendiente de aprobación y
solo `ApprovalService` lo envía cuando otra persona lo aprueba (fase 8.5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.approvals import ApprovalService
from harness.correction import CorrectionResult, generate_validated
from harness.prompts import PROMPTS
from harness.rules.patterns import CONTACT_DATA, money_amounts
from harness.rules.writing import (
    INTERNAL_IDS,
    INTERNAL_VOCABULARY,
    fold,
    has_template_text,
    tone_violations,
)
from harness.validation import Validator, Violation, staged
from headache_core.application.llm import complete_structured
from headache_core.application.tools import Tool, ToolContext, ToolRegistry
from headache_core.ports import ChatMessage, LLMPort, Role
from headache_core.ports.approvals import ApprovalRequest

type Language = Literal["es", "en"]

SUBJECT_PREFIX = "[Headache Partners]"

GREETINGS: dict[Language, str] = {"es": "Hola, {name}:", "en": "Dear {name},"}
ACTIONS_TITLE: dict[Language, str] = {"es": "Acciones solicitadas:", "en": "Requested actions:"}
SIGNATURES: dict[Language, str] = {
    "es": "Un saludo,\nEquipo de Partners de Headache",
    "en": "Kind regards,\nHeadache Partner Team",
}
FOOTERS: dict[Language, str] = {
    "es": "Mensaje confidencial dirigido únicamente a su destinatario. Headache trata los datos "
    "conforme al RGPD y no comparte datos personales de sus clientes con partners.",
    "en": "Confidential message intended only for its recipient. Headache processes data under "
    "the GDPR and does not share its customers' personal data with partners.",
}


class PartnerEmailDraft(BaseModel):
    """Huecos variables de un email de Headache a un partner (sin saludo, firma ni pie)."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(max_length=90, description="Asunto breve, sin prefijo ni referencia.")
    summary: str = Field(max_length=300, description="Una o dos frases con lo esencial.")
    body: str = Field(max_length=1500, description="Detalle en párrafos, sin saludo ni firma.")
    requested_actions: list[str] = Field(
        max_length=3, description="Hasta 3 acciones concretas que se piden al partner."
    )


@dataclass(frozen=True, slots=True)
class PartnerEmailCase:
    reference: str
    partner_name: str
    contact_name: str
    language: Language
    # Hechos verificados que el email puede citar (evento, fecha, entradas, importes...).
    facts: Mapping[str, str]
    # Notas del equipo de operaciones: contexto para el modelo, nunca contenido para el partner.
    internal_notes: str = ""
    # Término -> categoría (customer_name, other_partner...): nunca deben aparecer en el email.
    forbidden_terms: Mapping[str, str] = field(default_factory=dict)
    # Lo que el email tiene que mencionar sí o sí (p. ej. el título del evento).
    must_mention: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    body: str


def _texts(draft: PartnerEmailDraft) -> dict[str, str]:
    return {
        "subject": draft.subject,
        "summary": draft.summary,
        "body": draft.body,
        **{f"requested_actions.{i}": a for i, a in enumerate(draft.requested_actions)},
    }


def no_prohibited_data(draft: PartnerEmailDraft, case: PartnerEmailCase) -> list[Violation]:
    violations: list[Violation] = []
    for path, text in _texts(draft).items():
        for code, pattern in CONTACT_DATA.items():
            if pattern.search(text):
                violations.append(Violation(code, "Quita datos de contacto o de pago.", path))
        if INTERNAL_IDS.search(text):
            violations.append(
                Violation("internal_id", "Quita identificadores de clientes o pedidos.", path)
            )
        if INTERNAL_VOCABULARY.search(text):
            violations.append(
                Violation(
                    "internal_data", "No menciones comisiones, márgenes ni datos internos.", path
                )
            )
        folded = fold(text)
        for term, category in case.forbidden_terms.items():
            if re.search(rf"\b{re.escape(fold(term))}\b", folded):
                violations.append(
                    Violation(
                        category,
                        f"Contiene un dato de las notas internas ({category}) que el partner no "
                        "debe ver: elimínalo.",
                        path,
                    )
                )
    return violations


def professional_tone(draft: PartnerEmailDraft, _: PartnerEmailCase) -> list[Violation]:
    return tone_violations("\n".join(_texts(draft).values()))


def template_sections(draft: PartnerEmailDraft, _: PartnerEmailCase) -> list[Violation]:
    """La plantilla pone saludo, prefijo, referencia y firma: el modelo no debe duplicarlos."""
    violations: list[Violation] = []
    if draft.subject.startswith(SUBJECT_PREFIX):
        violations.append(
            Violation("template_owned", "El prefijo del asunto lo pone la plantilla.", "subject")
        )
    for path in ("summary", "body"):
        text = getattr(draft, path)
        if has_template_text(text):
            violations.append(
                Violation("template_owned", "Sin saludo ni despedida: los pone la plantilla.", path)
            )
    return violations


def grounded_content(draft: PartnerEmailDraft, case: PartnerEmailCase) -> list[Violation]:
    joined = "\n".join(_texts(draft).values())
    violations: list[Violation] = []
    known: set[Decimal] = set()
    for value in case.facts.values():
        known |= money_amounts(value)
    if money_amounts(joined) - known:
        violations.append(
            Violation("ungrounded_amount", "Solo puedes citar importes que estén en los hechos.")
        )
    folded = fold(joined)
    for mention in case.must_mention:
        if fold(mention) not in folded:
            violations.append(
                Violation("missing_mention", f"El email debe mencionar «{mention}».", "body")
            )
    return violations


DETERMINISTIC_EMAIL_VALIDATORS: tuple[Validator[PartnerEmailDraft, PartnerEmailCase], ...] = (
    template_sections,
    no_prohibited_data,
    professional_tone,
    grounded_content,
)


class ToneVerdict(BaseModel):
    """Evaluación del tono de un email profesional de Headache a un partner."""

    model_config = ConfigDict(extra="forbid")

    professional: bool
    issues: list[str] = Field(
        max_length=3, description="Problemas concretos; vacío si es correcto."
    )


TONE_JUDGE_PROMPT = PROMPTS.get("partner-email-tone-judge").text


def tone_judge(llm: LLMPort) -> Validator[PartnerEmailDraft, PartnerEmailCase]:
    """Juez LLM: etiqueta problemas de tono; el código decide (si no es profesional, falla)."""

    async def validate(draft: PartnerEmailDraft, case: PartnerEmailCase) -> list[Violation]:
        language = "español" if case.language == "es" else "inglés"
        verdict = await complete_structured(
            llm,
            [
                ChatMessage(Role.SYSTEM, TONE_JUDGE_PROMPT.format(language=language)),
                ChatMessage(Role.USER, draft.model_dump_json()),
            ],
            ToneVerdict,
        )
        if verdict.value.professional:
            return []
        issues = "; ".join(verdict.value.issues) or "Tono poco profesional."
        return [Violation("tone", issues)]

    return validate


def render_partner_email(draft: PartnerEmailDraft, case: PartnerEmailCase) -> RenderedEmail:
    language = case.language
    parts = [GREETINGS[language].format(name=case.contact_name), draft.summary, draft.body]
    if draft.requested_actions:
        actions = "\n".join(f"{i}. {a}" for i, a in enumerate(draft.requested_actions, start=1))
        parts.append(f"{ACTIONS_TITLE[language]}\n{actions}")
    parts += [SIGNATURES[language], f"Ref.: {case.reference}", "---", FOOTERS[language]]
    return RenderedEmail(
        subject=f"{SUBJECT_PREFIX} {draft.subject} · {case.reference}",
        body="\n\n".join(parts),
    )


PARTNER_EMAIL_PROMPT = PROMPTS.get("partner-email").text


def partner_email_messages(case: PartnerEmailCase) -> list[ChatMessage]:
    language = "español" if case.language == "es" else "inglés"
    facts = json.dumps(dict(case.facts), ensure_ascii=False)
    return [
        ChatMessage(
            Role.SYSTEM, PARTNER_EMAIL_PROMPT.format(language=language), cache_checkpoint=True
        ),
        ChatMessage(
            Role.USER,
            f"Partner: {case.partner_name}\nHechos verificados: {facts}\n\n"
            f"<notas_internas>\n{case.internal_notes}\n</notas_internas>",
        ),
    ]


@dataclass(frozen=True, slots=True)
class PartnerEmailResult:
    correction: CorrectionResult[PartnerEmailDraft]
    email: RenderedEmail | None

    @property
    def escalated(self) -> bool:
        return self.email is None


async def draft_partner_email(
    llm: LLMPort,
    case: PartnerEmailCase,
    *,
    judge: LLMPort | None = None,
    max_attempts: int = 3,
    validators: Sequence[Validator[PartnerEmailDraft, PartnerEmailCase]] = (
        DETERMINISTIC_EMAIL_VALIDATORS
    ),
) -> PartnerEmailResult:
    stages = [validators] if judge is None else [validators, [tone_judge(judge)]]
    correction = await generate_validated(
        llm,
        partner_email_messages(case),
        PartnerEmailDraft,
        validators=[staged(*stages)],
        context=case,
        max_attempts=max_attempts,
    )
    email = None if correction.value is None else render_partner_email(correction.value, case)
    return PartnerEmailResult(correction, email)


SEND_PARTNER_EMAIL = "send_partner_email"


class SendPartnerEmailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(pattern=r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$", max_length=254)
    reference: str = Field(max_length=40)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=6000)


type EmailSender = Callable[[SendPartnerEmailArgs], Awaitable[str]]


def build_partner_email_tools(send: EmailSender) -> ToolRegistry:
    """Tool de envío: nunca la ve un modelo; solo la ejecuta `ApprovalService` tras aprobarla."""

    async def send_partner_email(args: SendPartnerEmailArgs, _: ToolContext) -> dict[str, str]:
        return {"message_id": await send(args), "reference": args.reference}

    return ToolRegistry(
        [
            Tool(
                SEND_PARTNER_EMAIL,
                "Envía un email ya validado y aprobado a un partner.",
                SendPartnerEmailArgs,
                send_partner_email,
                read_only=False,
            )
        ]
    )


async def submit_partner_email(
    approvals: ApprovalService,
    result: PartnerEmailResult,
    case: PartnerEmailCase,
    *,
    to: str,
    requested_by: str,
) -> ApprovalRequest:
    """Deja el email validado pendiente de aprobación (cuatro ojos: otra persona lo envía)."""
    if result.email is None:
        raise ValueError("El borrador no superó la validación: no se puede enviar.")
    args = SendPartnerEmailArgs(
        to=to, reference=case.reference, subject=result.email.subject, body=result.email.body
    )
    return await approvals.request(
        SEND_PARTNER_EMAIL,
        args,
        ToolContext(customer_id=requested_by, request_id=case.reference),
        reason="Comunicación externa: un email enviado no se puede retirar.",
        summary=f"Email a {case.partner_name} <{to}>: {result.email.subject}",
    )
