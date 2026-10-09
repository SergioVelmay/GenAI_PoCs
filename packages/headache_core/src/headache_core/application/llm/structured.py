"""Structured outputs: el modelo devuelve JSON que cumple un esquema Pydantic.

El proveedor *garantiza* la forma (json_schema strict en Azure, tool use forzado en Bedrock);
aquí validamos igualmente y, si falla, pedimos una corrección: nunca se confía a ciegas.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from headache_core.ports import ChatMessage, LLMError, LLMPort, LLMResponse, ResponseSchema, Role


class StructuredOutputError(LLMError):
    """El modelo no devolvió JSON válido para el esquema tras todos los intentos."""


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    value: T
    # Todas las llamadas hechas (incluidas las de corrección): necesarias para el coste real.
    responses: list[LLMResponse] = field(default_factory=list)


def response_schema_for(output_type: type[BaseModel]) -> ResponseSchema:
    return ResponseSchema(
        name=output_type.__name__,
        json_schema=strict_json_schema(output_type),
        description=(output_type.__doc__ or "").strip(),
    )


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _to_strict(model.model_json_schema())


# Palabras clave que el modo strict de OpenAI no admite: Pydantic las sigue validando en local.
_UNSUPPORTED_STRICT_KEYS = frozenset({"default", "minLength", "maxLength"})


def _to_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapta el esquema de Pydantic al modo *strict* de OpenAI.

    Strict exige: `additionalProperties: false`, todas las propiedades en `required` (los
    opcionales se expresan como `T | None`), sin `default` y `$ref` sin claves hermanas.
    """

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        result = {
            key: visit(value) for key, value in node.items() if key not in _UNSUPPORTED_STRICT_KEYS
        }
        if result.get("type") == "object" and "properties" in result:
            result["additionalProperties"] = False
            result["required"] = list(result["properties"])
        return result

    strict: dict[str, Any] = visit(copy.deepcopy(schema))
    return strict


def _extract_json(text: str) -> str:
    """Quita vallas de Markdown (```json ... ```) si el modelo las añade."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
    return stripped.strip()


async def complete_structured[T: BaseModel](
    llm: LLMPort,
    messages: Sequence[ChatMessage],
    output_type: type[T],
    *,
    max_attempts: int = 2,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> StructuredResult[T]:
    schema = response_schema_for(output_type)
    conversation = list(messages)
    responses: list[LLMResponse] = []
    for _ in range(max_attempts):
        response = await llm.complete(
            conversation, temperature=temperature, max_tokens=max_tokens, response_schema=schema
        )
        responses.append(response)
        try:
            value = output_type.model_validate_json(_extract_json(response.content))
        except ValidationError as exc:
            errors = json.dumps(exc.errors(include_url=False, include_context=False), default=str)
            conversation += [
                ChatMessage(Role.ASSISTANT, response.content),
                ChatMessage(
                    Role.USER,
                    f"Tu respuesta no cumple el esquema {schema.name}. Errores: {errors}. "
                    "Devuelve únicamente el JSON corregido.",
                ),
            ]
            continue
        return StructuredResult(value=value, responses=responses)
    raise StructuredOutputError(f"Sin JSON válido para {schema.name} tras {max_attempts} intentos.")
