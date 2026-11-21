"""Detector de PII con Sensitive Data Protection (8.G2) y caída a patrones de `pii_filter`.

El doble calcula los hallazgos como el servicio: posiciones en *codepoints* sobre el texto
recibido. Se sustituye por respuestas capturadas en la primera validación en la nube.
"""

from __future__ import annotations

import pytest
from google.api_core.exceptions import ServiceUnavailable
from google.cloud.dlp_v2 import (
    Finding,
    InfoType,
    InspectContentRequest,
    InspectContentResponse,
    InspectResult,
    Likelihood,
    Location,
    Range,
)

from genaipocs_core.application.tools import ToolContext
from harness.pii import PII_FALLBACK, PII_REDACTED, PiiSpan, PiiUnavailableError, pii_filter
from harness.pii_gcp import (
    DEFAULT_INFO_TYPES,
    SensitiveDataProtectionPiiDetector,
    location_parent,
)

PARENT = "projects/genaipocs-dev/locations/europe-west4"
ANA = ToolContext(customer_id="cus_1")


def _finding(text: str, quote: str, info_type: str, likelihood: int = Likelihood.LIKELY) -> Finding:
    start = text.index(quote)
    return Finding(
        info_type=InfoType(name=info_type),
        likelihood=likelihood,
        location=Location(
            # El servicio da los dos rangos; el de bytes no sirve para indexar un `str`.
            byte_range=Range(
                start=len(text[:start].encode()), end=len(text[: start + len(quote)].encode())
            ),
            codepoint_range=Range(start=start, end=start + len(quote)),
        ),
    )


class FakeDlp:
    def __init__(
        self,
        found: dict[str, str] | None = None,
        *,
        error: Exception | None = None,
        truncated: bool = False,
        shift: int = 0,
    ) -> None:
        self.requests: list[InspectContentRequest] = []
        self.timeouts: list[float] = []
        self._found = found or {}
        self._error = error
        self._truncated = truncated
        self._shift = shift

    def inspect_content(
        self, *, request: InspectContentRequest, timeout: float
    ) -> InspectContentResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self._error is not None:
            raise self._error
        text = request.item.value
        findings = [_finding(text, q, t) for q, t in self._found.items() if q in text]
        for finding in findings:
            finding.location.codepoint_range.end += self._shift
        return InspectContentResponse(
            result=InspectResult(findings=findings, findings_truncated=self._truncated)
        )


def test_codepoint_ranges_map_to_presidio_entities_even_after_accents_and_emoji() -> None:
    text = "Soy José Muñoz 🎟️, NIF 12345678Z, tarjeta 4111 1111 1111 1111"
    client = FakeDlp(
        {
            "José Muñoz": "PERSON_NAME",
            "12345678Z": "SPAIN_NIF_NUMBER",
            "4111 1111 1111 1111": "CREDIT_CARD_NUMBER",
        }
    )
    detector = SensitiveDataProtectionPiiDetector(client, parent=PARENT, timeout_seconds=2.0)

    spans = detector.analyze(text, "es")

    assert [(s.entity, text[s.start : s.end]) for s in spans] == [
        ("PERSON", "José Muñoz"),
        ("ES_NIF", "12345678Z"),
        ("CREDIT_CARD", "4111 1111 1111 1111"),
    ]
    assert all(s.score == 0.8 for s in spans)
    [request] = client.requests
    assert request.parent == PARENT
    assert client.timeouts == [2.0]


def test_inline_config_without_template_and_never_asks_for_the_quote() -> None:
    client = FakeDlp()
    SensitiveDataProtectionPiiDetector(client, parent=PARENT).analyze("hola, una consulta", "es")

    [request] = client.requests
    config = request.inspect_config
    assert [t.name for t in config.info_types] == list(DEFAULT_INFO_TYPES)
    assert config.min_likelihood == Likelihood.LIKELY
    assert not config.include_quote
    assert not request.inspect_template_name


def test_template_decides_the_info_types() -> None:
    client = FakeDlp()
    template = f"{PARENT}/inspectTemplates/genaipocs-pii"
    detector = SensitiveDataProtectionPiiDetector(client, parent=PARENT, inspect_template=template)

    detector.analyze("hola, una consulta", "es")

    [request] = client.requests
    assert request.inspect_template_name == template
    assert not request.inspect_config.info_types
    assert not request.inspect_config.include_quote


def test_unknown_info_types_keep_their_name_and_blank_text_skips_the_call() -> None:
    client = FakeDlp({"ES12 3456": "SPAIN_SOCIAL_SECURITY_NUMBER"})
    detector = SensitiveDataProtectionPiiDetector(client, parent=PARENT)

    assert detector.analyze("   ", "es") == []
    assert not client.requests
    [span] = detector.analyze("NSS ES12 3456", "es")
    assert span.entity == "SPAIN_SOCIAL_SECURITY_NUMBER"


@pytest.mark.parametrize(
    "client",
    [
        FakeDlp(error=ServiceUnavailable("caído")),  # type: ignore[no-untyped-call]
        FakeDlp({"ana@example.com": "EMAIL_ADDRESS"}, truncated=True),
        FakeDlp({"ana@example.com": "EMAIL_ADDRESS"}, shift=500),
    ],
    ids=["api-error", "truncated", "range-out-of-text"],
)
def test_untrustworthy_answers_are_unavailable(client: FakeDlp) -> None:
    detector = SensitiveDataProtectionPiiDetector(client, parent=PARENT)

    with pytest.raises(PiiUnavailableError):
        detector.analyze("Mi email es ana@example.com", "es")


async def test_filter_redacts_with_the_service_markers() -> None:
    detector = SensitiveDataProtectionPiiDetector(
        FakeDlp({"José Muñoz": "PERSON_NAME"}), parent=PARENT
    )

    text, codes = await pii_filter(detector)("Me llamo José Muñoz", ANA)

    assert text == "Me llamo <PERSON>"
    assert codes == (PII_REDACTED,)


async def test_filter_falls_back_to_patterns_when_the_service_is_down() -> None:
    down = SensitiveDataProtectionPiiDetector(
        FakeDlp(error=ServiceUnavailable("caído")),  # type: ignore[no-untyped-call]
        parent=PARENT,
    )

    text, codes = await pii_filter(down)("Me llamo José, mi email es ana@example.com", ANA)

    # El nombre se escapa (los patrones no ven nombres), el email no: el suelo determinista.
    assert text == "Me llamo José, mi email es <EMAIL_ADDRESS>"
    assert codes == (PII_FALLBACK, PII_REDACTED)


async def test_fallback_is_reported_even_without_pii() -> None:
    class Down:
        def analyze(self, text: str, language: str) -> list[PiiSpan]:
            raise PiiUnavailableError("caído")

    text, codes = await pii_filter(Down())("¿A qué hora abren?", ANA)

    assert text == "¿A qué hora abren?"
    assert codes == (PII_FALLBACK,)


def test_location_parent() -> None:
    assert location_parent("genaipocs-dev", "europe-west4") == PARENT
