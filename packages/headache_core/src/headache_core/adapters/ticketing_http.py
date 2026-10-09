"""TicketingPort sobre la API HTTP de la fase 1 (`ticketing_api`) con httpx."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self

import httpx

from headache_core.config import TicketingApiSettings
from headache_core.domain import (
    Event,
    EventCategory,
    Money,
    Order,
    OrderStatus,
    RefundStatus,
    Session,
)
from headache_core.ports import (
    RefundResult,
    TicketingNotFoundError,
    TicketingRejectedError,
    TicketingUnavailableError,
)


def _money(data: dict[str, Any]) -> Money:
    return Money(Decimal(str(data["amount"])), data["currency"])


def _event(data: dict[str, Any]) -> Event:
    return Event(
        id=data["id"],
        title=data["title"],
        category=EventCategory(data["category"]),
        city=data["city"],
        venue_id=data["venue_id"],
        description=data["description"],
        price=_money(data["price"]),
        refundable=data["refundable"],
        refund_window_hours=data["refund_window_hours"],
    )


def _session(data: dict[str, Any]) -> Session:
    return Session(
        id=data["id"],
        event_id=data["event_id"],
        starts_at=datetime.fromisoformat(data["starts_at"]),
        capacity=data["capacity"],
        available=data["available"],
    )


def _order(data: dict[str, Any]) -> Order:
    return Order(
        id=data["id"],
        customer_id=data["customer_id"],
        session_id=data["session_id"],
        quantity=data["quantity"],
        total=_money(data["total"]),
        status=OrderStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]),
    )


class HttpTicketingClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: TicketingApiSettings) -> Self:
        return cls(httpx.AsyncClient(base_url=settings.base_url, timeout=settings.timeout_s))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.TransportError as exc:  # Incluye timeouts.
            raise TicketingUnavailableError(f"ticketing_api no responde: {exc!r}") from exc
        if response.status_code == 404:
            raise TicketingNotFoundError(response.json().get("detail", "No encontrado."))
        if response.status_code == 409:
            raise TicketingRejectedError(response.json().get("detail", "Operación rechazada."))
        if response.status_code == 422:
            raise TicketingRejectedError("Petición inválida para la API de ticketing.")
        if response.status_code >= 500:
            raise TicketingUnavailableError(f"ticketing_api devolvió {response.status_code}.")
        response.raise_for_status()
        return response.json()

    async def search_events(
        self, *, city: str | None = None, category: EventCategory | None = None, limit: int = 20
    ) -> list[Event]:
        params: dict[str, Any] = {"limit": limit}
        if city is not None:
            params["city"] = city
        if category is not None:
            params["category"] = category.value
        return [_event(e) for e in await self._request("GET", "/events", params=params)]

    async def get_event(self, event_id: str) -> Event:
        return _event(await self._request("GET", f"/events/{event_id}"))

    async def list_sessions(self, event_id: str, *, only_available: bool = True) -> list[Session]:
        data = await self._request(
            "GET",
            f"/events/{event_id}/sessions",
            params={"only_available": str(only_available).lower()},
        )
        return [_session(s) for s in data]

    async def get_order(self, order_id: str) -> Order:
        return _order(await self._request("GET", f"/orders/{order_id}"))

    async def list_customer_orders(self, customer_id: str, *, limit: int = 20) -> list[Order]:
        data = await self._request(
            "GET", f"/customers/{customer_id}/orders", params={"limit": limit}
        )
        return [_order(o) for o in data]

    async def request_refund(self, order_id: str, *, reason: str = "") -> RefundResult:
        data = await self._request("POST", f"/orders/{order_id}/refunds", json={"reason": reason})
        return RefundResult(
            refund_id=data["refund_id"],
            order_id=data["order_id"],
            status=RefundStatus(data["status"]),
            approved=data["approved"],
            amount=_money(data["amount"]),
            decision_reason=data["decision_reason"],
            requested_at=datetime.fromisoformat(data["requested_at"]),
        )
