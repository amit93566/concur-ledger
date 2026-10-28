"""locked_1phase -- the REJECTED design, built only as a comparator.

This is the design the reserve -> commit protocol exists to avoid: one
transaction that locks the dataset row, runs the operation while holding the
lock, then charges the budget and commits. It is trivially safe -- nobody else
can read or write the row until the charge lands -- and it serialises every
operation on a dataset behind the slowest one.

It is here so Experiment 3 can measure that cost instead of asserting it. With a
10 ms operation the lock hold may be cheap; with a 1 s operation throughput per
dataset is capped near 1/s, whatever N is.

It is NOT a reserve strategy and is not in the registry: there is no point
between "reserve" and "operation" at which it could return, so it exists only
as a full cycle and is reached only through /run. /reserve rejects the name.

Invariants still hold, by construction:
  * I1 -- the check and the charge happen under one row lock.
  * I2 -- the record is written as `committed` in the same transaction as the
    `epsilon_spent` increment, so the two never disagree.
  * I3 -- no `reserved` record is ever written, so no hold can be left behind; a
    crash or a failed operation rolls back the whole transaction.
  * I4 -- the same UNIQUE (dataset_id, idempotency_key) as every other path.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.adapters.base import MechanismAdapter, OperationResult
from app.enforcement.base import (
    RECORD_INSERT,
    DatasetNotFound,
    ReserveRequest,
    find_existing,
)
from app.enforcement.settle import DATASET_AFTER, OverCommit

STRATEGY = "locked_1phase"

COMMITTED_INSERT = """
    INSERT INTO spend_records
        (dataset_id, job_id, idempotency_key, epsilon_reserved, epsilon_committed,
         status, strategy, epsilon_source, actor, settled_at)
    VALUES (%s, %s, %s, %s, %s, 'committed', %s, %s, %s, now())
    RETURNING id
"""


@dataclass
class Locked1PhaseOutcome:
    record_id: UUID | None
    status: str  # 'committed' | 'denied' | a replayed prior status
    epsilon_cost: Decimal
    epsilon_committed: Decimal | None = None
    result: OperationResult | None = None
    dataset: dict[str, Any] | None = None
    replayed: bool = False
    # Milliseconds. `lock_wait` is the SELECT ... FOR UPDATE, which is where
    # this design queues; `commit` is the charge, the insert and COMMIT.
    timing_ms: dict[str, float] | None = None


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


async def run_locked_1phase(
    pool: AsyncConnectionPool,
    req: ReserveRequest,
    adapter: MechanismAdapter,
    spec: dict[str, Any],
) -> Locked1PhaseOutcome:
    existing = await find_existing(pool, req)
    if existing is not None:
        return Locked1PhaseOutcome(
            existing["id"], existing["status"], existing["epsilon_reserved"],
            replayed=True,
        )

    timing: dict[str, float] = {}
    record_id: UUID | None = None

    # ONE transaction, and the operation runs inside it. This is the whole
    # difference from every other path in the service, and it is deliberate.
    async with pool.connection() as conn:
        t = time.perf_counter()
        cur = await conn.execute(
            "SELECT epsilon_spent, epsilon_reserved, epsilon_cap "
            "FROM datasets WHERE id = %s FOR UPDATE",
            (req.dataset_id,),
        )
        row = await cur.fetchone()
        timing["lock_wait"] = _ms(t)
        if row is None:
            raise DatasetNotFound(str(req.dataset_id))

        projected = row["epsilon_spent"] + row["epsilon_reserved"] + req.epsilon_cost
        if projected > row["epsilon_cap"]:
            try:
                cur = await conn.execute(
                    RECORD_INSERT,
                    (
                        req.dataset_id, req.job_id, req.idempotency_key,
                        req.epsilon_cost, "denied", STRATEGY,
                        req.epsilon_source, req.actor,
                    ),
                )
                record_id = (await cur.fetchone())["id"]
            except errors.UniqueViolation:
                await conn.rollback()
            status = "denied"
            result = None
            actual = None
        else:
            # The lock is held across this await. Every other caller for this
            # dataset is queued at its own SELECT ... FOR UPDATE meanwhile.
            t = time.perf_counter()
            result = await adapter.run_operation(spec)
            timing["operation"] = _ms(t)

            actual = result.epsilon
            if actual > req.epsilon_cost:
                # Same rule as settle.commit. Raising rolls the transaction back.
                raise OverCommit(f"actual_cost {actual} exceeds checked {req.epsilon_cost}")

            t = time.perf_counter()
            await conn.execute(
                "UPDATE datasets SET epsilon_spent = epsilon_spent + %s WHERE id = %s",
                (actual, req.dataset_id),
            )
            try:
                cur = await conn.execute(
                    COMMITTED_INSERT,
                    (
                        req.dataset_id, req.job_id, req.idempotency_key,
                        req.epsilon_cost, actual, STRATEGY,
                        req.epsilon_source, req.actor,
                    ),
                )
                record_id = (await cur.fetchone())["id"]
            except errors.UniqueViolation:
                # A concurrent request with the same key won: undo the charge
                # with the insert and return the winner's outcome.
                await conn.rollback()
            status = "committed"

        dataset = None
        if record_id is not None:
            cur = await conn.execute(DATASET_AFTER, (req.dataset_id,))
            dataset = await cur.fetchone()
    # COMMIT happened at the block exit above; it is part of the commit time.
    if "operation" in timing:
        timing["commit"] = _ms(t)

    if record_id is None:
        existing = await find_existing(pool, req)
        return Locked1PhaseOutcome(
            existing["id"], existing["status"], existing["epsilon_reserved"],
            replayed=True, timing_ms=timing,
        )

    return Locked1PhaseOutcome(
        record_id,
        status,
        req.epsilon_cost,
        epsilon_committed=actual,
        result=result,
        dataset=dataset,
        timing_ms=timing,
    )
