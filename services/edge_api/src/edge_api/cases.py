"""Casos de soporte desde la web (fase 10.5): 202 al instante y seguimiento por SSE.

    POST /cases                 (Idempotency-Key)  → 202 + Location: /cases/{id}
    GET  /cases/{id}                               → estado actual
    GET  /cases/{id}/events     (text/event-stream) → `status` en cada cambio, hasta cerrarse

El trabajo pesado (triaje y especialistas con LLM, segundos o minutos) va por la cola: el alta
solo escribe el caso y su evento en el outbox, en una transacción. Repetir el POST con la misma
clave devuelve el mismo caso (`Idempotent-Replayed: true`); con otro cuerpo, 409.

El seguimiento consulta el `CaseStore` cada `case_poll_seconds`. Es lo más simple y aguanta
miles de clientes (una lectura por clave primaria); a más escala se empujaría desde los
eventos `case.outcomes` (fan-out por réplica) o `LISTEN/NOTIFY`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.sse import KEEPALIVE_COMMENT, format_sse_event
from pydantic import BaseModel, ConfigDict, Field

from edge_api.identity import CallerResolver
from edge_api.settings import EdgeSettings
from edge_api.sse import sse
from headache_core.application.admission import AdmissionController
from headache_core.application.case_intake import CaseIntakeService, CaseRequest
from headache_core.domain import CaseStatus
from headache_core.domain.admission import Caller, ClientPlan
from headache_core.domain.orchestration import is_terminal
from headache_core.ports.cases import CaseStore
from headache_core.ports.intake import CaseIntakeStore

ACCEPTED = "accepted"  # Alta guardada; el triaje aún no ha creado el caso.


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    order_id: str | None = Field(default=None, pattern=r"^ord_\w{1,40}$")
    language: str | None = Field(default=None, pattern=r"^(es|en)$")


class CaseOut(BaseModel):
    case_id: str
    status: str
    specialist: str | None = None
    updated_at: datetime | None = None
    links: dict[str, str]


@dataclass
class CaseBackend:
    intake: CaseIntakeStore
    cases: CaseStore


async def _caller(request: Request) -> Caller:
    resolve: CallerResolver = request.app.state.resolve
    return await resolve(request)


CurrentCaller = Annotated[Caller, Depends(_caller)]


def _links(case_id: str) -> dict[str, str]:
    return {"self": f"/cases/{case_id}", "events": f"/cases/{case_id}/events"}


def _customer(caller: Caller) -> str:
    if caller.subject is None:
        # Un caso es de alguien: sin identidad no hay a quién responder ni a quién enseñarlo.
        raise HTTPException(
            401, "Abrir un caso exige identificarse.", {"WWW-Authenticate": "Bearer"}
        )
    return caller.subject


async def _snapshot(backend: CaseBackend, case_id: str, caller: Caller) -> CaseOut:
    """Estado actual, solo para su dueño (o personal interno). 404 si no existe o no es suyo:
    no se confirma la existencia de casos ajenos."""
    submission = await backend.intake.get_submission(case_id)
    record = await backend.cases.get(case_id)
    owner = submission.customer_id if submission else record.customer_id if record else None
    if owner is None or (owner != caller.subject and caller.plan is not ClientPlan.INTERNAL):
        raise HTTPException(404, "Caso no encontrado.")
    if record is None:
        return CaseOut(case_id=case_id, status=ACCEPTED, links=_links(case_id))
    return CaseOut(
        case_id=case_id,
        status=record.status.value,
        specialist=record.specialist.value if record.specialist else None,
        updated_at=record.updated_at,
        links=_links(case_id),
    )


def _is_final(snapshot: CaseOut) -> bool:
    return snapshot.status != ACCEPTED and is_terminal(CaseStatus(snapshot.status))


async def _follow(
    backend: CaseBackend, case_id: str, caller: Caller, first: CaseOut, edge: EdgeSettings
) -> AsyncGenerator[bytes, None]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + edge.case_stream_seconds
    last_beat = loop.time()
    current = first
    # `retry`: si se corta, el navegador reconecta solo pasado este tiempo.
    yield format_sse_event(
        event="status",
        data_str=current.model_dump_json(),
        retry=int(edge.case_poll_seconds * 1000),
    )
    while not _is_final(current):
        if loop.time() >= deadline:
            yield sse("timeout", {"detail": "Vuelve a conectar para seguir el caso."})
            return
        await asyncio.sleep(edge.case_poll_seconds)
        latest = await _snapshot(backend, case_id, caller)
        if latest.status != current.status:
            current = latest
            yield format_sse_event(event="status", data_str=current.model_dump_json())
            last_beat = loop.time()
        elif loop.time() - last_beat >= edge.heartbeat_seconds:
            yield KEEPALIVE_COMMENT
            last_beat = loop.time()


def build_router(
    edge: EdgeSettings, controller: AdmissionController, backend: CaseBackend | None
) -> APIRouter:
    router = APIRouter(prefix="/cases", tags=["casos"])

    def require() -> CaseBackend:
        if backend is None:
            raise HTTPException(503, "Casos desactivados en este edge.")
        return backend

    @router.post("", status_code=202, response_model=CaseOut)
    async def submit(
        body: CaseIn,
        response: Response,
        who: CurrentCaller,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> CaseOut:
        store = require()
        customer = _customer(who)
        if idempotency_key is None:
            # Sin clave, un reintento del cliente abriría un segundo caso: se exige.
            raise HTTPException(400, "Falta la cabecera Idempotency-Key.")
        async with controller.admit(who):
            intake = CaseIntakeService(store.intake, topic=edge.cases_topic)
            submitted = await intake.submit(
                customer_id=customer,
                idempotency_key=idempotency_key,
                request=CaseRequest(body.subject, body.body, body.order_id, body.language),
            )
        response.headers["Location"] = f"/cases/{submitted.case_id}"
        if not submitted.created:
            response.headers["Idempotent-Replayed"] = "true"
        return await _snapshot(store, submitted.case_id, who)

    @router.get("/{case_id}", response_model=CaseOut)
    async def status(case_id: str, who: CurrentCaller) -> CaseOut:
        return await _snapshot(require(), case_id, who)

    @router.get(
        "/{case_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def events(case_id: str, who: CurrentCaller) -> StreamingResponse:
        store = require()
        first = await _snapshot(store, case_id, who)  # 404 antes de abrir el stream.
        return StreamingResponse(
            _follow(store, case_id, who, first, edge),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
