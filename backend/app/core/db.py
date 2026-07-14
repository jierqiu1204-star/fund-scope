from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseManager:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url, future=True)
        if self.engine.url.get_backend_name() == "sqlite":
            event.listen(self.engine.sync_engine, "connect", _sqlite_disable_legacy_transactions)
            event.listen(self.engine.sync_engine, "begin", _sqlite_begin)
        self._session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


def _sqlite_disable_legacy_transactions(dbapi_connection, _connection_record) -> None:
    dbapi_connection.isolation_level = None


def _sqlite_begin(connection) -> None:
    connection.exec_driver_sql("BEGIN")


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session() as session:
        yield session
