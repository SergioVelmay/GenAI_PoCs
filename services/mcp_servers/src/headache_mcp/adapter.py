"""Puente entre nuestras tools (`ToolRegistry`) y FastMCP.

El servidor MCP es solo un transporte: valida, ejecuta, tipa errores y sanea con el mismo
`ToolExecutor` que el bucle local. Así, una tool se comporta igual llamada en proceso o por MCP.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools.base import Tool as MCPTool
from fastmcp.tools.base import ToolResult
from mcp_types import ToolAnnotations
from pydantic import ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from headache_core.application.tools import (
    ToolContext,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from headache_core.ports import ToolCall

type IdentityResolver = Callable[[], ToolContext]
type Lifespan = Callable[[FastMCP[Any]], AbstractAsyncContextManager[Any]]


def anonymous() -> ToolContext:
    """Para servidores sin datos personales (catálogo)."""
    return ToolContext(customer_id="")


def fixed_identity(customer_id: str) -> IdentityResolver:
    """Solo para desarrollo local sin OAuth: todas las llamadas actúan como este cliente."""

    def resolve() -> ToolContext:
        if not customer_id:
            raise ToolError("Petición no autenticada.", kind="unauthenticated")
        return ToolContext(customer_id=customer_id)

    return resolve


class RegistryTool(MCPTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    executor: Annotated[SkipJsonSchema[ToolExecutor], Field(exclude=True)]
    identity: Annotated[SkipJsonSchema[IdentityResolver], Field(exclude=True)]

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        call = ToolCall(id="mcp", name=self.name, arguments=json.dumps(arguments))
        try:
            context = self.identity()
        except ToolError as exc:
            execution = ToolExecutor.failure(call, exc.kind, exc.message, 0.0)
        else:
            execution = await self.executor.execute(call, context)
        # La salida ya viene saneada y en JSON: texto para el modelo y estructura para código.
        payload = json.loads(execution.output)
        return ToolResult(
            content=execution.output,
            structured_content=payload if isinstance(payload, dict) else None,
            is_error=execution.is_error,
        )


def build_server(
    name: str,
    instructions: str,
    registry: ToolRegistry,
    *,
    identity: IdentityResolver,
    lifespan: Lifespan | None = None,
) -> FastMCP[Any]:
    server: FastMCP[Any] = FastMCP(
        name,
        instructions,
        version="0.1.0",
        lifespan=lifespan,
        mask_error_details=True,
    )
    executor = ToolExecutor(registry)
    for tool in registry:
        spec = tool.spec()
        server.add_tool(
            RegistryTool(
                name=spec.name,
                description=spec.description,
                parameters=dict(spec.json_schema),
                annotations=ToolAnnotations(
                    read_only_hint=tool.read_only,
                    destructive_hint=not tool.read_only,
                    idempotent_hint=tool.read_only,
                    open_world_hint=False,
                ),
                executor=executor,
                identity=identity,
            )
        )

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "server": name})

    return server
