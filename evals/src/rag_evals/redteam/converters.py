"""Conversores de texto para mutar ataques: un puerto propio y el adaptador de PyRIT.

Solo conversores deterministas y sin LLM (coste 0): codificaciones, homoglifos, espacios,
caracteres invisibles... Las técnicas que necesitan un LLM atacante (Crescendo, PAIR, TAP) van a
`docs/pendientes-cloud.md`. Tampoco se usan las plantillas de jailbreak de PyRIT: citan marcas
comerciales de terceros que no pueden aparecer en el repo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class TextConverter(Protocol):
    @property
    def name(self) -> str: ...

    async def convert(self, text: str) -> str: ...


@dataclass(frozen=True, slots=True)
class Identity:
    """Sin mutación: la línea base contra la que se compara cada conversor."""

    name: str = "original"

    async def convert(self, text: str) -> str:
        return text


@dataclass(frozen=True, slots=True)
class PyritConverter:
    name: str
    converter: Any  # pyrit.converter.Converter (extra `redteam`)

    async def convert(self, text: str) -> str:
        result = await self.converter.convert_async(prompt=text, input_type="text")
        return str(result.output_text)


def pyrit_available() -> bool:
    try:
        import pyrit  # noqa: F401
    except ImportError:
        return False
    return True


def pyrit_converters() -> Sequence[TextConverter]:
    """Familias de evasión de un filtro por patrones, todas deterministas."""
    try:
        from pyrit.converter import (
            AsciiSmugglerConverter,
            Base64Converter,
            CharacterSpaceConverter,
            DiacriticConverter,
            LeetspeakConverter,
            RandomCapitalLettersConverter,
            ROT13Converter,
            UnicodeConfusableConverter,
            ZeroWidthConverter,
        )
    except ImportError as exc:
        raise SystemExit("PyRIT no está instalado: uv sync --all-packages --extra redteam") from exc
    return (
        PyritConverter("base64", Base64Converter()),
        PyritConverter("rot13", ROT13Converter()),
        PyritConverter("leetspeak", LeetspeakConverter(deterministic=True)),
        PyritConverter("homoglifos", UnicodeConfusableConverter(deterministic=True)),
        PyritConverter("espaciado", CharacterSpaceConverter()),
        PyritConverter("ancho_cero", ZeroWidthConverter()),
        PyritConverter("tildes", DiacriticConverter()),
        PyritConverter("mayusculas", RandomCapitalLettersConverter(percentage=50.0, seed=42)),
        PyritConverter("etiquetas_unicode", AsciiSmugglerConverter(unicode_tags=True)),
    )
