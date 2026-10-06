"""Adaptador PostgreSQL (requiere el extra `headache-core[postgres]`)."""

from headache_core.adapters.postgres.database import (
    SqlAlchemyUnitOfWork,
    create_engine,
    create_session_factory,
)
from headache_core.adapters.postgres.tables import Base

__all__ = ["Base", "SqlAlchemyUnitOfWork", "create_engine", "create_session_factory"]
