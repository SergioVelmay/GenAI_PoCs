"""ticketing_api: el "backend corporativo" de Headache que leerán y modificarán los agentes.

Ejecutar en local (con PostgreSQL levantado y migrado):
    uv run uvicorn ticketing_api.app:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request, status
from fastapi.responses import JSONResponse

from headache_core.application import NotFoundError, PurchaseService, RefundService
from headache_core.config import get_settings
from headache_core.domain import EventCategory, PurchaseNotAllowedError
from headache_core.ports import ConflictError, UnitOfWork
from ticketing_api.schemas import (
    ID_PATTERN,
    ErrorOut,
    EventOut,
    MoneyOut,
    OrderOut,
    PurchaseIn,
    RefundIn,
    RefundOut,
    SessionOut,
)

UowFactory = Callable[[], UnitOfWork]
Clock = Callable[[], datetime]
ResourceId = Annotated[str, Path(pattern=ID_PATTERN)]

router = APIRouter()
ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
}


def get_uow(request: Request) -> UnitOfWork:
    factory: UowFactory = request.app.state.uow_factory
    return factory()


def get_now(request: Request) -> datetime:
    clock: Clock = request.app.state.clock
    return clock()


Uow = Annotated[UnitOfWork, Depends(get_uow)]
Now = Annotated[datetime, Depends(get_now)]


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/events", response_model=list[EventOut], tags=["catálogo"])
async def list_events(
    uow: Uow,
    city: str | None = None,
    category: EventCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[EventOut]:
    async with uow:
        events = await uow.events.find(city=city, category=category, limit=limit)
    return [EventOut.model_validate(e) for e in events]


@router.get("/events/{event_id}", response_model=EventOut, responses=ERRORS, tags=["catálogo"])
async def get_event(event_id: ResourceId, uow: Uow) -> EventOut:
    async with uow:
        event = await uow.events.get(event_id)
    if event is None:
        raise NotFoundError(f"Evento {event_id} no encontrado.")
    return EventOut.model_validate(event)


@router.get(
    "/events/{event_id}/sessions",
    response_model=list[SessionOut],
    tags=["disponibilidad"],
    summary="Próximas sesiones de un evento",
)
async def list_event_sessions(
    event_id: ResourceId, uow: Uow, now: Now, only_available: bool = True
) -> list[SessionOut]:
    async with uow:
        sessions = await uow.sessions.list_for_event(
            event_id, starts_after=now, only_available=only_available
        )
    return [SessionOut.model_validate(s) for s in sessions]


@router.get(
    "/sessions/{session_id}", response_model=SessionOut, responses=ERRORS, tags=["disponibilidad"]
)
async def get_session(session_id: ResourceId, uow: Uow) -> SessionOut:
    async with uow:
        session = await uow.sessions.get(session_id)
    if session is None:
        raise NotFoundError(f"Sesión {session_id} no encontrada.")
    return SessionOut.model_validate(session)


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    tags=["pedidos"],
    summary="Comprar entradas (idempotente con Idempotency-Key)",
)
async def create_order(
    body: PurchaseIn,
    uow: Uow,
    now: Now,
    idempotency_key: Annotated[str | None, Header(max_length=64)] = None,
) -> OrderOut:
    order = await PurchaseService(uow).purchase(
        customer_id=body.customer_id,
        session_id=body.session_id,
        quantity=body.quantity,
        idempotency_key=idempotency_key,
        now=now,
    )
    return OrderOut.model_validate(order)


@router.get("/orders/{order_id}", response_model=OrderOut, responses=ERRORS, tags=["pedidos"])
async def get_order(order_id: ResourceId, uow: Uow) -> OrderOut:
    async with uow:
        order = await uow.orders.get(order_id)
    if order is None:
        raise NotFoundError(f"Pedido {order_id} no encontrado.")
    return OrderOut.model_validate(order)


@router.get("/customers/{customer_id}/orders", response_model=list[OrderOut], tags=["pedidos"])
async def list_customer_orders(
    customer_id: ResourceId,
    uow: Uow,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[OrderOut]:
    async with uow:
        orders = await uow.orders.list_for_customer(customer_id, limit=limit)
    return [OrderOut.model_validate(o) for o in orders]


@router.post(
    "/orders/{order_id}/refunds",
    response_model=RefundOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
    tags=["reembolsos"],
    summary="Solicitar reembolso (la política de dominio decide)",
)
async def request_refund(order_id: ResourceId, body: RefundIn, uow: Uow, now: Now) -> RefundOut:
    outcome = await RefundService(uow).request_refund(
        order_id, reason=body.reason, requested_at=now
    )
    refund = outcome.refund
    return RefundOut(
        refund_id=refund.id,
        order_id=refund.order_id,
        status=refund.status,
        approved=outcome.decision.approved,
        amount=MoneyOut.model_validate(refund.amount),
        decision_reason=outcome.decision.reason,
        requested_at=refund.requested_at,
    )


def _error(status_code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handler


def create_app(uow_factory: UowFactory | None = None, clock: Clock | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if uow_factory is not None:
            yield
            return
        from headache_core.adapters.postgres import (
            SqlAlchemyUnitOfWork,
            create_engine,
            create_session_factory,
        )

        engine = create_engine(get_settings().postgres, application_name="ticketing_api")
        session_factory = create_session_factory(engine)
        app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
        yield
        await engine.dispose()

    app = FastAPI(title="Headache ticketing API", version="0.1.0", lifespan=lifespan)
    app.state.uow_factory = uow_factory
    app.state.clock = clock or (lambda: datetime.now(UTC))
    app.include_router(router)
    app.add_exception_handler(NotFoundError, _error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(PurchaseNotAllowedError, _error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(ConflictError, _error(status.HTTP_409_CONFLICT))
    return app
