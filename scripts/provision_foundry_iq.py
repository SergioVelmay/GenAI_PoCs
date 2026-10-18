"""Carril A en Azure: knowledge source/base de Foundry IQ, conexion MCP y agente.

Terraform crea AI Search, el proyecto de Foundry, los despliegues de modelos y los permisos
(`foundry.tf`). Estos objetos no los gestiona azurerm.

Uso:
    uv run python scripts/provision_foundry_iq.py            # dry-run: imprime las definiciones
    uv run python scripts/provision_foundry_iq.py --apply    # crea/actualiza (nube, con coste)

Orden: knowledge source (preview, `baseFilter` = audiencia) -> knowledge base (preview,
esfuerzo `minimal` y salida extractiva) ->
conexion `RemoteTool` del proyecto (ARM) -> agente con la herramienta MCP de la KB.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from headache_core.adapters.foundry_iq import (
    PREVIEW_API_VERSION,
    agent_definition,
    knowledge_base_definition,
    knowledge_source_definition,
    mcp_connection_definition,
    mcp_endpoint,
    publish_agent,
)
from headache_core.config import Settings, get_settings
from headache_core.domain.retrieval import AccessScope

ARM_CONNECTIONS_API = "2025-10-01-preview"


def definitions(settings: Settings) -> dict[str, dict[str, Any]]:
    foundry = settings.foundry_iq
    search = settings.azure_search.endpoint or "https://<servicio>.search.windows.net"
    url = mcp_endpoint(search, foundry.knowledge_base)
    return {
        "knowledge_source": knowledge_source_definition(
            foundry.knowledge_source,
            index_name=settings.azure_search.index_name,
            audience=AccessScope(foundry.audience),
        ),
        "knowledge_base": knowledge_base_definition(
            foundry.knowledge_base, knowledge_source=foundry.knowledge_source
        ),
        "connection": mcp_connection_definition(foundry.connection, mcp_url=url),
        "agent": agent_definition(
            foundry.agent_name,
            model=foundry.agent_model,
            mcp_url=url,
            connection=foundry.connection,
        ),
    }


async def apply(settings: Settings) -> None:
    import httpx
    from azure.core.rest import HttpRequest
    from azure.identity.aio import DefaultAzureCredential
    from azure.search.documents.indexes.aio import SearchIndexClient

    foundry = settings.foundry_iq
    missing = [
        name
        for name, value in (
            ("AZURE_SEARCH__ENDPOINT", settings.azure_search.endpoint),
            ("FOUNDRY_IQ__PROJECT_ENDPOINT", foundry.project_endpoint),
            ("FOUNDRY_IQ__PROJECT_RESOURCE_ID", foundry.project_resource_id),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Faltan: {', '.join(missing)}.")
    body = definitions(settings)
    source = body["knowledge_source"]
    path = f"/knowledgesources('{source['name']}')?api-version={PREVIEW_API_VERSION}"
    async with (
        DefaultAzureCredential() as credential,
        SearchIndexClient(settings.azure_search.endpoint, credential) as search,
        httpx.AsyncClient(timeout=60) as http,
    ):
        (await search.send_request(HttpRequest("PUT", path, json=source))).raise_for_status()
        stored = await search.send_request(HttpRequest("GET", path))
        stored.raise_for_status()
        expected = source["searchIndexParameters"]["baseFilter"]
        # Sin baseFilter el agente veria todo el indice: se verifica en vez de suponerlo.
        if stored.json()["searchIndexParameters"].get("baseFilter") != expected:
            raise SystemExit("La knowledge source no conserva el baseFilter de la audiencia.")
        # Preview (como la knowledge source): la GA no conoce el esfuerzo ni el modo de salida
        # y el endpoint MCP (solo preview) los necesita explicitos.
        base = body["knowledge_base"]
        kb_path = f"/knowledgebases('{base['name']}')?api-version={PREVIEW_API_VERSION}"
        (await search.send_request(HttpRequest("PUT", kb_path, json=base))).raise_for_status()
        stored_kb = await search.send_request(HttpRequest("GET", kb_path))
        stored_kb.raise_for_status()
        if stored_kb.json().get("retrievalReasoningEffort") != base["retrievalReasoningEffort"]:
            raise SystemExit("La knowledge base no conserva el esfuerzo de razonamiento minimal.")

        arm = await credential.get_token("https://management.azure.com/.default")
        connection = await http.put(
            f"https://management.azure.com{foundry.project_resource_id}/connections/"
            f"{foundry.connection}?api-version={ARM_CONNECTIONS_API}",
            headers={"Authorization": f"Bearer {arm.token}"},
            json=body["connection"],
        )
        connection.raise_for_status()

        foundry_token = await credential.get_token("https://ai.azure.com/.default")
        version = await publish_agent(
            http, foundry.project_endpoint, foundry_token.token, body["agent"]
        )
    print(
        f"Foundry IQ listo: KB '{foundry.knowledge_base}', "
        f"agente '{foundry.agent_name}' versión {version}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.apply:
        asyncio.run(apply(settings))
        return
    # ASCII: las marcas de cita del agente rompen la consola cp1252 de Windows al redirigir.
    print(json.dumps(definitions(settings), indent=2))


if __name__ == "__main__":
    main()
