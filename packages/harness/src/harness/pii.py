"""PII (RGPD): detectar y seudonimizar datos personales antes de que salgan de nuestro perímetro.

`PiiDetector` es un puerto: `RegexPiiDetector` (determinista, sin dependencias, con validación
de checksum en NIF/NIE y tarjetas) o `PresidioPiiDetector` (NER de spaCy para nombres, en el extra
`pii`). Se redacta con marcadores tipados (`<EMAIL_ADDRESS>`) para que el texto siga teniendo
sentido para el modelo sin contener el dato.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from harness.rules.patterns import CONTACT_DATA
from headache_core.application.tools import ToolContext

type TextFilter = Callable[[str, ToolContext], Awaitable[tuple[str, tuple[str, ...]]]]

PII_REDACTED = "pii_redacted"
_NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIF = re.compile(r"\b(\d{8})-?([A-Za-z])\b")
_NIE = re.compile(r"\b([XYZxyz])-?(\d{7})-?([A-Za-z])\b")


@dataclass(frozen=True, slots=True)
class PiiSpan:
    entity: str
    start: int
    end: int
    score: float = 1.0


class PiiDetector(Protocol):
    def analyze(self, text: str, language: str) -> list[PiiSpan]: ...


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


class RegexPiiDetector:
    """Patrones con checksum: menos falsos positivos que una regex ingenua, sin modelos."""

    def analyze(self, text: str, language: str) -> list[PiiSpan]:
        spans: list[PiiSpan] = []
        simple = {
            "EMAIL_ADDRESS": CONTACT_DATA["email"],
            "PHONE_NUMBER": CONTACT_DATA["phone"],
            "IBAN_CODE": CONTACT_DATA["iban"],
        }
        for entity, pattern in simple.items():
            spans += [PiiSpan(entity, m.start(), m.end()) for m in pattern.finditer(text)]
        for match in CONTACT_DATA["card_number"].finditer(text):
            if _luhn(re.sub(r"\D", "", match.group())):
                spans.append(PiiSpan("CREDIT_CARD", match.start(), match.end()))
        for match in _NIF.finditer(text):
            if _NIF_LETTERS[int(match.group(1)) % 23] == match.group(2).upper():
                spans.append(PiiSpan("ES_NIF", match.start(), match.end()))
        for match in _NIE.finditer(text):
            number = "XYZ".index(match.group(1).upper()) * 10_000_000 + int(match.group(2))
            if _NIF_LETTERS[number % 23] == match.group(3).upper():
                spans.append(PiiSpan("ES_NIE", match.start(), match.end()))
        return spans


def redact(text: str, spans: Sequence[PiiSpan]) -> str:
    """Sustituye cada tramo por `<ENTIDAD>`; los solapes se funden (gana el más largo)."""
    merged: list[PiiSpan] = []
    for span in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
        if merged and span.start < merged[-1].end:
            last = merged[-1]
            if span.end > last.end:
                merged[-1] = PiiSpan(last.entity, last.start, span.end, last.score)
            continue
        merged.append(span)
    parts: list[str] = []
    cursor = 0
    for span in merged:
        parts += [text[cursor : span.start], f"<{span.entity}>"]
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def pii_filter(detector: PiiDetector, *, language: str = "es") -> TextFilter:
    async def apply(text: str, _: ToolContext) -> tuple[str, tuple[str, ...]]:
        # Presidio es CPU (spaCy): fuera del bucle de eventos.
        spans = await asyncio.to_thread(detector.analyze, text, language)
        if not spans:
            return text, ()
        return redact(text, spans), (PII_REDACTED,)

    return apply
