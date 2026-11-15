"""Olvida el ledger de un almacén de la nube recreado vacío, para que el worker lo reindexe.

El ledger describe lo indexado en cada destino (`<índice>@<almacén>`). Si el destino es efímero y
se recrea vacío (Agent Retrieval con `vector_search_enabled`, AI Search con `search_enabled`), el
worker vería "sin cambios" y lo dejaría vacío. En AI Search lo hace `provision_search_index.py
--apply` al crear el índice; la colección de Agent Retrieval la crea Terraform, así que se lanza
esto después del `apply`.

Uso (solo toca PostgreSQL local; reindexar después sí cuesta embeddings):
    uv run python -m indexer.forget_ledger gcp_vector_search
"""

from __future__ import annotations

import argparse
import asyncio

from headache_core.adapters.postgres import (
    PostgresIngestionLedger,
    create_engine,
    create_session_factory,
)
from headache_core.config import get_settings
from indexer.bootstrap import cloud_ledger_index
from indexer.settings import IndexerSettings

CLOUD_STORES = ("azure_search", "s3_vectors", "gcp_vector_search")


async def forget(vector_store: str) -> int:
    engine = create_engine(get_settings().postgres, application_name="forget_ledger")
    try:
        ledger = PostgresIngestionLedger(create_session_factory(engine))
        index = cloud_ledger_index(IndexerSettings().index_name, vector_store)
        return await ledger.forget_index(index)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vector_store", choices=CLOUD_STORES)
    args = parser.parse_args()
    forgotten = asyncio.run(forget(args.vector_store))
    print(f"{forgotten} entradas del ledger olvidadas en '{args.vector_store}' (se reindexarán).")


if __name__ == "__main__":
    main()
