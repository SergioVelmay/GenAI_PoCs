"""El caso pausado sobrevive a un "reinicio": otro proceso lo reanuda desde PostgreSQL.

docker compose up -d
uv run pytest -m integration services/orchestrator
"""

from __future__ import annotations

import json
import uuid

import pytest

from headache_core.application.orchestration import TriageAgent
from headache_core.config import get_settings
from headache_core.domain import CaseStatus
from headache_core.ports import ToolCall
from orchestrator.checkpoints import postgres_checkpointer
from orchestrator.graph import RouterGraph
from orchestrator.specialists import build_approval_service, build_specialists
from orchestrator.testing import ORDER, LocalWorld, local_world, support_email

pytestmark = pytest.mark.integration

PENDING = "Tu solicitud queda pendiente de revisión por un agente de Headache."


def _router(world: LocalWorld, checkpointer: object) -> RouterGraph:
    return RouterGraph(
        TriageAgent(world.llm),
        build_specialists(world.deps),
        build_approval_service(world.deps),
        checkpointer=checkpointer,  # type: ignore[arg-type]
    )


async def test_paused_case_is_resumed_by_another_instance() -> None:
    email = support_email("Devolvedme el dinero.", email_id=f"eml_{uuid.uuid4().hex[:12]}")
    triage = {
        "intent": "refund_request",
        "language": "es",
        "order_id": ORDER,
        "urgency": "normal",
        "summary": "Pide el reembolso.",
    }
    refund = ToolCall("c1", "request_refund", json.dumps({"order_id": ORDER, "reason": "No va."}))
    world = local_world([json.dumps(triage), [refund], PENDING])
    settings = get_settings().postgres

    async with postgres_checkpointer(settings) as saver:
        run = await _router(world, saver).start(email)
    assert run["awaiting_review"] is True

    # "Reinicio": conexión y grafo nuevos; las aprobaciones viven en el mismo almacén.
    async with postgres_checkpointer(settings) as saver:
        router = _router(world, saver)
        resumed = await router.get(run["case_id"])
        assert resumed is not None and resumed["awaiting_review"] is True
        reviewed = await router.review(run["case_id"], reviewer="ops-1", approve=True)

    assert reviewed["outcome"] is not None
    assert reviewed["outcome"].status is CaseStatus.RESOLVED
