"""Sessions y Memory Bank de Agent Runtime como `CheckpointStore` y `PreferenceStore` (7.G3).

El equivalente de AgentCore Memory (eventos + registros a largo plazo) en GCP. Los dos cuelgan
de una instancia de Agent Runtime (`reasoningEngines/<id>`), que puede ser *standalone*, sin
código desplegado (`agent_platform.tf`, 7.G4). Cliente: `agentplatform.Client(...).aio`.

- **Hilos (Sessions)**: una sesión por hilo (`sessions/<id derivado del hilo>`) y un evento por
  guardado con la instantánea completa en `actions.state_delta` (`thread` + `version`): el
  resumen y la compactación son nuestros, como en AgentCore. Se lee el evento de mayor versión.
  El `user_id` de la sesión es el cliente (Sessions lo exige); el dueño lo comprueba
  `load_thread` igualmente.
- **Concurrencia optimista**: se relee la última versión antes de escribir. Sessions no tiene
  un `clientToken` como `CreateEvent` de AgentCore, así que entre la lectura y la escritura
  queda una ventana: dos turnos simultáneos del mismo hilo podrían escribir la misma versión
  (la lectura se queda con el primero). Un hilo es la conversación de un cliente, con turnos
  secuenciales desde la app; si hiciera falta, un lock por hilo en Valkey (fase 10).
- **Preferencias (Memory Bank)**: escritura **directa** (`memories.create`, sin extracción por
  LLM) con el scope `{"customer_id": ...}`, como la tool explícita `remember_preference`. Cada
  hecho es `clave=valor` de la lista cerrada del dominio; lo que no encaja (p. ej. memorias
  que generara la extracción automática) se ignora al leer. Cambiar una preferencia borra la
  anterior de esa clave y crea la nueva.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from genaipocs_core.adapters.thread_document import thread_document, thread_from_document
from genaipocs_core.domain.preferences import PreferenceKey
from genaipocs_core.ports.memory import CheckpointConflictError, ThreadState

if TYPE_CHECKING:
    from genaipocs_core.config import Settings

APP_AUTHOR = "genaipocs-booking-agent"
THREAD_KEY, VERSION_KEY = "thread", "version"
NOT_FOUND = 404


class SessionEventsClient(Protocol):
    """Lo que el adaptador usa de `Client.aio.sessions.events`."""

    async def append(
        self, *, name: str, author: str, invocation_id: str, timestamp: datetime, config: Any
    ) -> Any: ...

    async def list(self, *, name: str, config: Any = None) -> Any: ...


class SessionsClient(Protocol):
    """Lo que el adaptador usa de `Client.aio.sessions`."""

    @property
    def events(self) -> SessionEventsClient: ...

    async def create(self, *, name: str, user_id: str, config: Any = None) -> Any: ...


class MemoriesClient(Protocol):
    """Lo que el adaptador usa de `Client.aio.memory_banks.memories`."""

    async def create(
        self, *, name: str, fact: str, scope: dict[str, str], config: Any = None
    ) -> Any: ...

    async def retrieve(
        self, *, name: str, scope: dict[str, str], simple_retrieval_params: Any = None
    ) -> Any: ...

    async def delete(self, *, name: str, config: Any = None) -> Any: ...


def session_id(thread_id: str) -> str:
    """El hilo es texto libre; el id de la sesión, letras minúsculas, dígitos y guiones."""
    return f"thread-{hashlib.sha256(thread_id.encode()).hexdigest()[:40]}"


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "code", None) == NOT_FOUND


def _version(event: Any) -> int:
    delta = (event.actions.state_delta if event.actions else None) or {}
    return int(delta.get(VERSION_KEY, 0))


class SessionsCheckpointStore:
    def __init__(
        self,
        sessions: SessionsClient,
        engine: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._clock = clock

    def _session(self, thread_id: str) -> str:
        return f"{self._engine}/sessions/{session_id(thread_id)}"

    async def _latest(self, thread_id: str) -> Any | None:
        """El evento de mayor versión, o None si la sesión aún no existe."""
        latest: Any | None = None
        try:
            async for event in await self._sessions.events.list(name=self._session(thread_id)):
                if latest is None or _version(event) > _version(latest):
                    latest = event
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise
        return latest

    async def load(self, thread_id: str) -> ThreadState | None:
        event = await self._latest(thread_id)
        if event is None:
            return None
        return thread_from_document(event.actions.state_delta[THREAD_KEY], _version(event))

    async def save(self, state: ThreadState, *, expected_version: int) -> ThreadState:
        latest = await self._latest(state.thread_id)
        if (_version(latest) if latest is not None else 0) != expected_version:
            raise CheckpointConflictError(f"El hilo {state.thread_id} ha cambiado.")
        if latest is None and expected_version == 0:
            await self._sessions.create(
                name=self._engine,
                user_id=state.customer_id,
                config={"session_id": session_id(state.thread_id), "wait_for_completion": True},
            )
        version = expected_version + 1
        await self._sessions.events.append(
            name=self._session(state.thread_id),
            author=APP_AUTHOR,
            invocation_id=f"{session_id(state.thread_id)}:{version}",
            timestamp=self._clock(),
            config={
                "actions": {
                    "state_delta": {THREAD_KEY: thread_document(state), VERSION_KEY: version}
                }
            },
        )
        return ThreadState(
            thread_id=state.thread_id,
            customer_id=state.customer_id,
            messages=state.messages,
            summary=state.summary,
            version=version,
        )


def preference_scope(customer_id: str) -> dict[str, str]:
    return {"customer_id": customer_id}


def preference_fact(key: str, value: str) -> str:
    return f"{key}={value}"


def parse_fact(fact: str | None) -> tuple[str, str] | None:
    """`clave=valor` de la lista cerrada; cualquier otro hecho no es una preferencia nuestra."""
    key, separator, value = (fact or "").partition("=")
    if not separator or key not in PreferenceKey.__members__.values():
        return None
    return key, value


class MemoryBankPreferenceStore:
    def __init__(self, memories: MemoriesClient, engine: str) -> None:
        self._memories = memories
        self._engine = engine

    async def _memories_of(self, customer_id: str) -> list[Any]:
        response = await self._memories.retrieve(
            name=self._engine,
            scope=preference_scope(customer_id),
            simple_retrieval_params={"page_size": 100},
        )
        return [item.memory async for item in response if item.memory is not None]

    async def get(self, customer_id: str) -> dict[str, str]:
        found: list[tuple[datetime, str, str]] = []
        for memory in await self._memories_of(customer_id):
            if (parsed := parse_fact(memory.fact)) is not None:
                updated = memory.update_time or datetime.min.replace(tzinfo=UTC)
                found.append((updated, *parsed))
        # Si quedara más de una de la misma clave, gana la más reciente.
        return {key: value for _, key, value in sorted(found, key=lambda f: f[0])}

    async def put(self, customer_id: str, key: str, value: str) -> None:
        for memory in await self._memories_of(customer_id):
            if (parsed := parse_fact(memory.fact)) is not None and parsed[0] == key:
                await self._memories.delete(name=memory.name, config={"wait_for_completion": True})
        await self._memories.create(
            name=self._engine,
            fact=preference_fact(key, value),
            scope=preference_scope(customer_id),
            config={"wait_for_completion": True},
        )


def build_agent_platform_memory(
    settings: Settings,
) -> tuple[SessionsCheckpointStore, MemoryBankPreferenceStore]:
    """Composición con la nube real: `Settings` → cliente de `agentplatform`."""
    import agentplatform

    from genaipocs_core.adapters.gcp_auth import gcp_credentials

    gcp, platform = settings.gcp, settings.gcp_agent_platform
    if not (gcp.project and platform.memory_engine):
        raise ValueError("Faltan GCP__PROJECT y GCP_AGENT_PLATFORM__MEMORY_ENGINE (7.G4).")
    client = agentplatform.Client(
        project=gcp.project, location=gcp.region, credentials=gcp_credentials(gcp)
    )
    return (
        SessionsCheckpointStore(client.aio.sessions, platform.memory_engine),
        MemoryBankPreferenceStore(client.aio.memory_banks.memories, platform.memory_engine),
    )
