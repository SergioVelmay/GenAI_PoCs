from __future__ import annotations

import json
from typing import Any

from harness.rules.partner_email import (
    FOOTERS,
    SUBJECT_PREFIX,
    PartnerEmailCase,
    draft_partner_email,
)
from headache_core.adapters.memory import FakeLLM

CASE = PartnerEmailCase(
    reference="INC-2026-0042",
    partner_name="Sala Aurora",
    contact_name="Laura",
    language="es",
    facts={
        "event": "Candlelit Strings",
        "session": "2026-11-20 20:00 (Madrid)",
        "affected_tickets": "38",
        "refunded_total": "1140.00 EUR",
    },
    internal_notes=(
        "La clienta Marta Gil (marta.gil@example.com, cus_0000042) se quejó del retraso. "
        "Teatro Nébula tuvo el mismo problema. La comisión de este partner es del 12 %."
    ),
    forbidden_terms={"Marta Gil": "customer_name", "Teatro Nébula": "other_partner"},
    must_mention=("Candlelit Strings",),
)


def _draft(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "subject": "Retraso en la sesión del 20 de noviembre",
        "summary": "La sesión de Candlelit Strings del 20 de noviembre empezó con 40 minutos de "
        "retraso y hemos reembolsado 38 entradas.",
        "body": "El importe total reembolsado a los asistentes fue de 1140.00 EUR. Nos gustaría "
        "entender la causa para evitar que se repita.",
        "requested_actions": ["Enviar un informe del incidente antes del viernes."],
    }
    return json.dumps(base | overrides, ensure_ascii=False)


async def test_valid_draft_is_rendered_with_the_mandatory_template() -> None:
    result = await draft_partner_email(FakeLLM([_draft()]), CASE)

    assert result.email is not None
    assert result.email.subject == (
        f"{SUBJECT_PREFIX} Retraso en la sesión del 20 de noviembre · INC-2026-0042"
    )
    body = result.email.body
    assert body.startswith("Hola, Laura:")
    assert "Acciones solicitadas:\n1. Enviar un informe" in body
    assert "Equipo de Partners de Headache" in body and body.endswith(FOOTERS["es"])


async def test_prohibited_data_from_internal_notes_is_removed_by_feedback() -> None:
    leaky = _draft(
        body="Marta Gil (marta.gil@example.com, cus_0000042) se quejó, igual que en Teatro "
        "Nebula. Recordad que vuestra comisión es del 12 %. Total: 1140.00 EUR."
    )
    llm = FakeLLM([leaky, _draft()])

    result = await draft_partner_email(llm, CASE)

    assert result.email is not None
    codes = {v.code for v in result.correction.attempts[0].violations}
    assert codes == {"customer_name", "other_partner", "email", "internal_id", "internal_data"}
    feedback = llm.calls[1][-1].content
    assert "marta.gil@example.com" not in feedback and "Marta Gil" not in feedback


async def test_unprofessional_tone_and_invented_amounts_are_rejected() -> None:
    rude = _draft(
        summary="¡¡URGENTE!! Candlelit Strings empezó tarde y es culpa vuestra 😡",
        body="Os garantizamos que compensaremos con 500.00 EUR si no se repite.",
    )

    result = await draft_partner_email(FakeLLM([rude] * 3), CASE)

    assert result.escalated
    codes = {v.code for v in result.correction.violations}
    assert codes == {
        "exclamations",
        "shouting",
        "emoji",
        "blame",
        "unauthorized_promise",
        "ungrounded_amount",
    }


async def test_template_sections_and_required_mentions_are_enforced() -> None:
    draft = _draft(
        subject=f"{SUBJECT_PREFIX} Incidencia",
        summary="Hola Laura, la sesión empezó con retraso.",
        body="Necesitamos un informe. Un saludo, el equipo.",
    )

    result = await draft_partner_email(FakeLLM([draft]), CASE, max_attempts=1)

    violations = {(v.code, v.path) for v in result.correction.violations}
    assert violations == {
        ("template_owned", "subject"),
        ("template_owned", "summary"),
        ("template_owned", "body"),
        ("missing_mention", "body"),
    }


async def test_missing_mention_is_reported_once_the_template_is_respected() -> None:
    draft = _draft(summary="La sesión del 20 de noviembre empezó con retraso.", body="Gracias.")

    result = await draft_partner_email(FakeLLM([draft]), CASE, max_attempts=1)

    assert [v.code for v in result.correction.violations] == ["missing_mention"]


async def test_tone_judge_runs_only_after_deterministic_rules_pass() -> None:
    judge = FakeLLM(
        [
            json.dumps({"professional": False, "issues": ["Tono pasivo-agresivo."]}),
            json.dumps({"professional": True, "issues": []}),
        ]
    )
    leaky = _draft(body="Escribid a marta.gil@example.com. Total: 1140.00 EUR.")
    llm = FakeLLM([leaky, _draft(), _draft()])

    result = await draft_partner_email(llm, CASE, judge=judge)

    assert result.email is not None
    assert [a.violations[0].code for a in result.correction.attempts[:2]] == ["email", "tone"]
    assert len(judge.calls) == 2
    assert "Tono pasivo-agresivo." in llm.calls[2][-1].content
    assert "español" in judge.calls[0][0].content
