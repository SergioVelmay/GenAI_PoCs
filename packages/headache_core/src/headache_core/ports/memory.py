"""Puertos de memoria del agente: hilos (corto plazo) y preferencias (largo plazo)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from headache_core.ports.llm import ChatMessage


class ThreadAccessError(Exception):
    """El hilo pertenece a otro cliente: se trata igual que si no existiera."""


class CheckpointConflictError(Exception):
    """Otra petición guardó el hilo antes (dos turnos concurrentes del mismo hilo)."""


@dataclass(frozen=True, slots=True)
class ThreadState:
    thread_id: str
    customer_id: str
    messages: tuple[ChatMessage, ...] = ()
    # Resumen de los intercambios ya expulsados de la ventana (memoria a corto plazo comprimida).
    summary: str = ""
    # Concurrencia optimista: cada guardado debe partir de la última versión leída.
    version: int = 0


class CheckpointStore(Protocol):
    async def load(self, thread_id: str) -> ThreadState | None: ...

    async def save(self, state: ThreadState, *, expected_version: int) -> ThreadState: ...


class PreferenceStore(Protocol):
    async def get(self, customer_id: str) -> dict[str, str]: ...

    async def put(self, customer_id: str, key: str, value: str) -> None: ...
