"""Saneamiento de la salida de una tool antes de devolvérsela al modelo.

La salida de una tool es *dato no confiable*: puede traer texto de terceros (descripciones de
partners, emails...) y acaba dentro del prompt. Aquí solo se aplican reglas deterministas de
forma y tamaño; la detección de prompt injection llega con los guardrails (fase 8).
"""

from __future__ import annotations

import json
import re
from typing import Any

# Controles C0/C1 (salvo \t y \n), ancho cero, marcas bidi y caracteres "tag" de Unicode:
# invisibles para un humano pero no para el modelo (ASCII smuggling).
_INVISIBLE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069"
    "\ufeff\U000e0000-\U000e007f]"
)
TRUNCATION_MARK = "…[truncado]"


def _clean(value: Any, max_string_chars: int) -> Any:
    if isinstance(value, str):
        text = _INVISIBLE.sub("", value)
        if len(text) > max_string_chars:
            return text[:max_string_chars] + TRUNCATION_MARK
        return text
    if isinstance(value, dict):
        return {str(k): _clean(v, max_string_chars) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item, max_string_chars) for item in value]
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_tool_output(value: Any, *, max_chars: int = 4000, max_string_chars: int = 500) -> str:
    """JSON compacto y acotado. Las listas se recortan por elementos (nunca a mitad de JSON)
    y se indica cuántos se devuelven del total, para que el modelo sepa que hay más."""
    cleaned = _clean(value, max_string_chars)
    if isinstance(cleaned, list):
        total = len(cleaned)
        for returned in range(total, -1, -1):
            text = _dumps({"items": cleaned[:returned], "returned": returned, "total": total})
            if len(text) <= max_chars:
                return text
    text = _dumps(cleaned)
    if len(text) <= max_chars:
        return text
    return _dumps({"truncated": True, "preview": text[: max_chars // 2]})


def render_tool_error(kind: str, message: str, *, retryable: bool = False) -> str:
    """Error estructurado: el modelo puede corregir argumentos o explicar el fallo al usuario."""
    return _dumps(
        {"error": {"type": kind, "message": _clean(message, 1000), "retryable": retryable}}
    )
