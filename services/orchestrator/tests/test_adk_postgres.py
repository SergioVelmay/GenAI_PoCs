"""ADK con sesiones en PostgreSQL (9.G2): otro proceso reanuda el caso pausado.

docker compose up -d
uv run pytest -m integration services/orchestrator
"""

from __future__ import annotations

import json
import uuid

import pytest
from google.adk.sessions import BaseSessionService
from google.adk.workflow import RetryConfig
from sqlalchemy import text

from genaipocs_core.application.orchestration import TriageAgent
from genaipocs_core.config import get_settings
from genaipocs_core.domain import CaseStatus, SupportEmail
from genaipocs_core.ports import LLMError, LLMTransientError, ToolCall
from orchestrator.adk_workflow import AdkCaseWorkflow
from orchestrator.checkpoints import ADK_SCHEMA, postgres_session_service
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import ORDER, LocalWorld, local_world, support_email

pytestmark = pytest.mark.integration

PENDING = "Tu solicitud queda pendiente de revisión por un agente de Genaipocs."
NO_WAIT = RetryConfig(max_attempts=2, initial_delay=0, jitter=0, exceptions=[LLMTransientError])


def _triage(intent: str, order_id: str | None = ORDER) -> str:
    return json.dumps(
        {
            "intent": intent,
            "language": "es",
            "order_id": order_id,
            "urgency": "normal",
            "summary": f"Caso de tipo {intent}.",
        }
    )


def _adk(world: LocalWorld, sessions: BaseSessionService) -> AdkCaseWorkflow:
    return AdkCaseWorkflow(
        TriageAgent(world.llm),
        build_specialists(world.deps),
        build_approval_service(world.deps),
        session_service=sessions,
        retry_config=NO_WAIT,
    )


def _email(body: str, *, order_id: str | None = ORDER) -> SupportEmail:
    return support_email(body, email_id=f"eml_{uuid.uuid4().hex[:12]}", order_id=order_id)


async def test_paused_case_is_resumed_by_another_instance() -> None:
    email = _email("Devolvedme el dinero.")
    refund = ToolCall("c1", "request_refund", json.dumps({"order_id": ORDER, "reason": "No va."}))
    world = local_world([_triage("refund_request"), [refund], PENDING])
    settings = get_settings().postgres

    async with postgres_session_service(settings) as sessions:
        run = await _adk(world, sessions).start(email)
    assert run["awaiting_review"] is True

    # "Reinicio": engine, servicio de sesiones y workflow nuevos; mismo PostgreSQL.
    async with postgres_session_service(settings) as sessions:
        adk = _adk(world, sessions)
        resumed = await adk.get(run["case_id"])
        assert resumed is not None and resumed["awaiting_review"] is True
        reviewed = await adk.review(run["case_id"], reviewer="ops-1", approve=True)
        async with sessions.db_engine.connect() as conn:
            found = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                {"s": ADK_SCHEMA},
            )
            names = {row[0] for row in found}

    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED
    # Las tablas de ADK viven en su esquema, no en `public` (Alembic no las ve).
    assert {"sessions", "events"} <= names


async def test_after_a_crash_another_instance_resumes_without_retriaging() -> None:
    email = _email("¿A qué hora abren?", order_id=None)
    world = local_world([_triage("event_info", None), LLMError("credenciales"), "A las 19:00."])
    settings = get_settings().postgres

    async with postgres_session_service(settings) as sessions:
        with pytest.raises(LLMError):
            await _adk(world, sessions).start(email)
    async with postgres_session_service(settings) as sessions:
        run = await _adk(world, sessions).start(email)

    assert run["outcome"] is not None and run["outcome"].status is CaseStatus.RESOLVED
    triage_calls = [c for c in world.llm.calls if "agente de triaje" in c[0].content]
    assert len(triage_calls) == 1
