"""Contratos versionados de los servidores MCP: lo que ve cualquier cliente en `tools/list`.

Cambiar un nombre, una descripción o un esquema rompe a los agentes que ya los usan. El test de
contrato compara con `contracts/<servidor>.json`; actualizarlo es un cambio deliberado:

    uv run python -m headache_mcp.contracts --update
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP

from headache_core.adapters.memory import InMemoryUnitOfWork
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_mcp.servers import SERVERS, create_server

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
_WIRE_FIELDS = ("name", "title", "description", "inputSchema", "annotations")


async def describe(server: FastMCP[Any]) -> dict[str, Any]:
    async with Client(server) as client:
        tools = await client.list_tools()
    listed = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]
    return {
        "server": server.name,
        "tools": sorted(
            ({k: tool[k] for k in _WIRE_FIELDS if k in tool} for tool in listed),
            key=lambda tool: tool["name"],
        ),
    }


def contract_servers() -> dict[str, FastMCP[Any]]:
    ticketing = UnitOfWorkTicketing(InMemoryUnitOfWork)
    return {name: create_server(name, ticketing) for name in SERVERS}


def _render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, ensure_ascii=False, indent=2) + "\n"


async def _main(update: bool) -> int:
    stale = []
    for name, server in contract_servers().items():
        path = CONTRACTS_DIR / f"{name}.json"
        rendered = _render(await describe(server))
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            print(f"actualizado {path.name}")
        elif not path.exists() or path.read_text(encoding="utf-8") != rendered:
            stale.append(path.name)
    if stale:
        print(f"Contratos desactualizados: {', '.join(stale)}. Revisa y usa --update.")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Contratos de los servidores MCP")
    parser.add_argument("--update", action="store_true")
    raise SystemExit(asyncio.run(_main(parser.parse_args().update)))
