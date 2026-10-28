"""Reserve -- pessimistic lock, SELECT ... FOR UPDATE (plan section 6c).

The same read -> check -> write as `naive_txn`, with one change: the read takes
the row lock. A second caller blocks at its own SELECT until the first commits,
then reads the committed values. The check runs in Python, but on values nobody
else can change until this transaction ends, so the stale read behind the naive
breach cannot happen.

The cost, compared with `atomic`, is how long the lock is held: from the SELECT,
through the Python check, to COMMIT -- two more round trips than the single
conditional UPDATE. Experiment 3 measures whether that difference is visible.

Lock mode: FOR UPDATE, as named. It also conflicts with the FOR KEY SHARE lock a
foreign-key insert into `spend_records` takes on the dataset row. Here the only
such inserts come from other reserves, which are already queued at this lock, so
the stronger mode costs nothing measurable.

No transaction is held across the operation -- this returns before the adapter
is ever called.
"""

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.enforcement.base import (
    RECORD_INSERT,
    DatasetNotFound,
    ReserveOutcome,
    ReserveRequest,
    find_existing,
    register,
    replay,
)


@register("for_update")
async def reserve_for_update(
    pool: AsyncConnectionPool, req: ReserveRequest
) -> ReserveOutcome:
    existing = await find_existing(pool, req)
    if existing is not None:
        return replay(existing)

    # One transaction. The lock is taken by the read and held until the block
    # exits, so the check and the write see the same row version.
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT epsilon_spent, epsilon_reserved, epsilon_cap "
            "FROM datasets WHERE id = %s FOR UPDATE",
            (req.dataset_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise DatasetNotFound(str(req.dataset_id))

        projected = row["epsilon_spent"] + row["epsilon_reserved"] + req.epsilon_cost
        status = "denied" if projected > row["epsilon_cap"] else "reserved"

        if status == "reserved":
            # Unconditional, like `naive_txn` -- safe here only because the
            # lock guarantees the row has not moved since the read.
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
                    "for_update",
                    req.epsilon_source,
                    req.actor,
                ),
            )
            rid = (await cur.fetchone())["id"]
        except errors.UniqueViolation:
            # Concurrent reserve with the same idempotency key: roll back the
            # increment with the insert and return the winner's outcome.
            await conn.rollback()
            rid = None

    if rid is None:
        return replay(await find_existing(pool, req))

    return ReserveOutcome(
        rid,
        status,
        req.epsilon_cost,
        "for_update",
        # Read under the lock, so never stale -- compare `naive`, where the
        # same columns record the stale values behind a breach.
        observed_spent=row["epsilon_spent"],
        observed_reserved=row["epsilon_reserved"],
    )
