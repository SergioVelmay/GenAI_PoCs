from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from harness import Violation, extract_json, forbidden_patterns, parse_output, run_validators
from harness.rules.patterns import CONTACT_DATA, claims_refund, money_amounts


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z]{3}$")
    total: int


def test_extract_json_strips_fences_and_surrounding_text() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Aquí tienes: {"a": 1} ¡listo!') == '{"a": 1}'


def test_parse_output_reports_invalid_json_without_raising() -> None:
    value, violations = parse_output("{no es json", _Out)

    assert value is None
    assert [v.code for v in violations] == ["invalid_json"]


def test_parse_output_maps_schema_errors_to_paths_without_echoing_input() -> None:
    value, violations = parse_output('{"code": "secreto-123", "total": "x", "extra": 1}', _Out)

    assert value is None
    assert {v.path for v in violations} == {"code", "total", "extra"}
    assert all("secreto-123" not in v.message for v in violations)


async def test_run_validators_collects_sync_and_async_violations() -> None:
    def sync_rule(value: str, _: object) -> list[Violation]:
        return [Violation("sync", "s")]

    async def async_rule(value: str, _: object) -> list[Violation]:
        return [Violation("async", "a")]

    violations = await run_validators([sync_rule, async_rule], "x", None)

    assert [v.code for v in violations] == ["sync", "async"]


async def test_forbidden_patterns_names_the_problem_not_the_data() -> None:
    rule = forbidden_patterns(str, {"email": re.compile(r"\S+@\S+")}, path="reply")

    [violation] = await run_validators([rule], "Escribe a ana@example.com", None)

    assert violation.code == "email"
    assert "ana@example.com" not in violation.message


def test_contact_data_detects_pii_but_not_dates_amounts_or_ids() -> None:
    def found(text: str) -> set[str]:
        return {code for code, pattern in CONTACT_DATA.items() if pattern.search(text)}

    assert found("mi email es ana.perez+eventos@example.com") == {"email"}
    assert found("llámame al +34 612 345 678") == {"phone"}
    assert found("o al 612345678") == {"phone"}
    assert found("tarjeta 4111 1111 1111 1111") == {"card_number"}
    assert "iban" in found("IBAN ES91 2100 0418 4502 0005 1332")
    assert found("Pedido ord_0000123, sesión 2026-11-20 20:00, total 60.00 EUR") == set()


def test_claims_refund_ignores_negations() -> None:
    assert claims_refund("Tu reembolso ha sido aprobado.")
    assert claims_refund("Te devolveremos el importe en 5 días.")
    assert claims_refund("Your refund has been processed.")
    assert claims_refund("You will get a full refund.")
    assert not claims_refund("Tu reembolso no ha sido aprobado: está fuera de plazo.")
    assert not claims_refund("Lo siento, no te devolveremos el importe.")
    assert not claims_refund("Your refund was not approved.")
    assert not claims_refund("¿Quieres que solicite el reembolso?")


def test_money_amounts_normalises_formats() -> None:
    assert money_amounts("60,00 €, 60.00 EUR y €60") == {Decimal("60")}
    assert money_amounts("total 30.5 USD y $12") == {Decimal("30.50"), Decimal("12")}
    assert money_amounts("2 entradas el 2026-11-20") == set()
