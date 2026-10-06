"""Caso de uso: comprar entradas sin sobreventa y de forma idempotente."""

from __future__ import annotations

from datetime import UTC, datetime

from headache_core.application.common import NotFoundError, new_id
from headache_core.domain import Order, OrderStatus, PurchaseNotAllowedError, check_purchase
from headache_core.ports import ConflictError, UnitOfWork


class PurchaseService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def purchase(
        self,
        *,
        customer_id: str,
        session_id: str,
        quantity: int,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> Order:
        now = now or datetime.now(UTC)
        async with self._uow as uow:
            if idempotency_key is not None:
                previous = await uow.orders.get_by_idempotency_key(idempotency_key)
                if previous is not None:
                    if (previous.customer_id, previous.session_id, previous.quantity) != (
                        customer_id,
                        session_id,
                        quantity,
                    ):
                        raise ConflictError("Idempotency-Key reutilizada con otra petición.")
                    return previous

            if await uow.customers.get(customer_id) is None:
                raise NotFoundError(f"Cliente {customer_id} no encontrado.")
            session = await uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError(f"Sesión {session_id} no encontrada.")
            event = await uow.events.get(session.event_id)
            if event is None:
                raise NotFoundError(f"Evento {session.event_id} no encontrado.")

            check_purchase(session=session, quantity=quantity, now=now)
            # La lectura anterior puede estar obsoleta: la reserva atómica es la que decide.
            if not await uow.sessions.try_reserve(session_id, quantity):
                raise PurchaseNotAllowedError("No quedan plazas suficientes.")

            order = Order(
                id=new_id("ord"),
                customer_id=customer_id,
                session_id=session_id,
                quantity=quantity,
                total=event.price * quantity,
                status=OrderStatus.PAID,
                created_at=now,
            )
            await uow.orders.add(order, idempotency_key=idempotency_key)
            await uow.commit()
        return order
