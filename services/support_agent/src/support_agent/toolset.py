"""Cliente MCP del agente: descubre las tools de varios servidores y las adapta al bucle propio.

Lo que hace por dentro el `MCPClient` de Strands, `MultiServerMCPClient` de LangChain o la tool
`mcp` de Foundry Agent Service: `tools/list` -> esquemas para el modelo; `tools/call` al ejecutar.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any

from fastmcp import Client
from mcp_types import TextContent
from mcp_types import Tool as McpToolInfo
from pydantic import BaseModel, ConfigDict

from headache_core.application.tools import Tool, ToolContext, ToolError, ToolRegistry

logger = logging.getLogger(__name__)


class RemoteArgs(BaseModel):
    """Solo exige un objeto JSON: el servidor MCP valida contra su propio esquema."""

    model_config = ConfigDict(extra="allow")


def _remote_error(text: str) -> ToolError:
    try:
        error = json.loads(text)["error"]
        return ToolError(
            str(error["message"]),
            kind=str(error.get("type", "tool_error")),
            retryable=bool(error.get("retryable", False)),
        )
    except (ValueError, KeyError, TypeError):
        return ToolError(text[:500] or "La tool remota falló.", kind="tool_error")


def _text(content: list[Any]) -> str:
    return "\n".join(block.text for block in content if isinstance(block, TextContent))


class McpToolset:
    """`async with McpToolset({"catalog": client, ...}) as registry:` -> ToolRegistry."""

    def __init__(self, clients: Mapping[str, Client[Any]], *, timeout_s: float = 15.0) -> None:
        self._clients = dict(clients)
        self._timeout_s = timeout_s
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> ToolRegistry:
        tools: list[Tool[Any]] = []
        try:
            for server, client in self._clients.items():
                await self._stack.enter_async_context(client)
                tools.extend(
                    self._adapt(server, client, info) for info in await client.list_tools()
                )
            return ToolRegistry(tools)
        except BaseException:
            await self._stack.aclose()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._stack.aclose()

    def _adapt(self, server: str, client: Client[Any], info: McpToolInfo) -> Tool[RemoteArgs]:
        # Sin anotación se asume escritura: ni paralelo ni reintentos automáticos.
        read_only = bool(info.annotations and info.annotations.read_only_hint)

        async def call(args: RemoteArgs, _: ToolContext) -> Any:
            try:
                result = await client.call_tool(info.name, args.model_dump(), raise_on_error=False)
            except Exception as exc:
                logger.warning("Fallo de transporte con el servidor MCP %s: %r", server, exc)
                raise ToolError(
                    f"El servicio '{server}' no está disponible.",
                    kind="unavailable",
                    retryable=read_only,
                ) from exc
            if result.is_error:
                raise _remote_error(_text(result.content))
            return result.structured_content if result.structured_content else _text(result.content)

        return Tool(
            info.name,
            info.description or "",
            RemoteArgs,
            call,
            read_only=read_only,
            timeout_s=self._timeout_s,
            published_schema=info.input_schema,
        )
