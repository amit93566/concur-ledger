"""Reserve -- naive, but inside ONE transaction. Also breaches.

This strategy exists to answer one objection, and it is the most important
objection the project faces: *"your naive path used two separate transactions --
you built it to fail."*

The answer is that the transaction boundary is not what makes `naive` unsafe.
Here the read, the check and the write are wrapped in a single transaction, which
is what a competent engineer writes when told "make it transactional", and the
cap is breached exactly as before. What matters is not how many transactions the
work is spread across; it is that the decision is taken in the application, on a
value the database is free to change before the write lands.

Why one transaction does not help at READ COMMITTED:

  * a plain SELECT takes no row lock, so nothing stops a concurrent writer;
  * each statement in a READ COMMITTED transaction takes a FRESH snapshot, so
    being inside a transaction gives the later UPDATE no memory of what the
    earlier SELECT saw;
  * the UPDATE carries no predicate, so there is nothing for Postgres to
    re-evaluate against the newly committed row version -- the re-check that
    makes `atomic` safe has nothing to check.

Contrast with `atomic`, which differs from this file in exactly one respect: the
cap condition is in the WHERE clause of the write.

Note this is NOT a lost update. Every increment applies and none is overwritten;
the running total is exactly the sum of all the increments, and I2 holds
throughout. The anomaly is write skew: each transaction's decision was valid
against the state it read, and the invariant is violated only by the combination.

Do NOT "fix" this file. If it cannot breach, it proves nothing.
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


@register("naive_txn")
async def reserve_naive_single_txn(
    pool: AsyncConnectionPool, req: ReserveRequest
) -> ReserveOutcome:
    existing = await find_existing(pool, req)
    if existing is not None:
        return replay(existing)

    # ONE transaction for the read, the check and the write. Note what is absent
    # from the SELECT: no FOR UPDATE, no lock of any kind. That, not the
    # transaction count, is what leaves the race open.
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT epsilon_spent, epsilon_reserved, epsilon_cap "
            "FROM datasets WHERE id = %s",
            (req.dataset_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise DatasetNotFound(str(req.dataset_id))

        # The race window -- here it sits INSIDE an open transaction, which is
        # the only behavioural difference from `naive` and does not help.
        # Default 0: the breach reproduces without it.
        if settings.naive_race_delay_ms > 0:
            await asyncio.sleep(settings.naive_race_delay_ms / 1000.0)

        # The check, in the application, on values a concurrent writer may
        # already have invalidated.
        projected = row["epsilon_spent"] + row["epsilon_reserved"] + req.epsilon_cost
        status = "denied" if projected > row["epsilon_cap"] else "reserved"

        if status == "reserved":
            # Unconditional: no predicate, so nothing is re-evaluated against the
            # newly committed row version.
            await conn.execute(
                "UPDATE datasets SET epsilon_reserved = epsilon_reserved + %s "
                "WHERE id = %s",
                (req.epsilon_cost, req.dataset_id),
            )

        try:
            cur = await conn.execute(
                RECORD_INSERT,
                (
                    req.dataset_id,
                    req.job_id,
                    req.idempotency_key,
                    req.epsilon_cost,
                    status,
                    "naive_txn",
                    req.epsilon_source,
                    req.actor,
                ),
            )
            rid = (await cur.fetchone())["id"]
        except errors.UniqueViolation:
            # The increment rolls back with the record insert, so no drift
            # between the running columns and the record rows (I2) can arise.
            await conn.rollback()
            rid = None

    if rid is None:
        return replay(await find_existing(pool, req))

    return ReserveOutcome(
        rid,
        status,
        req.epsilon_cost,
        "naive_txn",
        # The PRE-write read, exactly as in `naive`: the stale values this caller
        # decided on. Evidence of the race, not a cause of it.
        observed_spent=row["epsilon_spent"],
        observed_reserved=row["epsilon_reserved"],
    )
