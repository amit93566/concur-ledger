"""Reserve-strategy interface and registry.

The `strategy` request parameter selects the reserve implementation, so every
strategy runs against identical load. A name in ALL_STRATEGIES that is not yet
registered fails loudly rather than silently falling back to something safe.

`locked_1phase` is deliberately NOT a reserve strategy: it holds its transaction
across the operation, so it exists only as a full cycle (see locked_1phase.py).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

ALL_STRATEGIES = ("naive", "naive_txn", "atomic", "for_update", "serializable")
# Reachable through /run only; see locked_1phase.py.
FULL_CYCLE_ONLY = ("locked_1phase",)
IMPLEMENTED_STRATEGIES = (
    "naive",
    "naive_txn",
    "atomic",
    "for_update",
    "serializable",
)


class StrategyNotImplemented(Exception):
    """A named strategy that is real, but scheduled for a later phase."""


class DatasetNotFound(Exception):
    pass


class RetriesExhausted(Exception):
    """`serializable` gave up after its bounded retries. Nothing was decided:
    this is a give-up, never a denial."""

    def __init__(self, retries: int):
        super().__init__(f"serialization failure persisted after {retries} retries")
        self.retries = retries


@dataclass
class ReserveRequest:
    dataset_id: UUID
    job_id: str
    idempotency_key: str
    epsilon_cost: Decimal
    strategy: str
    epsilon_source: str | None = None
    actor: str | None = None


@dataclass
class ReserveOutcome:
    reservation_id: UUID | None
    status: str  # 'reserved' | 'denied'
    epsilon_reserved: Decimal
    strategy: str
    replayed: bool = False  # this idempotency key had already been used
    retries: int = 0  # 40001 retries before this outcome; `serializable` only
    dataset: dict[str, Any] | None = None
    # Read-only telemetry: the (spent, reserved) this caller actually observed
    # when it made its decision. For `naive` these are the STALE values behind a
    # breach, which is what makes the demo trace explanatory rather than just
    # assertive. Recorded from values already in memory; nothing branches on
    # them and no extra query is issued.
    observed_spent: Decimal | None = None
    observed_reserved: Decimal | None = None


ReserveFn = Callable[[AsyncConnectionPool, ReserveRequest], Awaitable[ReserveOutcome]]

_REGISTRY: dict[str, ReserveFn] = {}


def register(name: str) -> Callable[[ReserveFn], ReserveFn]:
    def decorate(fn: ReserveFn) -> ReserveFn:
        _REGISTRY[name] = fn
        return fn

    return decorate


def get_strategy(name: str) -> ReserveFn:
    if name in _REGISTRY:
        return _REGISTRY[name]
    if name in ALL_STRATEGIES:
        raise StrategyNotImplemented(
            f"strategy '{name}' is scheduled for Phase 2 (the cost sweep); "
            f"implemented now: {', '.join(IMPLEMENTED_STRATEGIES)}"
        )
    if name in FULL_CYCLE_ONLY:
        raise ValueError(
            f"'{name}' holds its transaction across the operation, so it has no "
            "separate reserve step; it is available only through /run"
        )
    raise ValueError(f"unknown strategy '{name}'")


RECORD_INSERT = """
    INSERT INTO spend_records
        (dataset_id, job_id, idempotency_key, epsilon_reserved, status,
         strategy, epsilon_source, actor)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""


async def find_existing(pool: AsyncConnectionPool, req: ReserveRequest):
    """Idempotency pre-check: has this (dataset, key) already been decided?

    A retried reserve returns the prior outcome rather than creating a second
    reservation (I4). The UNIQUE constraint is the real guarantee; this read is
    the fast path that avoids relying on an exception.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, status, epsilon_reserved, strategy FROM spend_records "
            "WHERE dataset_id = %s AND idempotency_key = %s",
            (req.dataset_id, req.idempotency_key),
        )
        return await cur.fetchone()


def replay(row) -> ReserveOutcome:
    return ReserveOutcome(
        reservation_id=row["id"],
        status=row["status"],
        epsilon_reserved=row["epsilon_reserved"],
        strategy=row["strategy"],
        replayed=True,
    )
