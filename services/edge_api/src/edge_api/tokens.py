"""Tokens de desarrollo para el edge (solo local): mismo emisor que MCP y A2A.

uv run python -m edge_api.tokens --user ana --plan internal
"""

from __future__ import annotations

import argparse
from pathlib import Path

from edge_api.settings import EdgeSettings
from headache_core.config import get_settings
from headache_core.domain.admission import ClientPlan
from headache_mcp.auth import DevTokenIssuer


def mint(settings: EdgeSettings, *, user: str, plan: ClientPlan, ttl_s: int = 3600) -> str:
    issuer = DevTokenIssuer.load_or_create(
        Path(settings.dev_private_key_file), issuer=settings.issuer
    )
    return issuer.mint(
        subject=user,
        audience=settings.audience,
        expires_in_seconds=ttl_s,
        claims={settings.plan_claim: plan.value},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Token de desarrollo para el edge")
    parser.add_argument("--user", required=True)
    plans = [ClientPlan.PUBLIC.value, ClientPlan.INTERNAL.value]
    parser.add_argument("--plan", choices=plans, default=ClientPlan.PUBLIC.value)
    args = parser.parse_args()
    if get_settings().environment != "local":
        raise SystemExit("El emisor de desarrollo solo existe en local.")
    print(mint(EdgeSettings(), user=args.user, plan=ClientPlan(args.plan)))


if __name__ == "__main__":
    main()
