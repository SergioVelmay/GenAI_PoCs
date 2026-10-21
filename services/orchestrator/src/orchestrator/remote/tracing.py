"""La traza cruza A2A (fase 11.3): `traceparent` en la petición y span SERVER en el especialista.

Va por el puerto de telemetría y no por la instrumentación automática de httpx/ASGI: así
funciona igual en los tests (cliente ASGI en proceso) que entre contenedores, y el especialista
abre un solo span por petición con lo que importa (especialista, método A2A).
"""

from __future__ import annotations

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

from headache_core.application.telemetry import get_telemetry
from headache_core.ports.messaging import PROPAGATED_HEADERS
from headache_core.ports.telemetry import SpanKind


async def inject_trace_headers(request: httpx.Request) -> None:
    """Hook de httpx: la petición lleva el contexto del span activo (el `invoke_agent`)."""
    for key, value in get_telemetry().inject().items():
        request.headers[key] = value


def propagate_trace(http: httpx.AsyncClient) -> None:
    hooks = http.event_hooks
    if inject_trace_headers not in hooks["request"]:
        http.event_hooks = {**hooks, "request": [*hooks["request"], inject_trace_headers]}


class TraceContextMiddleware:
    """Middleware ASGI: un span SERVER por petición, hijo del contexto que trae el llamante."""

    def __init__(self, app: ASGIApp, *, specialist: str, skip: frozenset[str]) -> None:
        self._app = app
        self._specialist = specialist
        self._skip = skip

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._skip:
            await self._app(scope, receive, send)
            return
        carrier = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope["headers"]
            if key.decode("latin-1") in PROPAGATED_HEADERS
        }
        method, path = scope["method"], scope["path"]
        with get_telemetry().span(
            f"{method} {path}",
            kind=SpanKind.SERVER,
            parent=carrier,
            attributes={
                "http.request.method": method,
                "url.path": path,
                "headache.a2a.specialist": self._specialist,
            },
        ):
            await self._app(scope, receive, send)
