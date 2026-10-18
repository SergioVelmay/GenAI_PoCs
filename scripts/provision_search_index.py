"""Crea (o actualiza) el índice vectorial de Azure AI Search del carril B.

Terraform crea el servicio de AI Search, pero azurerm no gestiona índices: se crean con el SDK.

Uso:
    uv run python scripts/provision_search_index.py            # dry-run: imprime la definición
    uv run python scripts/provision_search_index.py --apply    # crea el índice (requiere nube)
"""

from __future__ import annotations

import argparse
import asyncio
import json

from headache_core.adapters.azure_search import build_index
from headache_core.adapters.postgres import EMBEDDING_DIMENSIONS
from headache_core.config import get_settings


async def forget_ledger(index_name: str) -> int:
    """El ledger describe el índice: si el índice es nuevo (vacío), lo indexado ya no existe.

    Sin esto, con AI Search efímero el worker vería "sin cambios" y dejaría el índice vacío.
    """
    from headache_core.adapters.postgres import (
        PostgresIngestionLedger,
        create_engine,
        create_session_factory,
    )
    from indexer.bootstrap import cloud_ledger_index

    engine = create_engine(get_settings().postgres, application_name="provision_search_index")
    try:
        ledger = PostgresIngestionLedger(create_session_factory(engine))
        return await ledger.forget_index(cloud_ledger_index(index_name, "azure_search"))
    finally:
        await engine.dispose()


async def apply() -> None:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity.aio import DefaultAzureCredential
    from azure.search.documents.indexes.aio import SearchIndexClient

    settings = get_settings().azure_search
    if not settings.endpoint:
        raise SystemExit("Falta AZURE_SEARCH__ENDPOINT.")
    async with (
        DefaultAzureCredential() as credential,
        SearchIndexClient(settings.endpoint, credential) as client,
    ):
        try:
            await client.get_index(settings.index_name)
            created = False
        except ResourceNotFoundError:
            created = True
        index = await client.create_or_update_index(
            build_index(settings.index_name, EMBEDDING_DIMENSIONS)
        )
    print(f"Índice '{index.name}' listo en {settings.endpoint}.")
    if created:
        forgotten = await forget_ledger(index.name)
        print(f"Índice nuevo: {forgotten} entradas del ledger olvidadas (se reindexarán).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        asyncio.run(apply())
        return
    index = build_index(get_settings().azure_search.index_name, EMBEDDING_DIMENSIONS)
    print(json.dumps(index.as_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
