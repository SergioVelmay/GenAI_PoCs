"""Reglas de redacción compartidas por los textos que Headache envía (partners y clientes).

Deterministas y baratas: tono, datos internos y huecos que pone la plantilla. Se aplican antes
de pagar un juez LLM (`staged`).
"""

from __future__ import annotations

import re
import unicodedata

from harness.validation import Violation

# Nunca salen de Headache: identifican (o enlazan con) clientes, pedidos o reembolsos concretos.
INTERNAL_IDS = re.compile(r"\b(?:cus|ord|ref)_[A-Za-z0-9]+\b")
INTERNAL_VOCABULARY = re.compile(
    r"\b(?:comisi[oó]n(?:es)?|m[aá]rgen(?:es)?|take[\s-]?rate|commissions?|margins?|"
    r"uso\s+interno|internal\s+only|confidencial\s+interno)\b",
    re.IGNORECASE,
)
BLAME = re.compile(
    r"\b(?:culpa|negligencia|vuestro\s+error|su\s+error|incompetencia|fault|negligen(?:ce|t)|"
    r"your\s+mistake|liable|responsabilidad\s+legal)\b",
    re.IGNORECASE,
)
PROMISE = re.compile(
    r"\b(?:garantiza(?:mos)?|compensaremos|indemnizaremos|we\s+guarantee|we\s+will\s+compensate|"
    r"guaranteed)\b",
    re.IGNORECASE,
)
SHOUTING = re.compile(r"\b[A-ZÁÉÍÓÚÑ]{4,}\b")
EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b50\u2b55]")
GREETING = re.compile(r"^\s*(?:hola|estimad[oa]s?|buen[oa]s|dear|hi|hello)\b", re.IGNORECASE)
SIGN_OFF = re.compile(
    r"\b(?:un\s+saludo|saludos|atentamente|cordialmente|best\s+regards|kind\s+regards|"
    r"sincerely)\b",
    re.IGNORECASE,
)
ACRONYMS = frozenset({"RGPD", "GDPR", "UTC", "IBAN", "PDF", "CEST", "FAQ"})


def fold(text: str) -> str:
    """Minúsculas y sin tildes: 'Pérez' y 'perez' son el mismo nombre."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def tone_violations(text: str) -> list[Violation]:
    violations: list[Violation] = []
    if text.count("!") > 1:
        violations.append(Violation("exclamations", "Como mucho una exclamación en todo el email."))
    if [w for w in SHOUTING.findall(text) if w not in ACRONYMS]:
        violations.append(Violation("shouting", "No escribas palabras enteras en mayúsculas."))
    if EMOJI.search(text):
        violations.append(Violation("emoji", "Sin emojis: es una comunicación profesional."))
    if BLAME.search(text):
        violations.append(
            Violation("blame", "No atribuyas culpas ni admitas responsabilidades legales.")
        )
    if PROMISE.search(text):
        violations.append(
            Violation("unauthorized_promise", "No garantices ni prometas compensaciones.")
        )
    return violations


def has_template_text(text: str) -> bool:
    """¿El modelo ha escrito el saludo o la despedida que pone la plantilla?"""
    return bool(GREETING.search(text) or SIGN_OFF.search(text))
