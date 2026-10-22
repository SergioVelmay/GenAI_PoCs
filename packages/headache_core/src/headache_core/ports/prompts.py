"""Puerto del registro externo de prompts (fase 12.6): Langfuse hoy; mañana otro.

Solo se escribe en él desde la publicación (`rag_evals.llmops.prompts publish`): en ejecución
los prompts se leen del paquete, nunca de aquí.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PublishedPrompt:
    name: str
    version: int
    text: str


class PromptStorePort(Protocol):
    def latest(self, name: str) -> PublishedPrompt | None:
        """La última versión publicada; None si el prompt no existe."""
        ...

    def create(
        self,
        name: str,
        text: str,
        *,
        labels: Sequence[str],
        config: Mapping[str, object],
        commit_message: str,
    ) -> int:
        """Crea una versión nueva (el almacén la numera) y devuelve su número."""
        ...
