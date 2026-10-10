"""`Model` de Strands Agents sobre `LLMPort`.

Strands habla el formato de Bedrock Converse: mensajes con bloques `text` / `toolUse` /
`toolResult` / `cachePoint` y un modelo que emite eventos de streaming. El puente traduce a
nuestros `ChatMessage` en la entrada y emite los eventos equivalentes a la respuesta del gateway.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable, Iterable, Sequence
from typing import Any, Literal, TypeVar, cast

from pydantic import BaseModel
from strands.models import Model
from strands.types.content import ContentBlock, Messages, SystemContentBlock
from strands.types.content import Message as StrandsMessage
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolChoiceTool, ToolResult
from strands.types.tools import ToolSpec as StrandsToolSpec

from headache_core.ports import ChatMessage, LLMPort, Role, ToolCall, ToolConfig
from headache_core.ports.llm import ToolSpec

type ToolChoiceToolDict = dict[Literal["tool"], ToolChoiceTool]

# Strands sustituye en silencio el JSON inválido por {}: el puente lo envuelve para que el
# ejecutor lo rechace (y quede en el diario) igual que en el sabor native.
RAW_ARGUMENTS = "__raw_arguments__"
CACHE_POINT: ContentBlock = {"cachePoint": {"type": "default"}}

T = TypeVar("T", bound=BaseModel)


def tool_spec(spec: ToolSpec) -> StrandsToolSpec:
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": {"json": dict(spec.json_schema)},
    }


def arguments_of(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        if isinstance(raw := tool_input.get(RAW_ARGUMENTS), str):
            return raw
        return json.dumps(tool_input, ensure_ascii=False)
    return str(tool_input)


def _input_of(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {RAW_ARGUMENTS: arguments}


def system_blocks(prefix: Iterable[ChatMessage]) -> list[SystemContentBlock]:
    blocks: list[SystemContentBlock] = []
    for m in prefix:
        blocks.append({"text": m.content})
        if m.cache_checkpoint:
            blocks.append({"cachePoint": {"type": "default"}})
    return blocks


def _from_system(blocks: Sequence[SystemContentBlock]) -> list[ChatMessage]:
    converted: list[ChatMessage] = []
    for block in blocks:
        if "text" in block:
            converted.append(ChatMessage(Role.SYSTEM, block["text"]))
        elif "cachePoint" in block and converted:
            last = converted[-1]
            converted[-1] = ChatMessage(last.role, last.content, cache_checkpoint=True)
    return converted


def _result_text(result: ToolResult) -> str:
    return "".join(c.get("text", "") for c in result["content"])


def to_chat_messages(messages: Messages) -> list[ChatMessage]:
    converted: list[ChatMessage] = []
    for m in messages:
        blocks = m["content"]
        checkpoint = any("cachePoint" in b for b in blocks)
        if results := [b["toolResult"] for b in blocks if "toolResult" in b]:
            converted.extend(
                ChatMessage(
                    Role.TOOL,
                    _result_text(r),
                    tool_call_id=r["toolUseId"],
                    is_error=r.get("status") == "error",
                )
                for r in results
            )
            if checkpoint:
                last = converted[-1]
                converted[-1] = ChatMessage(
                    last.role,
                    last.content,
                    cache_checkpoint=True,
                    tool_call_id=last.tool_call_id,
                    is_error=last.is_error,
                )
            continue
        text = "".join(b.get("text", "") for b in blocks)
        calls = tuple(
            ToolCall(
                b["toolUse"]["toolUseId"], b["toolUse"]["name"], arguments_of(b["toolUse"]["input"])
            )
            for b in blocks
            if "toolUse" in b
        )
        converted.append(ChatMessage(Role(m["role"]), text, checkpoint, tool_calls=calls))
    return converted


def to_strands_messages(messages: Iterable[ChatMessage]) -> Messages:
    """Los resultados de tools consecutivos van en un único mensaje `user` (Converse)."""
    converted: Messages = []
    for m in messages:
        if m.role is Role.TOOL:
            block: ContentBlock = {
                "toolResult": {
                    "toolUseId": m.tool_call_id or "",
                    "status": "error" if m.is_error else "success",
                    "content": [{"text": m.content}],
                }
            }
            previous = converted[-1] if converted else None
            if previous is not None and any("toolResult" in b for b in previous["content"]):
                previous["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
        else:
            content: list[ContentBlock] = [{"text": m.content}] if m.content else []
            content += [
                {"toolUse": {"toolUseId": c.id, "name": c.name, "input": _input_of(c.arguments)}}
                for c in m.tool_calls
            ]
            role: Literal["user", "assistant"] = "assistant" if m.role is Role.ASSISTANT else "user"
            converted.append({"role": role, "content": content})
        if m.cache_checkpoint:
            converted[-1]["content"].append(CACHE_POINT)
    return converted


def _choice(tool_choice: ToolChoice | None) -> str:
    if tool_choice is None or "auto" in tool_choice:
        return "auto"
    if "any" in tool_choice:
        return "required"
    return str(cast(ToolChoiceToolDict, tool_choice)["tool"]["name"])


class GatewayModel(Model):
    def __init__(self, gateway: LLMPort, tool_specs: Iterable[ToolSpec]) -> None:
        self._gateway = gateway
        self._specs = {spec.name: spec for spec in tool_specs}

    def get_config(self) -> dict[str, Any]:
        return {"model_id": "headache-gateway"}

    def update_config(self, **model_config: Any) -> None:
        raise NotImplementedError("La configuración del modelo vive en el gateway.")

    def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        raise NotImplementedError("El agente no usa salida estructurada de Strands.")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[StrandsToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        prefix = _from_system(system_prompt_content or [])
        if not prefix and system_prompt:
            prefix = [ChatMessage(Role.SYSTEM, system_prompt, cache_checkpoint=True)]
        # Se envía el ToolSpec del agente: el esquema de Strands es solo su copia.
        specs = tuple(self._specs[t["name"]] for t in tool_specs or [] if t["name"] in self._specs)
        tools = ToolConfig(specs, choice=_choice(tool_choice)) if specs else None
        response = await self._gateway.complete([*prefix, *to_chat_messages(messages)], tools=tools)
        yield {"messageStart": {"role": "assistant"}}
        if response.content:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": response.content}}}
            yield {"contentBlockStop": {}}
        for call in response.tool_calls:
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": call.id, "name": call.name}}
                }
            }
            tool_input = json.dumps(_input_of(call.arguments), ensure_ascii=False)
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": tool_input}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if response.tool_calls else "end_turn"}}
        usage = response.usage
        yield cast(
            StreamEvent,
            {
                "metadata": {
                    "usage": {
                        "inputTokens": usage.input_tokens,
                        "outputTokens": usage.output_tokens,
                        "totalTokens": usage.total_tokens,
                        "cacheReadInputTokens": usage.cached_input_tokens,
                        "cacheWriteInputTokens": usage.cache_write_tokens,
                    },
                    "metrics": {"latencyMs": int(response.latency_ms)},
                }
            },
        )


def message_text(message: StrandsMessage) -> str:
    return "".join(b.get("text", "") for b in message["content"])
