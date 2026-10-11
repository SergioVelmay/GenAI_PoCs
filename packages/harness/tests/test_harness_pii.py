from __future__ import annotations

from importlib.util import find_spec

import pytest

from harness.pii import PII_REDACTED, PiiSpan, RegexPiiDetector, pii_filter, redact
from headache_core.application.tools import ToolContext

ANA = ToolContext(customer_id="cus_1")


def _entities(text: str) -> list[str]:
    return [s.entity for s in RegexPiiDetector().analyze(text, "es")]


def test_regex_detector_validates_checksums() -> None:
    assert _entities("DNI 12345678Z") == ["ES_NIF"]
    assert _entities("DNI 12345678A") == []
    assert _entities("NIE X1234567L") == ["ES_NIE"]
    assert _entities("tarjeta 4111 1111 1111 1111") == ["CREDIT_CARD"]
    assert _entities("tarjeta 4111 1111 1111 1112") == []
    assert set(_entities("ana@example.com o +34 612 345 678")) == {
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
    }
    assert _entities("Pedido ord_0000123 del 2026-11-20 por 60.00 EUR en Madrid") == []


def test_redact_merges_overlapping_spans() -> None:
    text = "IBAN ES91 2100 0418 4502 0005 1332."
    spans = [PiiSpan("IBAN_CODE", 5, 34), PiiSpan("CREDIT_CARD", 10, 30)]

    assert redact(text, spans) == "IBAN <IBAN_CODE>."


async def test_pii_filter_reports_what_it_redacted() -> None:
    apply = pii_filter(RegexPiiDetector())

    assert await apply("Escríbeme a ana@example.com", ANA) == (
        "Escríbeme a <EMAIL_ADDRESS>",
        (PII_REDACTED,),
    )
    assert await apply("¿Qué hay en Madrid?", ANA) == ("¿Qué hay en Madrid?", ())


@pytest.mark.skipif(find_spec("presidio_analyzer") is None, reason="extra `pii` no instalado")
def test_presidio_detects_person_names_and_keeps_cities() -> None:
    from harness.pii_presidio import PresidioPiiDetector

    detector = PresidioPiiDetector()
    text = "Hola, me llamo Marta Gil y mi DNI es 12345678Z. Voy al concierto de Madrid."

    redacted = redact(text, detector.analyze(text, "es"))

    assert "Marta Gil" not in redacted and "12345678Z" not in redacted
    assert "Madrid" in redacted
