"""`BaseLlm` de Google ADK sobre `LLMPort`.

ADK habla el formato de Gemini (`google.genai.types`): `Content` con `role` `user`/`model` y
partes `text`, `function_call` y `function_response`. El puente traduce a nuestros
`ChatMessage` en la entrada y devuelve la respuesta del gateway como un `LlmResponse`.

- **Prompt**: ADK compone su propio `system_instruction` (instrucciones del agente + "You are an
  agent. Your internal name is ...", en inglés). El puente envía en su lugar nuestro prefijo
  (instrucciones y contexto de sesión con sus checkpoints de caché), el mismo de todos los
  sabores, igual que el puente de Strands recibe los bloques de sistema que le damos.
- **Firmas**: la *thought signature* de Gemini va en `Part.thought_signature` (bytes); en
  `ToolCall.signature`, en base64. ADK nunca la descarta (`_is_part_invisible`).
- **Resultados de tools**: `{"output": ...}` o `{"error": ...}`, la misma forma que manda
  `GeminiLLM` en `functionResponse` (6.G1).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import replace
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict, PrivateAttr

from genaipocs_core.ports import ChatMessage, LLMPort, LLMResponse, Role, ToolCall, ToolConfig
from genaipocs_core.ports.llm import ToolSpec

# ADK pasa a las tools los argumentos ya parseados: el JSON inválido viaja envuelto para que lo
# rechace nuestro ejecutor (y quede en el diario) igual que en native.
RAW_ARGUMENTS = "__raw_arguments__"
OUTPUT, ERROR = "output", "error"


def arguments_of(args: dict[str, Any] | None) -> str:
    args = args or {}
    if isinstance(raw := args.get(RAW_ARGUMENTS), str):
        return raw
    return json.dumps(args, ensure_ascii=False)


def _args_of(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {RAW_ARGUMENTS: arguments}


def _signature_bytes(signature: str | None) -> bytes | None:
    return base64.b64decode(signature) if signature is not None else None


def _signature_text(signature: bytes | None) -> str | None:
    return base64.b64encode(signature).decode("ascii") if signature is not None else None


def tool_result(output: str, *, is_error: bool) -> dict[str, Any]:
    return {ERROR if is_error else OUTPUT: output}


def _result_text(response: dict[str, Any] | None) -> tuple[str, bool]:
    response = response or {}
    if ERROR in response:
        value, is_error = response[ERROR], True
    elif OUTPUT in response:
        value, is_error = response[OUTPUT], False
    else:  # Respuesta que no es nuestra (p. ej. un error de ADK): se pasa entera.
        value, is_error = response, False
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), is_error


def call_part(call: ToolCall) -> types.Part:
    return types.Part(
        function_call=types.FunctionCall(id=call.id, name=call.name, args=_args_of(call.arguments)),
        thought_signature=_signature_bytes(call.signature),
    )


def to_contents(messages: Iterable[ChatMessage]) -> list[types.Content]:
    """Los resultados de tools consecutivos van en un único `Content` `user` (como en Gemini)."""
    contents: list[types.Content] = []
    for m in messages:
        if m.role is Role.TOOL:
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=m.tool_call_id,
                    name=_call_name(contents, m.tool_call_id),
                    response=tool_result(m.content, is_error=m.is_error),
                )
            )
            previous = contents[-1] if contents else None
            if previous is not None and any(p.function_response for p in previous.parts or []):
                (previous.parts or []).append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
            continue
        parts = [types.Part(text=m.content)] if m.content else []
        parts += [call_part(c) for c in m.tool_calls]
        role = "model" if m.role is Role.ASSISTANT else "user"
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _call_name(contents: Sequence[types.Content], call_id: str | None) -> str:
    """`FunctionResponse` exige el nombre de la tool: se busca la llamada a la que responde."""
    for content in reversed(contents):
        for part in content.parts or []:
            if part.function_call is not None and part.function_call.id == call_id:
                return part.function_call.name or ""
    return ""


def to_chat_messages(contents: Iterable[types.Content]) -> list[ChatMessage]:
    converted: list[ChatMessage] = []
    for content in contents:
        parts = content.parts or []
        if responses := [p.function_response for p in parts if p.function_response]:
            for response in responses:
                text, is_error = _result_text(response.response)
                converted.append(
                    ChatMessage(Role.TOOL, text, tool_call_id=response.id, is_error=is_error)
                )
            continue
        text = "".join(p.text for p in parts if p.text and not p.thought)
        calls = tuple(
            ToolCall(
                p.function_call.id or "",
                p.function_call.name or "",
                arguments_of(p.function_call.args),
                _signature_text(p.thought_signature),
            )
            for p in parts
            if p.function_call is not None
        )
        role = Role.ASSISTANT if content.role == "model" else Role.USER
        converted.append(ChatMessage(role, text, tool_calls=calls))
    return converted


def to_llm_response(response: LLMResponse) -> LlmResponse:
    parts = [types.Part(text=response.content)] if response.content else []
    parts += [call_part(c) for c in response.tool_calls]
    usage = response.usage
    return LlmResponse(
        content=types.Content(role="model", parts=parts),
        model_version=response.model,
        finish_reason=types.FinishReason.STOP,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage.input_tokens,
            candidates_token_count=usage.output_tokens,
            total_token_count=usage.total_tokens,
            cached_content_token_count=usage.cached_input_tokens,
        ),
    )


class GatewayLlm(BaseLlm):
    """Un modelo por turno: lleva el prefijo del turno y dónde acaba el historial previo."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _gateway: LLMPort = PrivateAttr()
    _specs: dict[str, ToolSpec] = PrivateAttr(default_factory=dict)
    _prefix: list[ChatMessage] = PrivateAttr(default_factory=list)
    _history: int = PrivateAttr(default=0)

    def __init__(
        self,
        gateway: LLMPort,
        tool_specs: Iterable[ToolSpec],
        *,
        prefix: Sequence[ChatMessage] = (),
        history: int = 0,
    ) -> None:
        # El modelo real lo elige el router del gateway en cada llamada.
        super().__init__(model="genaipocs-gateway")
        self._gateway = gateway
        self._specs = {spec.name: spec for spec in tool_specs}
        self._prefix = list(prefix)
        self._history = history

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        conversation = to_chat_messages(llm_request.contents)
        if 0 < self._history <= len(conversation):
            # Checkpoint de caché al final del historial previo, como en el resto de sabores.
            index = self._history - 1
            conversation[index] = replace(conversation[index], cache_checkpoint=True)
        # Se envía el ToolSpec del agente: la declaración de ADK es solo su copia.
        specs = tuple(self._specs[n] for n in llm_request.tools_dict if n in self._specs)
        tools = ToolConfig(specs) if specs else None
        response = await self._gateway.complete([*self._prefix, *conversation], tools=tools)
        yield to_llm_response(response)
