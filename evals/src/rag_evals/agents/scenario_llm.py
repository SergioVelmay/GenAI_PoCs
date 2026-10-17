"""Modelo guionizado: decide como un LLM, pero de forma determinista y sin coste.

No evalúa la inteligencia del modelo, sino el *harness* de cada framework:
- ¿llegan las tools, el historial, el resumen y las preferencias al modelo? Los marcadores
  {tool:...}, {memory:...} y {seen:...} solo se resuelven con lo que el framework le envía; si
  algo se pierde, la respuesta contiene "<?>" y el escenario falla.
- ¿vuelven los resultados de las tools en el formato correcto y en el turno correcto?
- ¿cuánto prompt se reutiliza entre llamadas? Simula una caché de prefijo ideal (como la
  automática de OpenAI, sin mínimo de 1024 tokens) para comparar el orden del prompt.

En modo `llm` este modelo no se usa: decide el modelo real y la suite mide lo mismo.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from headache_core.application.agents import SUMMARIZER_PROMPT, estimate_tokens
from headache_core.ports import (
    ChatMessage,
    LLMResponse,
    ResponseSchema,
    Role,
    TokenUsage,
    ToolCall,
    ToolConfig,
)
from rag_evals.agents.scenarios import Scenario, ScenarioTurn

UNRESOLVED = "<?>"
_MARKER = re.compile(r"\{(tool|memory|seen):([^{}]+)\}")


def _tool_results(messages: Sequence[ChatMessage]) -> list[tuple[str, Any]]:
    """(nombre de tool, salida JSON) de los resultados visibles sin error, del más reciente al
    más antiguo."""
    names = {c.id: c.name for m in messages for c in m.tool_calls}
    results: list[tuple[str, Any]] = []
    for message in reversed(messages):
        if message.role is not Role.TOOL or message.is_error or message.tool_call_id is None:
            continue
        try:
            value = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "error" in value:
            continue
        results.append((names.get(message.tool_call_id, ""), value))
    return results


def _navigate(value: Any, path: Sequence[str]) -> Any:
    for part in path:
        # Las listas llegan como {"items": [...], "returned", "total"} (render_tool_output).
        if isinstance(value, dict) and part.isdigit() and isinstance(value.get("items"), list):
            value = value["items"]
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return UNRESOLVED
    return value


def resolve(template: str, messages: Sequence[ChatMessage]) -> str:
    def replace(match: re.Match[str]) -> str:
        kind, ref = match.group(1), match.group(2)
        if kind == "tool":
            name, *path = ref.split(".")
            for tool, value in _tool_results(messages):
                if tool == name:
                    return str(_navigate(value, path))
            return UNRESOLVED
        if kind == "memory":
            line = re.compile(rf"^- {re.escape(ref)}: (.+)$", re.MULTILINE)
            for message in messages:
                if message.role is Role.SYSTEM and (found := line.search(message.content)):
                    return found.group(1).strip()
            return UNRESOLVED
        visible = any(ref in m.content for m in messages)
        return ref if visible else UNRESOLVED

    return _MARKER.sub(replace, template)


def _resolve_arguments(value: Any, messages: Sequence[ChatMessage]) -> Any:
    if isinstance(value, str):
        return resolve(value, messages)
    if isinstance(value, dict):
        return {k: _resolve_arguments(v, messages) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_arguments(v, messages) for v in value]
    return value


def serialize_prompt(messages: Sequence[ChatMessage], tools: ToolConfig | None) -> str:
    """Lo que el proveedor ve, en orden: tools primero y después los mensajes."""
    parts = [
        json.dumps([[t.name, t.description, t.json_schema] for t in tools.tools], sort_keys=True)
        if tools
        else ""
    ]
    for m in messages:
        calls = "".join(f"[{c.name} {c.arguments}]" for c in m.tool_calls)
        parts.append(f"<{m.role.value}|{m.tool_call_id or ''}>{m.content}{calls}")
    return "\n".join(parts)


def _common_prefix(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    index = 0
    while index < limit and a[index] == b[index]:
        index += 1
    return index


class ScenarioLLM:
    def __init__(self, scenario: Scenario, model: str = "scenario-llm") -> None:
        self._turns: dict[str, ScenarioTurn] = {t.user.strip(): t for t in scenario.turns}
        self._turns |= {t.model_sees.strip(): t for t in scenario.turns if t.model_sees}
        self._model = model
        self._prompts: list[str] = []
        self._ids = 0

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        prompt = serialize_prompt(messages, tools)
        cached = max((_common_prefix(prompt, p) for p in self._prompts), default=0)
        self._prompts.append(prompt)
        calls: tuple[ToolCall, ...] = ()
        if messages and messages[0].content == SUMMARIZER_PROMPT:
            content = self._summarize(messages)
        else:
            content, calls = self._decide(messages)
        output = content + "".join(c.name + c.arguments for c in calls)
        return LLMResponse(
            content=content,
            model=self._model,
            usage=TokenUsage(
                input_tokens=estimate_tokens(prompt),
                output_tokens=estimate_tokens(output),
                cached_input_tokens=estimate_tokens(prompt[:cached]),
            ),
            provider="scenario",
            finish_reason="tool_calls" if calls else "stop",
            tool_calls=calls,
        )

    def _decide(self, messages: Sequence[ChatMessage]) -> tuple[str, tuple[ToolCall, ...]]:
        last_user = max((i for i, m in enumerate(messages) if m.role is Role.USER), default=-1)
        turn = self._turns.get(messages[last_user].content.strip()) if last_user >= 0 else None
        if turn is None:
            return f"{UNRESOLVED} turno desconocido", ()
        step = sum(
            1 for m in messages[last_user + 1 :] if m.role is Role.ASSISTANT and m.tool_calls
        )
        if step >= len(turn.plan):
            return resolve(turn.final, messages), ()
        calls: list[ToolCall] = []
        for planned in turn.plan[step]:
            if planned.when_seen and not any(planned.when_seen in m.content for m in messages):
                continue
            self._ids += 1
            arguments = _resolve_arguments(planned.arguments, messages)
            calls.append(
                ToolCall(
                    f"call_{self._ids}", planned.name, json.dumps(arguments, ensure_ascii=False)
                )
            )
        if not calls:
            return resolve(turn.final, messages), ()
        return "", tuple(calls)

    @staticmethod
    def _summarize(messages: Sequence[ChatMessage]) -> str:
        """Resumen extractivo: resumen previo + lo que dijo el cliente en lo expulsado."""
        text = messages[-1].content
        previous = text.split("Resumen previo:\n", 1)[-1].split("\n\nConversación a incorporar:")[0]
        said = [line[len("USER: ") :] for line in text.splitlines() if line.startswith("USER: ")]
        parts = [] if previous == "(ninguno)" else [previous]
        return " | ".join([*parts, *said])
