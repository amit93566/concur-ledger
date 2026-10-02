"""Async connection pool.

Transaction boundaries are the `async with pool.connection()` blocks in
app/enforcement/. Each block is exactly one transaction: psycopg commits on a
clean exit and rolls back on an exception. Nothing else opens a transaction, so
"is a transaction held across run_operation?" is answerable by reading those
blocks -- and none of them contains an adapter call.

The isolation level is pinned here rather than inherited, because the safety
argument for the `atomic` strategy is specific to READ COMMITTED. See below.
"""

from psycopg import IsolationLevel
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None

# The isolation level the correctness argument depends on.
#
# Under READ COMMITTED, when a concurrent UPDATE to the same row commits first,
# Postgres re-evaluates the second updater's WHERE clause against the *newly
# committed* row version and proceeds only if it still matches (PostgreSQL
# manual 13.2.1). That re-evaluation is exactly what makes the atomic strategy's
# cap predicate sound: it is checked against committed state, never a stale
# snapshot, so a reservation that no longer fits returns rowcount 0.
#
# This is NOT the behaviour at REPEATABLE READ or SERIALIZABLE. There the same
# situation raises a serialization failure (SQLSTATE 40001) instead of
# re-checking, so `atomic` would start erroring rather than denying. Those levels
# are safe too, but only with a retry loop -- which is the Phase 2 `serializable`
# strategy, and it will set its own level per transaction rather than change
# this default.
#
# The level was previously inherited from the server, whose default is also READ
# COMMITTED: pinning it changes no behaviour and invalidates no earlier result.
# It makes a dependency the results rest on explicit instead of accidental.
LEDGER_ISOLATION = IsolationLevel.READ_COMMITTED


async def _configure(conn) -> None:
    # On async connections this is a coroutine, not a settable property.
    await conn.set_isolation_level(LEDGER_ISOLATION)


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            kwargs={"row_factory": dict_row},
            configure=_configure,
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
