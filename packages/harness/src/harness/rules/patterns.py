"""Patrones deterministas reutilizables (es/en). La detección de PII con NER llega en la fase 8.4;
esto es la red de seguridad barata que no depende de ningún servicio."""

from __future__ import annotations

import re
from decimal import Decimal

CONTACT_DATA: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(
        r"(?<![\w+])(?:\+\d{1,3}[\s.-]?\d(?:[\s.-]?\d){7,12}|[6789]\d{2}[\s.]?\d{3}[\s.]?\d{3})(?!\d)"
    ),
    "card_number": re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b"),
}

# Afirmaciones o promesas de reembolso. La negación se comprueba aparte (`claims_refund`).
_REFUND_CLAIM = re.compile(
    r"\b(?:reembolso|devoluci[oó]n)\b[^.\n]{0,40}?\b(?:aprobad[oa]|confirmad[oa]|procesad[oa]"
    r"|tramitad[oa]|realizad[oa]|emitid[oa])\b"
    r"|\b(?:te|le|os|les)\s+(?:reembolsaremos|devolveremos|reembolsamos|devolvemos)\b"
    r"|\bhemos\s+(?:reembolsado|devuelto)\b"
    r"|\brefund\b[^.\n]{0,40}?\b(?:approved|confirmed|processed|issued|completed)\b"
    r"|\b(?:we|we'll|we\s+will|we\s+have|you'll|you\s+will)\s+(?:refund|get\s+(?:a|your)"
    r"(?:\s+full)?\s+refund|receive\s+(?:a|your)(?:\s+full)?\s+refund|been\s+refunded)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:no|not|never|nunca|ni|denegad[oa]|rechazad[oa]|cannot|can't|won't)\b|n't\b",
    re.IGNORECASE,
)

_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,6}(?:[.,]\d{1,2})?)\s?(?:EUR|USD|GBP|MXN|€|\$|£)"
    r"|(?:€|\$|£)\s?(\d{1,6}(?:[.,]\d{1,2})?)(?![\d.,]*\d)"
)


def claims_refund(text: str) -> bool:
    """¿Afirma o promete un reembolso? Ignora las frases negadas ("no ha sido aprobado")."""
    for match in _REFUND_CLAIM.finditer(text):
        window = text[max(0, match.start() - 20) : match.end()]
        if not _NEGATION.search(window):
            return True
    return False


def money_amounts(text: str) -> set[Decimal]:
    """Importes con moneda. Se compara solo el valor: '60,00 €' == '60.00 EUR'."""
    return {
        Decimal((match.group(1) or match.group(2)).replace(",", "."))
        for match in _AMOUNT.finditer(text)
    }
