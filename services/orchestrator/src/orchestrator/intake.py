"""Entrada de emails: publica emails sintéticos en `support.emails`. Publicar no cuesta; los
workers que los consuman sí (llaman al LLM).

    uv run python -m orchestrator.intake --limit 5
    uv run python -m orchestrator.intake --limit 5 --repeat 2   # duplicados: idempotencia
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from headache_core.adapters.messaging_factory import open_message_bus
from headache_core.application.orchestration import email_from_payload, email_to_payload
from headache_core.config import get_settings
from orchestrator.async_flow import SUPPORT_EMAILS
from orchestrator.settings import OrchestratorSettings

DEFAULT_SOURCE = Path("data/synthetic/support_emails.jsonl")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Publica emails de soporte en la cola")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--repeat", type=int, default=1, help="Publica cada email N veces")
    args = parser.parse_args()

    settings, config = get_settings(), OrchestratorSettings()
    lines = args.source.read_text(encoding="utf-8").splitlines()[: args.limit]
    # Se valida antes de publicar: lo que entra en la cola ya cumple el contrato.
    emails = [email_from_payload(json.loads(line)) for line in lines]
    async with open_message_bus(
        settings,
        config.broker,
        max_deliveries=config.max_deliveries,
        retry_delay_ms=config.retry_delay_ms,
    ) as bus:
        for _ in range(args.repeat):
            for email in emails:
                await bus.publish(SUPPORT_EMAILS, email_to_payload(email))
    print(f"Publicados {len(emails) * args.repeat} emails en '{SUPPORT_EMAILS}' ({config.broker}).")


if __name__ == "__main__":
    asyncio.run(main())
