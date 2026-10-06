"""Engine, factoría de sesiones y Unit of Work sobre PostgreSQL."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from headache_core.adapters.postgres.repositories import (
    PostgresCustomerRepository,
    PostgresEventRepository,
    PostgresOrderRepository,
    PostgresRefundRepository,
    PostgresSessionRepository,
)
from headache_core.config import PostgresSettings


def create_engine(
    settings: PostgresSettings,
    *,
    application_name: str = "headache",
    statement_timeout_ms: int = 5_000,
) -> AsyncEngine:
    return create_async_engine(
        settings.dsn(),
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                # Visible en pg_stat_activity; ayuda a saber qué servicio lanza cada consulta.
                "application_name": application_name,
                # Ninguna consulta (ni la de un agente) puede bloquear la BD indefinidamente.
                "statement_timeout": str(statement_timeout_ms),
            }
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: en async no hay lazy loading implícito tras el commit.
    return async_sessionmaker(engine, expire_on_commit=False)


class SqlAlchemyUnitOfWork:
    customers: PostgresCustomerRepository
    events: PostgresEventRepository
    sessions: PostgresSessionRepository
    orders: PostgresOrderRepository
    refunds: PostgresRefundRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.customers = PostgresCustomerRepository(self._session)
        self.events = PostgresEventRepository(self._session)
        self.sessions = PostgresSessionRepository(self._session)
        self.orders = PostgresOrderRepository(self._session)
        self.refunds = PostgresRefundRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Lo no confirmado se descarta: un error a mitad de compra no deja plazas descontadas.
        await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
