"""Reserve -- naive strategy (plan section 6a). MUST be able to breach.

This is a faithful read -> check -> write, not a strawman:

  * statement 1 reads the running columns in its own transaction, which then
    ends -- no lock is carried forward;
  * the cap check happens in Python, on values that are already stale by the
    time they are used;
  * statement 2 opens a *new* transaction and applies the increment
    unconditionally, because the application already "knows" it fits.

The gap between the two transactions is the race window. Concurrent callers read
the same values, all pass the check, and all write. This is exactly how a
plausible first implementation is written, which is the point: nothing here is
sabotaged, and the same code is safe under no concurrency.

Do NOT "fix" this file. If the naive path cannot breach, Experiment 1 is void.
"""

import asyncio

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.enforcement.base import (
    RECORD_INSERT,
    DatasetNotFound,
    ReserveOutcome,
    ReserveRequest,
    find_existing,
    register,
    replay,
)


@register("naive")
async def reserve_naive(
    pool: AsyncConnectionPool, req: ReserveRequest
) -> ReserveOutcome:
    existing = await find_existing(pool, req)
    if existing is not None:
        return replay(existing)

    # --- statement 1: read (transaction opens and closes) --------------------
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT epsilon_spent, epsilon_reserved, epsilon_cap "
            "FROM datasets WHERE id = %s",
            (req.dataset_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise DatasetNotFound(str(req.dataset_id))

    # --- the race window -----------------------------------------------------
    # Stands in for whatever application work a real service does between
    # deciding and writing (validation, logging, an auth callout). Default 0:
    # the breach reproduces without it, and its value is recorded per run.
    if settings.naive_race_delay_ms > 0:
        await asyncio.sleep(settings.naive_race_delay_ms / 1000.0)

    # --- the check, in the application, on stale values ----------------------
    projected = row["epsilon_spent"] + row["epsilon_reserved"] + req.epsilon_cost
    if projected > row["epsilon_cap"]:
        async with pool.connection() as conn:
            try:
                cur = await conn.execute(
                    RECORD_INSERT,
                    (
                        req.dataset_id,
                        req.job_id,
                        req.idempotency_key,
                        req.epsilon_cost,
                        "denied",
                        "naive",
                        req.epsilon_source,
                        req.actor,
                    ),
                )
                rid = (await cur.fetchone())["id"]
            except errors.UniqueViolation:
                await conn.rollback()
                rid = None
        if rid is None:
            return replay(await find_existing(pool, req))
        return ReserveOutcome(rid, "denied", req.epsilon_cost, "naive")

    # --- statement 2: a separate transaction, no lock, unconditional write ---
    async with pool.connection() as conn:
        try:
            await conn.execute(
                "UPDATE datasets SET epsilon_reserved = epsilon_reserved + %s "
                "WHERE id = %s",
                (req.epsilon_cost, req.dataset_id),
            )
            cur = await conn.execute(
                RECORD_INSERT,
                (
                    req.dataset_id,
                    req.job_id,
                    req.idempotency_key,
                    req.epsilon_cost,
                    "reserved",
                    "naive",
                    req.epsilon_source,
                    req.actor,
                ),
            )
            rid = (await cur.fetchone())["id"]
        except errors.UniqueViolation:
            # Both statements roll back together, so no drift between the
            # running columns and the record rows (I2) can arise here.
            await conn.rollback()
            rid = None
    if rid is None:
        return replay(await find_existing(pool, req))

    return ReserveOutcome(rid, "reserved", req.epsilon_cost, "naive")
