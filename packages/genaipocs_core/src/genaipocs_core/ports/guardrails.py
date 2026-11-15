"""Puerto de escudos de contenido: prompt injection, contenido dañino y temas vetados.

Lo implementan Azure AI Content Safety (Prompt Shields + análisis de texto), Amazon Bedrock
Guardrails (ApplyGuardrail) y un escudo heurístico local sin coste.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ShieldVerdict:
    # Intento de manipular al modelo (jailbreak en el prompt o instrucciones en un documento).
    attack: bool = False
    # Otras detecciones normalizadas: "hate", "violence", "topic:<nombre>", "pii:<tipo>"...
    categories: tuple[str, ...] = ()

    @property
    def flagged(self) -> bool:
        return self.attack or bool(self.categories)


class ShieldUnavailableError(Exception):
    """El servicio de seguridad no respondió: quien llama decide si falla cerrado o abierto."""


class ContentShieldPort(Protocol):
    async def scan_prompt(self, text: str) -> ShieldVerdict:
        """Entrada directa del usuario (jailbreak, contenido dañino)."""
        ...

    async def scan_documents(self, texts: Sequence[str]) -> list[ShieldVerdict]:
        """Texto de terceros que entra al contexto (RAG, tools): prompt injection indirecta."""
        ...

    async def scan_output(self, text: str) -> ShieldVerdict:
        """Respuesta del modelo antes de mostrarla."""
        ...
