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

from harness.normalize import detection_view, sanitize
from harness.rules.patterns import CONTACT_DATA
from headache_core.application.tools import ToolContext

type TextFilter = Callable[[str, ToolContext], Awaitable[tuple[str, tuple[str, ...]]]]

PII_REDACTED = "pii_redacted"
_NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIF = re.compile(r"\b(\d{8})-?([A-Za-z])\b")
_NIE = re.compile(r"\b([XYZxyz])-?(\d{7})-?([A-Za-z])\b")
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[\s-]{0,3}\d){12,}(?!\d)")
_CARD_MIN, _CARD_MAX = 13, 19


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


def card_spans(text: str) -> list[PiiSpan]:
    """Tarjetas en tramos de dígitos con cualquier separador corto (espacios, guiones).

    Dentro de cada tramo se busca una ventana de 13 a 19 dígitos que pase Luhn: con «pedido 35
    4111 1111 1111 1111», el tramo entero no pasa el checksum, pero la tarjeta sí (fase 12.4).
    Las ventanas empiezan y acaban en un separador (o en el borde del tramo): un número largo
    sin separadores no se trocea, que multiplicaría los falsos positivos de Luhn (1 de cada 10).
    """
    spans: list[PiiSpan] = []
    for match in _DIGIT_RUN.finditer(text):
        run = match.group()
        positions = [match.start() + i for i, c in enumerate(run) if c.isdigit()]
        digits = "".join(text[p] for p in positions)
        offsets = [p - match.start() for p in positions]
        starts = [i for i, o in enumerate(offsets) if o == 0 or not run[o - 1].isdigit()]
        ends = {
            i + 1 for i, o in enumerate(offsets) if o == len(run) - 1 or not run[o + 1].isdigit()
        }
        covered = 0
        for start in starts:
            if start < covered:
                continue
            for end in sorted(ends, reverse=True):
                length = end - start
                if _CARD_MIN <= length <= _CARD_MAX and _luhn(digits[start:end]):
                    spans.append(PiiSpan("CREDIT_CARD", positions[start], positions[end - 1] + 1))
                    covered = end
                    break
    return spans


class RegexPiiDetector:
    """Patrones con checksum: menos falsos positivos que una regex ingenua, sin modelos.

    Detecta sobre `detection_view` (dígitos de otros alfabetos y espacios raros → ASCII, misma
    longitud), así las posiciones valen para redactar el texto original."""

    def analyze(self, text: str, language: str) -> list[PiiSpan]:
        text = detection_view(text)
        spans: list[PiiSpan] = []
        simple = {
            "EMAIL_ADDRESS": CONTACT_DATA["email"],
            "PHONE_NUMBER": CONTACT_DATA["phone"],
            "IBAN_CODE": CONTACT_DATA["iban"],
        }
        for entity, pattern in simple.items():
            spans += [PiiSpan(entity, m.start(), m.end()) for m in pattern.finditer(text)]
        spans += card_spans(text)
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
        # Al modelo llega el texto saneado: sin invisibles y con lo escondido en etiquetas
        # Unicode a la vista (fase 12.4). Así tampoco puede leer lo que el filtro no ve.
        text = sanitize(text)
        # Presidio es CPU (spaCy): fuera del bucle de eventos.
        spans = await asyncio.to_thread(detector.analyze, text, language)
        if not spans:
            return text, ()
        return redact(text, spans), (PII_REDACTED,)

    return apply
