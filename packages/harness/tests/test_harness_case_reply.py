from __future__ import annotations

import pytest

from harness.rules.case_reply import (
    REPLY_VALIDATORS,
    ReplyCase,
    ReplyDraft,
    render_reply,
)
from harness.validation import run_validators

CASE = ReplyCase(
    reference="case_1",
    audience="customer",
    language="en",
    brief="Caso: case_1",
    facts={"importe": "60.00 EUR"},
    allowed_ids=frozenset({"ord_00000001"}),
)


async def _codes(body: str, subject: str = "About your complaint") -> set[str]:
    violations = await run_validators(
        REPLY_VALIDATORS, ReplyDraft(subject=subject, body=body), CASE
    )
    return {v.code for v in violations}


@pytest.mark.parametrize(
    "body",
    [
        # Controles benignos: no deben disparar nada.
        "We are sorry about the delay with order ord_00000001. Our team will review it.",
        "We cannot confirm a refund in this reply; the refunds team will review your order.",
        "Your order total was 60.00 EUR and the case is now with the organiser.",
    ],
)
async def test_benign_replies_pass(body: str) -> None:
    assert await _codes(body) == set()


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("Hello, we are sorry. Kind regards", "template_owned"),
        ("Call us at +34 600 123 123.", "phone"),
        ("We checked order ord_99999999 too.", "internal_id"),
        ("We will refund your tickets.", "refund_promise"),
        ("You will get 80.00 EUR back as a gesture.", "ungrounded_amount"),
        ("It was the venue's fault and we guarantee it won't happen again.", "blame"),
    ],
)
async def test_risky_replies_are_flagged(body: str, code: str) -> None:
    assert code in await _codes(body)


def test_template_adds_greeting_signature_reference_and_footer() -> None:
    email = render_reply(ReplyDraft(subject="Your complaint", body="We are sorry."), CASE)

    assert email.subject == "[Headache] Your complaint · case_1"
    assert email.body.startswith("Hello,\n\nWe are sorry.")
    assert "Headache Customer Care" in email.body
    assert "Ref.: case_1" in email.body
    assert email.body.endswith("Headache processes your data under the GDPR.")
