"""PostgreSQL access (psycopg 3 async pool).

The backend connects as `backend_app`: it can read tables and call api.* functions, nothing else.
Business writes always go through api.* functions so the database enforces the rules.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings

AsyncConn = AsyncConnection[DictRow]


class Database:
    def __init__(self, settings: Settings) -> None:
        self._pool: AsyncConnectionPool[AsyncConn] = AsyncConnectionPool(
            conninfo=settings.database_url.get_secret_value(),
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            kwargs={"row_factory": dict_row, "application_name": "novatech-backend", "autocommit": True},
            open=False,
            timeout=10,
        )

    async def open(self) -> None:
        await self._pool.open(wait=False)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConn]:
        async with self._pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def snapshot(self) -> AsyncIterator[AsyncConn]:
        """Read-only REPEATABLE READ transaction: several queries see one consistent snapshot."""
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield conn

    async def fetch_one(self, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None) -> DictRow | None:
        async with self.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchone()

    async def fetch_all(self, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None) -> list[DictRow]:
        async with self.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall()

    async def ping(self) -> bool:
        row = await self.fetch_one("SELECT 1 AS ok")
        return bool(row and row["ok"] == 1)
