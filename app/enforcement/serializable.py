"""Reserve -- SERIALIZABLE isolation with retry (plan section 6c).

The same read -> check -> write as `naive_txn` -- a plain SELECT, no lock, the
check in Python -- but run at SERIALIZABLE. Postgres's serializable snapshot
isolation does not stop concurrent readers; it detects that two transactions
read and wrote the same row in an order no serial execution could produce, and
aborts one with SQLSTATE 40001. Correctness therefore comes from the abort plus
the retry, not from waiting.

That makes retries the cost to measure: every 40001 is a reservation's worth of
work thrown away. They are counted and returned in `ReserveOutcome.retries`.

Retry policy, fixed before Experiment 3 so the throughput number means something:
at most MAX_RETRIES retries, jittered exponential backoff between them. An
unbounded immediate retry on one hot row can livelock, and its throughput would
measure the retry loop rather than the strategy. A caller that exhausts the
retries gets RetriesExhausted -- reported as a give-up, never as a denial,
because nothing was decided.

The level is set per transaction (`SET TRANSACTION`), not on the connection, so
the pooled connection goes back at the pool's READ COMMITTED default.
"""

import asyncio
import random

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.enforcement.base import (
    RECORD_INSERT,
    DatasetNotFound,
    ReserveOutcome,
    ReserveRequest,
    RetriesExhausted,
    find_existing,
    register,
    replay,
)

MAX_RETRIES = 10
BACKOFF_BASE_S = 0.001
BACKOFF_CAP_S = 0.050


def backoff_s(attempt: int) -> float:
    """Exponential, capped, with jitter in [0.5, 1) so retriers spread out."""
    return min(BACKOFF_BASE_S * 2**attempt, BACKOFF_CAP_S) * random.uniform(0.5, 1.0)


@register("serializable")
async def reserve_serializable(
    pool: AsyncConnectionPool, req: ReserveRequest
) -> ReserveOutcome:
    existing = await find_existing(pool, req)
    if existing is not None:
        return replay(existing)

    for attempt in range(MAX_RETRIES + 1):
        try:
            # The 40001 can be raised by any statement OR by the COMMIT on block
            # exit, so the whole transaction sits inside the try.
            async with pool.connection() as conn:
                await conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cur = await conn.execute(
                    "SELECT epsilon_spent, epsilon_reserved, epsilon_cap "
                    "FROM datasets WHERE id = %s",
                    (req.dataset_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    raise DatasetNotFound(str(req.dataset_id))

                projected = (
                    row["epsilon_spent"] + row["epsilon_reserved"] + req.epsilon_cost
                )
                status = "denied" if projected > row["epsilon_cap"] else "reserved"

                if status == "reserved":
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
                            "serializable",
                            req.epsilon_source,
                            req.actor,
                        ),
                    )
                    rid = (await cur.fetchone())["id"]
                except errors.UniqueViolation:
                    await conn.rollback()
                    rid = None
        except errors.SerializationFailure:
            if attempt == MAX_RETRIES:
                break
            await asyncio.sleep(backoff_s(attempt))
            continue

        if rid is None:
            return replay(await find_existing(pool, req))

        return ReserveOutcome(
            rid,
            status,
            req.epsilon_cost,
            "serializable",
            retries=attempt,
            # The snapshot this attempt decided on. SSI guarantees that a
            # committed attempt's snapshot was not invalidated by a concurrent
            # writer, so unlike `naive` these are not stale.
            observed_spent=row["epsilon_spent"],
            observed_reserved=row["epsilon_reserved"],
        )

    raise RetriesExhausted(MAX_RETRIES)
