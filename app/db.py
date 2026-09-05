"""Async connection pool.

Transaction boundaries are the `async with pool.connection()` blocks in
app/enforcement/. Each block is exactly one transaction: psycopg commits on a
clean exit and rolls back on an exception. Nothing else opens a transaction, so
"is a transaction held across run_operation?" is answerable by reading those
blocks -- and none of them contains an adapter call.
"""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await _pool.open(wait=True, timeout=30)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("connection pool is not open")
    return _pool
