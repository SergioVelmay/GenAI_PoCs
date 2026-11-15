"""Adaptador PostgreSQL (requiere el extra `genaipocs-core[postgres]`)."""

from genaipocs_core.adapters.postgres.database import (
    SqlAlchemyUnitOfWork,
    create_engine,
    create_session_factory,
)
from genaipocs_core.adapters.postgres.tables import EMBEDDING_DIMENSIONS, Base
from genaipocs_core.adapters.postgres.vector_store import PgVectorStore, PostgresIngestionLedger

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "Base",
    "PgVectorStore",
    "PostgresIngestionLedger",
    "SqlAlchemyUnitOfWork",
    "create_engine",
    "create_session_factory",
]
