"""Prueba de humo SIN COSTE de los especialistas A2A en contenedores (fase 9.5).

Por especialista: descubre la Agent Card, comprueba que sin token responde 401 y, con el token
de servicio del orquestador, envía un traspaso manipulado (ruta que contradice `ROUTES`), que
el especialista debe rechazar sin llegar a llamar al LLM.

    docker compose --profile agents up -d
    uv run python scripts/a2a_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import httpx
from a2a import helpers
from a2a.types import a2a_pb2 as pb

from headache_core.application.orchestration import envelope_to_payload
from headache_core.config import get_settings
from headache_core.domain import CaseEnvelope, Specialist, SupportIntent
from headache_mcp.auth import DevTokenIssuer
from orchestrator.remote import BearerAuth, RemoteSpecialist, dev_token_source

WRONG_INTENT = {
    Specialist.REFUNDS: SupportIntent.EVENT_INFO,
    Specialist.CREATIVE: SupportIntent.REFUND_REQUEST,
    Specialist.KNOWLEDGE: SupportIntent.REFUND_REQUEST,
}


async def check(specialist: Specialist, issuer: DevTokenIssuer) -> tuple[bool, str]:
    settings = get_settings().a2a
    base_url = settings.urls[specialist.value]
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as anonymous:
        unauthenticated = (await anonymous.post("/a2a", json={})).status_code
    auth = BearerAuth(dev_token_source(issuer, settings, specialist.value))
    async with httpx.AsyncClient(base_url=base_url, auth=auth, timeout=30) as http:
        remote = await RemoteSpecialist.connect(specialist, base_url, http)
        envelope = CaseEnvelope(
            case_id=f"case_smoke_{uuid.uuid4().hex[:8]}",
            email_id="eml_smoke",
            customer_id="cus_smoke",
            specialist=specialist,
            intent=WRONG_INTENT[specialist],
            language="es",
            urgency="normal",
            summary="Prueba de humo.",
        )
        message = helpers.new_data_message(envelope_to_payload(envelope), role=pb.Role.ROLE_USER)
        message.message_id = str(uuid.uuid4())
        client = remote._client  # Envío en bruto: el cliente legítimo bloquearía la ruta mala.
        states = [
            pb.TaskState.Name(r.task.status.state)
            async for r in client.send_message(pb.SendMessageRequest(message=message))
            if r.HasField("task")
        ]
        await remote.close()
    ok = unauthenticated == 401 and states == ["TASK_STATE_REJECTED"]
    return ok, f"sin token {unauthenticated}, traspaso manipulado {states}"


async def main() -> int:
    settings = get_settings()
    issuer = DevTokenIssuer.load_or_create(
        Path(settings.a2a.dev_private_key_file), issuer=settings.a2a.issuer
    )
    failures = 0
    for specialist in Specialist:
        ok, detail = await check(specialist, issuer)
        failures += not ok
        print(f"{'OK ' if ok else 'ERR'} {specialist.value:<10} {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
