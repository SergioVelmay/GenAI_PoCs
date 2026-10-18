"""Carril A (Azure): agente servidor en Foundry Agent Service.

- El agente (instrucciones + tools) se define UNA vez en el servicio (`agent_definition`,
  aprovisionado con `scripts/provision_foundry_agent.py`); cada turno solo envía entradas.
- El historial vive en una `conversation` del servicio: se añade, nunca se reescribe. No hay
  compactación nuestra (el servicio trunca por su cuenta) y el contexto de sesión entra como
  mensaje `developer` en cada turno.
- Las tools son *function tools*: el servicio devuelve `function_call` y las ejecuta el cliente
  (nuestro `JournaledExecutor`), así la identidad y las escrituras nunca salen del backend.

`LocalAgentService` emula el servicio con la misma definición y el `LLMPort`: permite ejecutar
la suite sin nube. `FoundryAgentService` es el cliente real (Responses API sobre el proyecto).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING, Any, Protocol, cast

from headache_core.application.tools import ToolRegistry
from headache_core.ports import ChatMessage, LLMPort, Role, TokenUsage, ToolCall, ToolConfig
from headache_core.ports.llm import ToolSpec

if TYPE_CHECKING:
    from openai import AsyncOpenAI

type Item = dict[str, Any]

RESPONSE_KEY = "response_id"


@dataclass(frozen=True, slots=True)
class ServiceReply:
    text: str
    calls: tuple[ToolCall, ...]
    # Tokens que facturó el servicio en esta respuesta (el historial lo gestiona él).
    usage: TokenUsage | None = None


class AgentServiceClient(Protocol):
    async def create_conversation(self) -> str: ...

    async def append(self, conversation_id: str, items: Sequence[Item]) -> None: ...

    async def respond(self, conversation_id: str, items: Sequence[Item]) -> ServiceReply: ...


def agent_definition(instructions: str, tools: ToolRegistry, *, model: str) -> dict[str, Any]:
    return {
        "kind": "prompt",
        "model": model,
        "instructions": instructions,
        "tools": [
            {
                "type": "function",
                "name": spec.name,
                "description": spec.description,
                "parameters": dict(spec.json_schema),
                "strict": spec.strict,
            }
            for spec in (tool.spec() for tool in tools)
        ],
    }


def message(role: str, text: str) -> Item:
    return {"type": "message", "role": role, "content": text}


def function_output(call_id: str, output: str) -> Item:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


_ROLES = {"developer": Role.SYSTEM, "system": Role.SYSTEM, "user": Role.USER}


def items_to_messages(items: Sequence[Item]) -> list[ChatMessage]:
    """Los elementos que produjo una misma respuesta del modelo forman un único ASSISTANT."""
    messages: list[ChatMessage] = []
    previous: Item | None = None
    for item in items:
        same_response = (
            previous is not None
            and item.get(RESPONSE_KEY) is not None
            and previous.get(RESPONSE_KEY) == item.get(RESPONSE_KEY)
        )
        if item["type"] == "function_call":
            call = ToolCall(item["call_id"], item["name"], item["arguments"])
            if same_response:
                last = messages[-1]
                messages[-1] = ChatMessage(
                    Role.ASSISTANT, last.content, tool_calls=(*last.tool_calls, call)
                )
            else:
                messages.append(ChatMessage(Role.ASSISTANT, "", tool_calls=(call,)))
        elif item["type"] == "function_call_output":
            messages.append(ChatMessage(Role.TOOL, item["output"], tool_call_id=item["call_id"]))
        else:
            messages.append(ChatMessage(_ROLES.get(item["role"], Role.ASSISTANT), item["content"]))
        previous = item
    return messages


class LocalAgentService:
    """Emulador del servicio: conversación en memoria + definición del agente + `LLMPort`."""

    def __init__(self, llm: LLMPort, definition: dict[str, Any]) -> None:
        self._llm = llm
        self._instructions: str = definition["instructions"]
        specs = tuple(
            ToolSpec(t["name"], t["description"], t["parameters"], strict=t["strict"])
            for t in definition["tools"]
        )
        self._tools = ToolConfig(specs) if specs else None
        self._ids = count(1)
        self.conversations: dict[str, list[Item]] = {}

    async def create_conversation(self) -> str:
        conversation_id = f"conv_{next(self._ids)}"
        self.conversations[conversation_id] = []
        return conversation_id

    async def append(self, conversation_id: str, items: Sequence[Item]) -> None:
        self.conversations[conversation_id].extend(items)

    async def respond(self, conversation_id: str, items: Sequence[Item]) -> ServiceReply:
        conversation = self.conversations[conversation_id]
        conversation.extend(items)
        prompt = [
            ChatMessage(Role.SYSTEM, self._instructions, cache_checkpoint=True),
            *items_to_messages(conversation),
        ]
        response = await self._llm.complete(prompt, tools=self._tools)
        response_id = f"resp_{next(self._ids)}"
        if response.content:
            reply = message("assistant", response.content)
            conversation.append({**reply, RESPONSE_KEY: response_id})
        conversation.extend(
            {
                "type": "function_call",
                "call_id": c.id,
                "name": c.name,
                "arguments": c.arguments,
                RESPONSE_KEY: response_id,
            }
            for c in response.tool_calls
        )
        return ServiceReply(response.content, response.tool_calls, response.usage)


AI_FOUNDRY_SCOPE = "https://ai.azure.com/.default"
BOOKING_AGENT_NAME = "headache-booking-agent"


class FoundryAgentService:
    """Cliente real: `AsyncOpenAI` con `base_url={proyecto}/openai/v1` y token de Entra ID."""

    def __init__(self, client: AsyncOpenAI, agent_name: str) -> None:
        self._client = client
        self._agent = {"name": agent_name, "type": "agent_reference"}

    @classmethod
    def from_project(
        cls, project_endpoint: str, credential: Any, agent_name: str = BOOKING_AGENT_NAME
    ) -> FoundryAgentService:
        """`credential`: credencial asíncrona de `azure.identity.aio` (la cierra quien la crea)."""
        from azure.identity.aio import get_bearer_token_provider
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=f"{project_endpoint.rstrip('/')}/openai/v1",
            api_key=get_bearer_token_provider(credential, AI_FOUNDRY_SCOPE),
        )
        return cls(client, agent_name)

    async def close(self) -> None:
        await self._client.close()

    async def create_conversation(self) -> str:
        return (await self._client.conversations.create()).id

    async def append(self, conversation_id: str, items: Sequence[Item]) -> None:
        await self._client.conversations.items.create(conversation_id, items=cast(Any, items))

    async def respond(self, conversation_id: str, items: Sequence[Item]) -> ServiceReply:
        response = await self._client.responses.create(
            input=cast(Any, list(items)),
            conversation=conversation_id,
            extra_body={"agent_reference": self._agent},
        )
        calls = tuple(
            ToolCall(item.call_id, item.name, item.arguments)
            for item in response.output
            if item.type == "function_call"
        )
        usage = None
        if response.usage is not None:
            details = response.usage.input_tokens_details
            usage = TokenUsage(
                response.usage.input_tokens,
                response.usage.output_tokens,
                cached_input_tokens=details.cached_tokens if details is not None else 0,
            )
        return ServiceReply(response.output_text, calls, usage)
