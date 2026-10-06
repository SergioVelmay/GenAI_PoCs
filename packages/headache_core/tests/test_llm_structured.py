from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from headache_core.adapters.memory import FakeLLM
from headache_core.application import SupportTriageService, TicketTriage
from headache_core.application.llm import (
    StructuredOutputError,
    complete_structured,
    response_schema_for,
)
from headache_core.domain import SupportEmail, SupportIntent
from headache_core.ports import ChatMessage, Role

VALID = {
    "intent": "refund_request",
    "language": "es",
    "order_id": "ord_00000001",
    "urgency": "normal",
    "summary": "El cliente pide el reembolso de su pedido.",
}


def _walk(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [d for item in node for d in _walk(item)]
    if isinstance(node, dict):
        return [node, *(d for value in node.values() for d in _walk(value))]
    return []


def test_schema_is_strict_compatible() -> None:
    schema = response_schema_for(TicketTriage)
    nodes = _walk(dict(schema.json_schema))

    assert schema.name == "TicketTriage"
    assert schema.json_schema["additionalProperties"] is False
    assert set(schema.json_schema["required"]) == set(VALID)  # Opcionales también: `T | None`.
    assert all("default" not in node for node in nodes)
    assert all(set(node) == {"$ref"} for node in nodes if "$ref" in node)


async def test_parses_json_even_inside_markdown_fences() -> None:
    llm = FakeLLM([f"```json\n{json.dumps(VALID)}\n```"])

    result = await complete_structured(llm, [ChatMessage(Role.USER, "hola")], TicketTriage)

    assert result.value.intent is SupportIntent.REFUND_REQUEST
    assert llm.schemas[0] is not None
    assert llm.schemas[0].name == "TicketTriage"


async def test_invalid_output_triggers_a_correction_turn() -> None:
    llm = FakeLLM(['{"intent": "otra_cosa"}', json.dumps(VALID)])

    result = await complete_structured(llm, [ChatMessage(Role.USER, "hola")], TicketTriage)

    assert len(result.responses) == 2  # Las dos llamadas cuentan para el coste.
    correction = llm.calls[1]
    assert correction[-2] == ChatMessage(Role.ASSISTANT, '{"intent": "otra_cosa"}')
    assert "no cumple el esquema" in correction[-1].content


async def test_gives_up_after_max_attempts() -> None:
    llm = FakeLLM(["no es json", "tampoco"])

    with pytest.raises(StructuredOutputError):
        await complete_structured(llm, [ChatMessage(Role.USER, "hola")], TicketTriage)


async def test_support_triage_extracts_fields() -> None:
    email = SupportEmail(
        id="eml_1",
        customer_id="cus_1",
        language="es",
        subject="Reembolso",
        body="Quiero que me devolváis el dinero del pedido ord_00000001.",
        received_at=datetime(2026, 10, 1, tzinfo=UTC),
        order_id="ord_00000001",
    )
    llm = FakeLLM([json.dumps(VALID)])

    result = await SupportTriageService(llm).triage(email)

    assert result.value.order_id == "ord_00000001"
    assert llm.calls[0][0].role is Role.SYSTEM
    assert "ord_00000001" in llm.calls[0][1].content
