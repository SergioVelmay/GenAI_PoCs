"""Consola del equipo de operaciones sobre los casos del flujo asíncrono.

    uv run python -m orchestrator.cases list [--status pending_approval]
    uv run python -m orchestrator.cases approve case_eml_000004 --reviewer ops-ana
    uv run python -m orchestrator.cases reject case_eml_000007 --reviewer ops-ana --note "Tono"

Aprobar ejecuta la acción congelada (reembolso por la API de ticketing, envío del email) sin
volver a llamar al LLM.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack

from harness.approvals import ApprovalService
from headache_core.adapters.messaging_factory import open_message_bus
from headache_core.adapters.ticketing_http import HttpTicketingClient
from headache_core.config import get_settings
from headache_core.domain import CaseStatus
from orchestrator.async_flow import CaseReviewService
from orchestrator.bootstrap import log_outbox, open_postgres_stores
from orchestrator.review import ReviewError
from orchestrator.settings import OrchestratorSettings
from orchestrator.specialists import approval_registry_for


async def main() -> None:
    parser = argparse.ArgumentParser(description="Casos de soporte (operaciones)")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--status", choices=[s.value for s in CaseStatus])
    for name in ("approve", "reject"):
        decide = sub.add_parser(name)
        decide.add_argument("case_id")
        decide.add_argument("--reviewer", required=True)
        decide.add_argument("--note", default="")
    args = parser.parse_args()

    settings, config = get_settings(), OrchestratorSettings()
    async with AsyncExitStack() as stack:
        approvals, cases = await stack.enter_async_context(
            open_postgres_stores(settings, "orchestrator-cases")
        )
        if args.command == "list":
            status = CaseStatus(args.status) if args.status else None
            for case in await cases.list_cases(status=status):
                print(f"{case.case_id:<22} {case.specialist:<10} {case.status:<17} {case.email_id}")
            return
        ticketing = await stack.enter_async_context(
            HttpTicketingClient.from_settings(settings.ticketing_api)
        )
        bus = await stack.enter_async_context(
            open_message_bus(
                settings,
                config.broker,
                max_deliveries=config.max_deliveries,
                retry_delay_ms=config.retry_delay_ms,
            )
        )
        service = CaseReviewService(
            cases, ApprovalService(approvals, approval_registry_for(ticketing, log_outbox)), bus
        )
        try:
            case = await service.review(
                args.case_id,
                reviewer=args.reviewer,
                approve=args.command == "approve",
                note=args.note,
            )
        except ReviewError as exc:
            raise SystemExit(str(exc)) from None
        print(f"{case.case_id}: {case.status} ({', '.join(case.guardrails)})")


if __name__ == "__main__":
    asyncio.run(main())
