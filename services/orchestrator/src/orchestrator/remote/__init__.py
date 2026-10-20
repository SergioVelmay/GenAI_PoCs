"""Especialistas como microservicios A2A (fase 9.5)."""

from orchestrator.remote.auth import (
    BearerAuth,
    BearerAuthMiddleware,
    TokenSource,
    build_verifier,
    dev_token_source,
)
from orchestrator.remote.client import RemoteSpecialist, RemoteSpecialistError
from orchestrator.remote.server import build_agent_card, card_name, create_specialist_app

__all__ = [
    "BearerAuth",
    "BearerAuthMiddleware",
    "RemoteSpecialist",
    "RemoteSpecialistError",
    "TokenSource",
    "build_agent_card",
    "build_verifier",
    "card_name",
    "create_specialist_app",
    "dev_token_source",
]
