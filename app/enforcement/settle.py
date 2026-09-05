"""Commit and release (plan sections 6d, 6e). Shared across all strategies.

Commit cannot fail the cap check: the budget was already held at reserve time,
so settling only moves reserved -> spent. Both statements run in ONE transaction,
so a crash mid-settle leaves the reservation intact rather than half-applied.

Two guards carry the invariants:

  * `AND status = 'reserved'` on the record update makes settling idempotent --
    a retried commit finds rowcount 0 and returns the prior outcome instead of
    charging again (I4), and a release of an already-committed reservation is
    rejected (I3).
  * `SELECT ... FOR UPDATE` on the record serialises concurrent settle attempts
    for the same reservation, so the retry sees a settled row rather than
    racing the first one.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool


class ReservationNotFound(Exception):
    pass


class OverCommit(Exception):
    """actual_cost exceeded what was approved at reserve time."""


@dataclass
class SettleOutcome:
    reservation_id: UUID
    status: str  # 'committed' | 'released'
    epsilon_held: Decimal
    epsilon_committed: Decimal | None
    already_settled: bool
    dataset: dict[str, Any] | None = None


SELECT_FOR_SETTLE = """
    SELECT dataset_id, status, epsilon_reserved, epsilon_committed
      FROM spend_records
     WHERE id = %s
       FOR UPDATE
"""

DATASET_AFTER = """
    SELECT id, name, epsilon_cap, epsilon_spent, epsilon_reserved
      FROM datasets WHERE id = %s
"""


async def _settle(
    pool: AsyncConnectionPool,
    reservation_id: UUID,
    terminal_status: str,
    actual_cost: Decimal | None,
) -> SettleOutcome:
    async with pool.connection() as conn:
        cur = await conn.execute(SELECT_FOR_SETTLE, (reservation_id,))
        rec = await cur.fetchone()
        if rec is None:
            raise ReservationNotFound(str(reservation_id))

        held = rec["epsilon_reserved"]

        if rec["status"] != "reserved":
            # Already settled (or denied). Return the prior outcome; charge
            # nothing. This is I3 and the retried half of I4.
            cur = await conn.execute(DATASET_AFTER, (rec["dataset_id"],))
            return SettleOutcome(
                reservation_id=reservation_id,
                status=rec["status"],
                epsilon_held=held,
                epsilon_committed=rec["epsilon_committed"],
                already_settled=True,
                dataset=await cur.fetchone(),
            )

        if terminal_status == "committed":
            assert actual_cost is not None
            if actual_cost > held:
                # Would exceed what the cap check approved. Reject; the caller
                # must reserve again for the larger amount.
                raise OverCommit(f"actual_cost {actual_cost} exceeds held {held}")
            committed_value: Decimal | None = actual_cost
            spent_delta = actual_cost
        else:
            committed_value = None
            spent_delta = Decimal(0)

        await conn.execute(
            """
            UPDATE spend_records
               SET status = %s, epsilon_committed = %s, settled_at = now()
             WHERE id = %s AND status = 'reserved'
            """,
            (terminal_status, committed_value, reservation_id),
        )

        # If actual < held, the difference returns to available budget
        # automatically: reserved drops by the full hold, spent rises by only
        # the actual.
        await conn.execute(
            """
            UPDATE datasets
               SET epsilon_spent    = epsilon_spent + %s,
                   epsilon_reserved = epsilon_reserved - %s
             WHERE id = %s
            """,
            (spent_delta, held, rec["dataset_id"]),
        )

        cur = await conn.execute(DATASET_AFTER, (rec["dataset_id"],))
        dataset = await cur.fetchone()

    return SettleOutcome(
        reservation_id=reservation_id,
        status=terminal_status,
        epsilon_held=held,
        epsilon_committed=committed_value,
        already_settled=False,
        dataset=dataset,
    )


async def commit(
    pool: AsyncConnectionPool, reservation_id: UUID, actual_cost: Decimal
) -> SettleOutcome:
    return await _settle(pool, reservation_id, "committed", actual_cost)


async def release(pool: AsyncConnectionPool, reservation_id: UUID) -> SettleOutcome:
    return await _settle(pool, reservation_id, "released", None)
