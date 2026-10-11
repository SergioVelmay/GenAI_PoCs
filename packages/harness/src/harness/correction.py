"""Bucle de autocorrección: generar -> validar -> si falla, devolver el feedback y reintentar.

    intento 1: LLM -> JSON -> esquema -> reglas  ✗ -> "corrige: [amount_exceeds_paid] ..."
    intento 2: LLM -> JSON -> esquema -> reglas  ✓ -> valor validado
    ... tras N intentos (o una violación no corregible) -> escalar a un humano.

El modelo nunca decide si su salida es válida: lo decide código determinista. El LLM solo recibe
el feedback para intentarlo de nuevo, y cada reintento cuenta (coste y latencia).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from harness.validation import Validator, Violation, parse_output, render_feedback, run_validators
from headache_core.application.llm import response_schema_for
from headache_core.ports import ChatMessage, LLMPort, LLMResponse, Role


@dataclass(frozen=True, slots=True)
class Attempt:
    raw: str
    violations: tuple[Violation, ...]


@dataclass(frozen=True, slots=True)
class CorrectionResult[T]:
    # None = escalado: ningún intento superó la validación.
    value: T | None
    attempts: list[Attempt] = field(default_factory=list)
    responses: list[LLMResponse] = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return self.value is None

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Las del último intento: lo que verá quien reciba el caso escalado."""
        return self.attempts[-1].violations if self.attempts else ()


def correction_feedback(violations: Sequence[Violation], attempt: int, max_attempts: int) -> str:
    return (
        f"Tu respuesta no supera la validación (intento {attempt} de {max_attempts}). "
        "Corrige exactamente estos problemas y devuelve solo el JSON completo:\n"
        + render_feedback(violations)
    )


async def generate_validated[T: BaseModel, C](
    llm: LLMPort,
    messages: Sequence[ChatMessage],
    output_type: type[T],
    *,
    validators: Sequence[Validator[T, C]],
    context: C,
    max_attempts: int = 3,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> CorrectionResult[T]:
    if max_attempts < 1:
        raise ValueError("max_attempts debe ser >= 1.")
    schema = response_schema_for(output_type)
    conversation = list(messages)
    attempts: list[Attempt] = []
    responses: list[LLMResponse] = []
    for number in range(1, max_attempts + 1):
        response = await llm.complete(
            conversation, temperature=temperature, max_tokens=max_tokens, response_schema=schema
        )
        responses.append(response)
        value, violations = parse_output(response.content, output_type)
        if value is not None:
            violations = await run_validators(validators, value, context)
        attempts.append(Attempt(response.content, tuple(violations)))
        if not violations:
            return CorrectionResult(value, attempts, responses)
        if any(not v.correctable for v in violations):
            break
        conversation += [
            ChatMessage(Role.ASSISTANT, response.content),
            ChatMessage(Role.USER, correction_feedback(violations, number, max_attempts)),
        ]
    return CorrectionResult(None, attempts, responses)
