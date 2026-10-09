"""Tools tipadas: el esquema que ve el modelo sale del mismo modelo Pydantic que valida."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from headache_core.application.llm.structured import strict_json_schema
from headache_core.ports import ToolConfig, ToolSpec

_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # Restricción común de OpenAI y Bedrock.


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Identidad que pone el backend autenticado. Nunca es un argumento que elija el modelo."""

    customer_id: str
    request_id: str = ""


class ToolError(Exception):
    """Fallo esperado. `message` es seguro para el modelo (sin trazas ni datos internos)."""

    def __init__(self, message: str, *, kind: str = "tool_error", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Tool[A: BaseModel]:
    name: str
    description: str
    args_model: type[A]
    # Devuelve modelos Pydantic (proyección de lo que el modelo puede ver) o JSON.
    handler: Callable[[A, ToolContext], Awaitable[Any]]
    # Las escrituras no se ejecutan en paralelo y un timeout en ellas es "resultado incierto".
    read_only: bool = True
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"Nombre de tool no válido: {self.name!r}")

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, strict_json_schema(self.args_model))

    def parse(self, arguments: str) -> A:
        return self.args_model.model_validate_json(arguments or "{}")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool[Any]]) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Tool duplicada: {tool.name}")
            self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def __iter__(self) -> Iterator[Tool[Any]]:
        return iter(self._tools.values())

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        """Solo las tools indicadas: cada servidor MCP expone su contexto acotado."""
        return ToolRegistry(self._tools[name] for name in names)

    def config(self, *, choice: str = "auto", parallel: bool = True) -> ToolConfig:
        return ToolConfig(
            tuple(t.spec() for t in self._tools.values()), choice=choice, parallel=parallel
        )
