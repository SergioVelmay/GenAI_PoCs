from __future__ import annotations

import json

from headache_core.domain.tool_output import (
    TRUNCATION_MARK,
    render_tool_error,
    render_tool_output,
)


def test_strips_invisible_characters_but_keeps_newlines() -> None:
    text = "Hola\u200b mundo\u202e\U000e0041\x07\nfin"

    assert json.loads(render_tool_output({"t": text})) == {"t": "Hola mundo\nfin"}


def test_truncates_long_strings() -> None:
    payload = json.loads(render_tool_output({"d": "x" * 50}, max_string_chars=10))

    assert payload["d"] == "x" * 10 + TRUNCATION_MARK


def test_lists_are_trimmed_by_items_and_stay_valid_json() -> None:
    items = [{"id": f"evt_{i}", "title": "t" * 40} for i in range(50)]

    text = render_tool_output(items, max_chars=600)
    payload = json.loads(text)

    assert len(text) <= 600
    assert payload["total"] == 50
    assert payload["returned"] == len(payload["items"]) < 50
    assert payload["items"][0]["id"] == "evt_0"


def test_oversized_object_becomes_a_preview() -> None:
    payload = json.loads(render_tool_output({str(i): "v" * 100 for i in range(100)}, max_chars=300))

    assert payload["truncated"] is True


def test_error_payload_is_structured() -> None:
    payload = json.loads(render_tool_error("not_found", "Pedido\u200b ord_1 no existe."))

    assert payload == {
        "error": {"type": "not_found", "message": "Pedido ord_1 no existe.", "retryable": False}
    }
