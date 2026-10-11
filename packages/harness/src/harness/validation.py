"""Validación determinista de lo que produce un LLM: forma (JSON + Pydantic), patrones (regex) y
reglas de negocio. Un validador nunca lanza: devuelve violaciones que se pueden explicar al modelo.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from headache_core.domain import RuleViolation


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    # Seguro para devolverlo al modelo: explica qué falla, sin repetir datos sensibles.
    message: str
    path: str = ""
    # False: reintentar no lo arregla (p. ej. contenido malicioso); se escala sin más intentos.
    correctable: bool = True


type Validator[T, C] = Callable[[T, C], Sequence[Violation] | Awaitable[Sequence[Violation]]]


async def run_validators[T, C](
    validators: Iterable[Validator[T, C]], value: T, context: C
) -> list[Violation]:
    """Ejecuta TODOS los validadores: el feedback debe listar todos los fallos a la vez."""
    violations: list[Violation] = []
    for validator in validators:
        result = validator(value, context)
        if inspect.isawaitable(result):
            violations.extend(await result)
        else:
            violations.extend(result)
    return violations


def from_rules(rules: Iterable[RuleViolation], *, path: str = "") -> list[Violation]:
    return [Violation(r.code, r.message, path) for r in rules]


def extract_json(text: str) -> str:
    """El objeto JSON de la respuesta, aunque venga con vallas de Markdown o texto alrededor."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start > 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def parse_output[T: BaseModel](text: str, model: type[T]) -> tuple[T | None, list[Violation]]:
    try:
        data = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        return None, [Violation("invalid_json", f"JSON no válido: {exc.msg} (línea {exc.lineno}).")]
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        # Sin `input`: se explica qué campo falla, no se reenvía lo que inventó el modelo.
        return None, [
            Violation(
                "schema",
                str(error["msg"]),
                ".".join(str(p) for p in error["loc"]) or "(raíz)",
            )
            for error in exc.errors(include_url=False, include_input=False, include_context=False)
        ]


def forbidden_patterns[T](
    get: Callable[[T], str], patterns: Mapping[str, re.Pattern[str]], *, path: str = ""
) -> Validator[T, object]:
    """Regex de contenido prohibido. El código de cada patrón describe el problema, no el dato."""

    def validate(value: T, _: object) -> list[Violation]:
        text = get(value)
        return [
            Violation(code, f"Contiene contenido prohibido ({code}): elimínalo.", path)
            for code, pattern in patterns.items()
            if pattern.search(text)
        ]

    return validate


def render_feedback(violations: Sequence[Violation]) -> str:
    lines = [f"- [{v.code}] {v.path + ': ' if v.path else ''}{v.message}" for v in violations]
    return "\n".join(lines)
