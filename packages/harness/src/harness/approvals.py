"""Human-in-the-loop: ninguna acción irreversible se ejecuta sin la aprobación de una persona.

    modelo pide la tool -> guardas -> ApprovalGate -> solicitud PENDIENTE (no se ejecuta)
                                           |-> el modelo recibe `approval_required` y lo explica
    revisor -> ApprovalService.decide -> APPROVED -> el backend ejecuta los argumentos
                                                     congelados -> EXECUTED / FAILED

Independiente del framework: no hace falta pausar ni reanudar el grafo del agente (cada uno lo
hace a su manera). Quien ejecuta lo aprobado es el backend, no el modelo, así que lo que firma el
revisor es exactamente lo que ocurre. La política de dominio sigue decidiendo al ejecutar: la
aprobación autoriza la acción, no se salta las reglas.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from harness.executor import Denial, TurnTrace
from headache_core.application.tools import Tool, ToolContext, ToolExecutor, ToolRegistry
from headache_core.ports import ToolCall
from headache_core.ports.approvals import (
    ApprovalConflictError,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    approval_fingerprint,
)

logger = logging.getLogger(__name__)

APPROVAL_REQUIRED = "approval_required"
APPROVAL_REQUESTED = "approval_requested"
DEFAULT_TTL = timedelta(hours=24)

# Motivo por el que la acción necesita aprobación, o None si puede ejecutarse sin ella.
type ApprovalRule = Callable[[Tool[Any], BaseModel, ToolContext], Awaitable[str | None]]
type Summarizer = Callable[[BaseModel], str]
type Clock = Callable[[], datetime]


def always(reason: str) -> ApprovalRule:
    async def rule(_: Tool[Any], __: BaseModel, ___: ToolContext) -> str | None:
        return reason

    return rule


def canonical_arguments(args: BaseModel) -> str:
    return json.dumps(
        args.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def open_request(
    store: ApprovalStore,
    *,
    tool: str,
    arguments: str,
    context: ToolContext,
    summary: str,
    reason: str,
    now: datetime,
    ttl: timedelta = DEFAULT_TTL,
) -> ApprovalRequest:
    """Crea la solicitud o reutiliza la abierta para la misma acción (idempotente)."""
    fingerprint = approval_fingerprint(context.customer_id, tool, arguments)
    if existing := await store.find_open(fingerprint):
        return existing
    request = ApprovalRequest(
        id=f"apr_{secrets.token_hex(8)}",
        tool=tool,
        arguments=arguments,
        customer_id=context.customer_id,
        summary=summary,
        reason=reason,
        created_at=now,
        expires_at=now + ttl,
    )
    try:
        await store.add(request)
    except ApprovalConflictError:
        # Otra réplica la creó entre la búsqueda y la inserción.
        if existing := await store.find_open(fingerprint):
            return existing
        raise
    logger.info("Aprobación %s solicitada para %s", request.id, tool)
    return request


@dataclass(frozen=True, slots=True)
class ApprovalGate:
    """Guarda de tools: la última de la cadena (antes ya se validaron argumentos y políticas)."""

    store: ApprovalStore
    rules: Mapping[str, ApprovalRule]
    summaries: Mapping[str, Summarizer] = field(default_factory=dict)
    ttl: timedelta = DEFAULT_TTL
    clock: Clock = _utcnow

    async def check(
        self, tool: Tool[Any], args: BaseModel, context: ToolContext, trace: TurnTrace
    ) -> Denial | None:
        rule = self.rules.get(tool.name)
        reason = await rule(tool, args, context) if rule else None
        if reason is None:
            return None
        arguments = canonical_arguments(args)
        summarize = self.summaries.get(tool.name)
        request = await open_request(
            self.store,
            tool=tool.name,
            arguments=arguments,
            context=context,
            summary=summarize(args) if summarize else f"{tool.name} {arguments}",
            reason=reason,
            now=self.clock(),
            ttl=self.ttl,
        )
        trace.findings.append(APPROVAL_REQUESTED)
        return Denial(
            APPROVAL_REQUIRED,
            f"Esta acción necesita la aprobación de una persona y queda pendiente (referencia "
            f"{request.id}). No la repitas ni prometas el resultado: indica al cliente que un "
            "agente de Headache la revisará y que recibirá la confirmación.",
        )


class ApprovalError(Exception):
    """La decisión no es válida (revisor, estado o caducidad)."""


class ApprovalService:
    """Lado del revisor: decide y, si aprueba, ejecuta exactamente lo solicitado."""

    def __init__(
        self,
        store: ApprovalStore,
        registry: ToolRegistry,
        *,
        clock: Clock = _utcnow,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._store = store
        self._registry = registry
        # Sin guardas: la aprobación ES la guarda; volver a pasar por ApprovalGate sería un bucle.
        self._executor = ToolExecutor(registry)
        self._clock = clock
        self._ttl = ttl

    async def request(
        self, tool_name: str, args: BaseModel, context: ToolContext, *, reason: str, summary: str
    ) -> ApprovalRequest:
        """Para flujos sin agente (p. ej. enviar un email ya validado)."""
        tool = self._registry.get(tool_name)
        if tool is None or not isinstance(args, tool.args_model):
            raise ApprovalError(f"Tool o argumentos no válidos: {tool_name}.")
        return await open_request(
            self._store,
            tool=tool_name,
            arguments=canonical_arguments(args),
            context=context,
            summary=summary,
            reason=reason,
            now=self._clock(),
            ttl=self._ttl,
        )

    async def pending(self, *, customer_id: str | None = None) -> list[ApprovalRequest]:
        return await self._store.list_requests(
            status=ApprovalStatus.PENDING, customer_id=customer_id
        )

    async def decide(
        self, approval_id: str, *, reviewer: str, approve: bool, note: str = ""
    ) -> ApprovalRequest:
        request = await self._store.get(approval_id)
        if request is None:
            raise ApprovalError(f"No existe la solicitud {approval_id}.")
        # Separación de funciones: quien pide (cliente o empleado) no puede aprobarse a sí mismo.
        if not reviewer.strip() or reviewer == request.customer_id:
            raise ApprovalError("El revisor debe ser una persona distinta de quien lo solicita.")
        if request.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"La solicitud ya está en estado '{request.status}'.")
        now = self._clock()
        if now >= request.expires_at:
            expired = replace(request, status=ApprovalStatus.EXPIRED, decided_at=now)
            await self._store.transition(approval_id, ApprovalStatus.PENDING, expired)
            raise ApprovalError("La solicitud ha caducado: el contexto puede haber cambiado.")
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        decided = replace(request, status=status, reviewer=reviewer, note=note, decided_at=now)
        if not await self._store.transition(approval_id, ApprovalStatus.PENDING, decided):
            raise ApprovalError("Otra persona ya ha decidido esta solicitud.")
        logger.info("Aprobación %s: %s por %s", approval_id, status, reviewer)
        if not approve:
            return decided
        return await self._execute(decided)

    async def _execute(self, request: ApprovalRequest) -> ApprovalRequest:
        execution = await self._executor.execute(
            ToolCall(request.id, request.tool, request.arguments),
            ToolContext(customer_id=request.customer_id, request_id=request.id),
        )
        status = ApprovalStatus.FAILED if execution.is_error else ApprovalStatus.EXECUTED
        final = replace(request, status=status, result=execution.output)
        # Si el proceso muere antes de este punto queda APPROVED: se concilia, no se reejecuta.
        await self._store.transition(request.id, ApprovalStatus.APPROVED, final)
        return final
