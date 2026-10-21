"""Redacción mínima para logs y trazas: datos de contacto, de pago y credenciales.

Es la red de seguridad de la telemetría, no el detector de PII del harness (fase 8.4, con
checksums y Presidio). Prefiere redactar de más: un log con `<PHONE>` de sobra no hace daño;
un log con una tarjeta es un incidente de RGPD y de PCI. Cada servicio puede inyectar otro
redactor (`Redactor`) al configurar la telemetría.
"""

from __future__ import annotations

import re
from collections.abc import Callable

type Redactor = Callable[[str], str]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Credenciales primero: un JWT contiene puntos y dígitos que otros patrones romperían.
    ("<JWT>", re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")),
    ("<SECRET>", re.compile(r"(?i)\b(?:bearer|basic)\s+[\w.~+/=-]{8,}")),
    ("<EMAIL_ADDRESS>", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("<IBAN_CODE>", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("<ES_NIF>", re.compile(r"\b(?:\d{8}|[XYZxyz]-?\d{7})-?[A-Za-z]\b")),
    # Internacional con prefijo, o nacional de 9 cifras que empieza por 6, 7, 8 o 9.
    # Sin más: una fecha (2026-10-21) o un importe no deben parecer teléfonos.
    (
        "<PHONE_NUMBER>",
        re.compile(
            r"(?<![\w+-])(?:\+\d{1,3}[ ]?\d(?:[ ]?\d){7,12}|[6-9]\d{2} ?\d{3} ?\d{3})(?![\w-])"
        ),
    ),
)
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _card(match: re.Match[str]) -> str:
    # Con checksum: un timestamp en milisegundos (13 cifras) no es una tarjeta.
    digits = re.sub(r"\D", "", match.group())
    return "<CREDIT_CARD>" if _luhn(digits) else match.group()


def redact_text(text: str) -> str:
    text = _CARD.sub(_card, text)
    for marker, pattern in _PATTERNS:
        text = pattern.sub(marker, text)
    return text
