"""Tool de memoria a largo plazo: el modelo propone guardar una preferencia; el dominio valida."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from headache_core.application.tools.registry import Tool, ToolContext, ToolError
from headache_core.domain.preferences import (
    InvalidPreferenceError,
    PreferenceKey,
    normalize_preference,
)
from headache_core.ports.memory import PreferenceStore


class RememberPreferenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: PreferenceKey = Field(description="Qué preferencia guardar.")
    value: str = Field(
        max_length=60,
        description="language: 'es'|'en'; home_city: ciudad en inglés; favorite_category: "
        "una categoría del catálogo.",
    )


class RememberedView(BaseModel):
    key: str
    value: str


def build_memory_tools(store: PreferenceStore) -> list[Tool[RememberPreferenceArgs]]:
    async def remember_preference(
        args: RememberPreferenceArgs, context: ToolContext
    ) -> RememberedView:
        try:
            value = normalize_preference(args.key, args.value)
        except InvalidPreferenceError as exc:
            raise ToolError(str(exc), kind="invalid_preference") from exc
        await store.put(context.customer_id, args.key.value, value)
        return RememberedView(key=args.key.value, value=value)

    return [
        Tool(
            "remember_preference",
            "Guarda una preferencia del cliente para futuras conversaciones. Úsala solo cuando "
            "el cliente la declare de forma explícita (p. ej. 'vivo en Lisboa').",
            RememberPreferenceArgs,
            remember_preference,
            read_only=False,
        )
    ]
