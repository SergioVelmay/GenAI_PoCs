from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from headache_core.adapters.memory import FakeLLM
from headache_core.application.orchestration import (
    InvalidHandoffError,
    TriageAgent,
    envelope_from_payload,
    envelope_to_payload,
    render_handoff,
    tool_context,
)
from headache_core.domain import (
    ROUTES,
    CaseEnvelope,
    CaseOutcome,
    CaseStatus,
    Specialist,
    SupportEmail,
    SupportIntent,
    can_transition,
    is_terminal,
    resolve_language,
    resolve_order_id,
    route,
)

EMAIL = SupportEmail(
    id="eml_1",
    customer_id="cus_1",
    language="es",
    subject="No me llegan las entradas",
    body="Compré entradas (pedido ord_00000001) y no aparecen. Mi teléfono es 600123123.",
    received_at=datetime(2026, 10, 1, tzinfo=UTC),
    order_id="ord_00000001",
)

ENVELOPE = CaseEnvelope(
    case_id="case_1",
    email_id="eml_1",
    customer_id="cus_1",
    specialist=Specialist.KNOWLEDGE,
    intent=SupportIntent.LOST_TICKETS,
    language="es",
    urgency="normal",
    summary="El cliente no encuentra sus entradas.",
    order_id="ord_00000001",
)


def _triage_json(**overrides: object) -> str:
    value: dict[str, object] = {
        "intent": "lost_tickets",
        "language": "es",
        "order_id": "ord_00000001",
        "urgency": "normal",
        "summary": "El cliente no encuentra sus entradas.",
    }
    return json.dumps(value | overrides)


def test_every_intent_has_a_route() -> None:
    assert set(ROUTES) == set(SupportIntent)
    assert route(SupportIntent.REFUND_REQUEST) is Specialist.REFUNDS
    assert route(SupportIntent.PARTNERSHIP) is Specialist.CREATIVE
    assert route(SupportIntent.EVENT_INFO) is Specialist.KNOWLEDGE


@pytest.mark.parametrize(
    ("system", "extracted", "expected"),
    [
        ("ord_00000001", "ord_99999999", "ord_00000001"),  # Manda el sistema.
        (None, "ord_00000002", "ord_00000002"),
        (None, "ord_1; DROP TABLE", None),  # Formato inválido: se descarta.
        (None, None, None),
    ],
)
def test_order_id_prefers_the_system(
    system: str | None, extracted: str | None, expected: str | None
) -> None:
    assert resolve_order_id(system, extracted) == expected


def test_language_falls_back_when_detection_is_not_iso() -> None:
    assert resolve_language(" EN ", "es") == "en"
    assert resolve_language("español", "es") == "es"


def test_closed_cases_cannot_be_reopened() -> None:
    assert can_transition(CaseStatus.TRIAGED, CaseStatus.PENDING_APPROVAL)
    assert can_transition(CaseStatus.PENDING_APPROVAL, CaseStatus.RESOLVED)
    assert not can_transition(CaseStatus.RESOLVED, CaseStatus.ESCALATED)
    assert not can_transition(CaseStatus.PENDING_APPROVAL, CaseStatus.TRIAGED)
    assert is_terminal(CaseStatus.ESCALATED)
    assert not is_terminal(CaseStatus.PENDING_APPROVAL)


def test_outcome_invariants() -> None:
    CaseOutcome("case_1", Specialist.REFUNDS, CaseStatus.PENDING_APPROVAL, "...", approval_id="a")
    with pytest.raises(ValueError, match="approval_id"):
        CaseOutcome("case_1", Specialist.REFUNDS, CaseStatus.PENDING_APPROVAL, "...")
    with pytest.raises(ValueError, match="approval_id"):
        CaseOutcome("case_1", Specialist.REFUNDS, CaseStatus.RESOLVED, "...", approval_id="a")
    with pytest.raises(ValueError, match="no puede dejar"):
        CaseOutcome("case_1", Specialist.REFUNDS, CaseStatus.TRIAGED, "...")


def test_envelope_round_trip() -> None:
    payload = json.loads(json.dumps(envelope_to_payload(ENVELOPE)))

    assert envelope_from_payload(payload) == ENVELOPE
    assert envelope_from_payload(payload | {"order_id": None}).order_id is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"customer_id": ""}, "customer_id"),
        ({"summary": None}, "summary"),
        ({"urgency": "critical"}, "Urgencia"),
        ({"intent": "otra_cosa"}, "otra_cosa"),
        ({"order_id": 123}, "order_id"),
        # Ruta manipulada: un reembolso enviado al agente de Conocimiento.
        ({"intent": "refund_request"}, "no corresponde"),
    ],
)
def test_invalid_payload_is_rejected(change: dict[str, object], message: str) -> None:
    payload = envelope_to_payload(ENVELOPE) | change

    with pytest.raises(InvalidHandoffError, match=message):
        envelope_from_payload(payload)


def test_handoff_message_carries_facts_not_identity_or_email() -> None:
    message = render_handoff(ENVELOPE)

    assert "ord_00000001" in message
    assert "lost_tickets" in message
    assert "cus_1" not in message
    assert "eml_1" not in message
    assert tool_context(ENVELOPE).customer_id == "cus_1"
    assert tool_context(ENVELOPE).request_id == "case_1"
    assert render_handoff(replace(ENVELOPE, order_id=None)).count("no indicado") == 1


async def test_triage_builds_the_envelope_with_domain_routing() -> None:
    llm = FakeLLM([_triage_json(intent="refund_request", language="EN", urgency="high")])

    result = await TriageAgent(llm).triage(EMAIL, case_id="case_9")

    assert result.envelope == CaseEnvelope(
        case_id="case_9",
        email_id="eml_1",
        customer_id="cus_1",
        specialist=Specialist.REFUNDS,
        intent=SupportIntent.REFUND_REQUEST,
        language="en",
        urgency="high",
        summary="El cliente no encuentra sus entradas.",
        order_id="ord_00000001",
    )
    assert len(result.responses) == 1


async def test_triage_never_trusts_the_llm_for_identity_or_order() -> None:
    email = replace(EMAIL, order_id=None)
    llm = FakeLLM([_triage_json(order_id="ord_x' OR 1=1")])

    result = await TriageAgent(llm).triage(email)

    assert result.envelope.customer_id == "cus_1"
    assert result.envelope.order_id is None
    assert result.envelope.case_id.startswith("case_")
    assert result.envelope.specialist is Specialist.KNOWLEDGE


async def test_triage_counts_correction_calls() -> None:
    llm = FakeLLM(['{"intent": "otra_cosa"}', _triage_json()])

    result = await TriageAgent(llm).triage(EMAIL)

    assert len(result.responses) == 2
