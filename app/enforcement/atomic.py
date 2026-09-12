"""Reserve -- atomic conditional update (plan section 6b). The primary safe path.

The whole difference from `naive` is that the check moved out of Python and into
the WHERE clause of the write. One statement: the check and the write cannot
interleave, because there is nothing between them to interleave with. Postgres
serialises the concurrent updates to the row and re-evaluates the predicate
against each writer's fresh version, so exactly the reservations that fit
succeed and the rest see rowcount 0.

No lock is held beyond the statement, and no transaction is held across the
operation -- this returns before the adapter is ever called.
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

RESERVE_SQL = """
    UPDATE datasets
       SET epsilon_reserved = epsilon_reserved + %s
     WHERE id = %s
       AND epsilon_spent + epsilon_reserved + %s <= epsilon_cap
    RETURNING epsilon_spent, epsilon_reserved
"""


@register("atomic")
async def reserve_atomic(
    pool: AsyncConnectionPool, req: ReserveRequest
) -> ReserveOutcome:
    existing = await find_existing(pool, req)
    if existing is not None:
        return replay(existing)

    # One transaction: conditional update + the record row for it.
    async with pool.connection() as conn:
        cur = await conn.execute(
            RESERVE_SQL, (req.epsilon_cost, req.dataset_id, req.epsilon_cost)
        )
        updated = await cur.fetchone()

        if updated is None:
            # rowcount 0. Either the dataset does not exist, or the predicate
            # failed -- distinguish, so a denial is never confused with a typo.
            cur = await conn.execute(
                "SELECT 1 FROM datasets WHERE id = %s", (req.dataset_id,)
            )
            if await cur.fetchone() is None:
                raise DatasetNotFound(str(req.dataset_id))
            status = "denied"
        else:
            status = "reserved"

        try:
            cur = await conn.execute(
                RECORD_INSERT,
                (
                    req.dataset_id,
                    req.job_id,
                    req.idempotency_key,
                    req.epsilon_cost,
                    status,
                    "atomic",
                    req.epsilon_source,
                    req.actor,
                ),
            )
            rid = (await cur.fetchone())["id"]
        except errors.UniqueViolation:
            # Concurrent reserve with the same idempotency key. Roll the whole
            # transaction back explicitly -- including the increment -- and
            # return the outcome the winner recorded.
            await conn.rollback()
            rid = None

    if rid is None:
        return replay(await find_existing(pool, req))

    return ReserveOutcome(
        rid,
        status,
        req.epsilon_cost,
        "atomic",
        # Post-update values from RETURNING, so unlike `naive` these are never
        # stale: the database evaluated the predicate against exactly this
        # version of the row. `None` on a denial, where nothing was returned.
        observed_spent=updated["epsilon_spent"] if updated else None,
        observed_reserved=updated["epsilon_reserved"] if updated else None,
    )
