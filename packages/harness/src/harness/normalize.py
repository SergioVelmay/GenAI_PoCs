"""Normalización de texto antes de los filtros de seguridad (fase 12.4, hallazgo de PyRIT).

Un filtro por patrones solo ve lo que hay escrito tal cual. El red teaming con PyRIT demostró que
basta con caracteres invisibles, etiquetas Unicode o dígitos «de otro alfabeto» para que la PII
pase al proveedor del LLM y la inyección pase el escudo. Dos vistas:

- `sanitize`: lo que puede llegar al modelo. Revela el texto escondido en etiquetas Unicode
  (U+E0020-E007E → ASCII: el modelo sí las lee, es el *ASCII smuggling*) y quita los invisibles
  (ancho cero, control bidi, selectores de variante). No toca nada visible.
- `detection_view`: solo para detectar. NFKC carácter a carácter (dígitos de ancho completo o
  matemáticos → ASCII, espacios raros → espacio) SIN cambiar la longitud, para que las posiciones
  detectadas sirvan para redactar el texto saneado.
"""

from __future__ import annotations

import re
import unicodedata

_TAG_FIRST, _TAG_LAST = 0xE0020, 0xE007E
_INVISIBLE = re.compile(
    "[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufe00-\ufe0f\ufeff"
    "\U000e0000-\U000e001f\U000e007f\U000e0100-\U000e01ef]"
)


def sanitize(text: str) -> str:
    revealed = "".join(
        chr(ord(c) - 0xE0000) if _TAG_FIRST <= ord(c) <= _TAG_LAST else c for c in text
    )
    return _INVISIBLE.sub("", revealed)


def _ascii_like(char: str) -> str:
    folded = unicodedata.normalize("NFKC", char)
    if len(folded) != 1:
        return char
    return " " if folded.isspace() else folded


def detection_view(text: str) -> str:
    """Misma longitud que `text`: cada posición se corresponde con la original."""
    return "".join(_ascii_like(c) for c in text)
