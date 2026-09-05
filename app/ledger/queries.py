"""Read-side ledger access: dataset state, records, and the invariant checks.

The invariant checks (I1-I4, plan section 5) are computed in SQL against the
committed state, so the harness and the crash test verify the same thing the
demo shows on screen.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

DATASET_COLUMNS = (
    "id, name, epsilon_cap, epsilon_spent, epsilon_reserved, delta, created_at"
)

RECORD_COLUMNS = (
    "id, dataset_id, job_id, idempotency_key, epsilon_reserved, epsilon_committed, "
    "status, strategy, epsilon_source, actor, reserved_at, settled_at"
)


async def create_dataset(
    conn: AsyncConnection,
    name: str,
    epsilon_cap: Decimal,
    epsilon_spent: Decimal = Decimal(0),
    delta: Decimal = Decimal(0),
) -> dict[str, Any]:
    cur = await conn.execute(
        "INSERT INTO datasets (name, epsilon_cap, epsilon_spent, delta) "
        f"VALUES (%s, %s, %s, %s) RETURNING {DATASET_COLUMNS}",
        (name, epsilon_cap, epsilon_spent, delta),
    )
    return await cur.fetchone()


async def get_dataset(conn: AsyncConnection, dataset_id: UUID) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"SELECT {DATASET_COLUMNS} FROM datasets WHERE id = %s", (dataset_id,)
    )
    return await cur.fetchone()


async def list_datasets(conn: AsyncConnection) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {DATASET_COLUMNS} FROM datasets ORDER BY created_at DESC"
    )
    return await cur.fetchall()


async def seed_dataset(
    conn: AsyncConnection,
    dataset_id: UUID,
    epsilon_spent: Decimal,
    epsilon_reserved: Decimal,
) -> dict[str, Any] | None:
    """Test-only: put the budget in a near-cap precondition.

    Experiment 1 needs the budget to start near the cap; without that there is
    room to succeed legitimately and no breach appears.

    Crucially this writes matching spend_records rows, not just the running
    columns. Forcing `epsilon_spent = 6` with no committed record behind it
    would itself be an I2 violation, and would make the invariant read as
    failing in every seeded run -- masking any real drift it exists to detect.
    """
    cur = await conn.execute(
        "UPDATE datasets SET epsilon_spent = %s, epsilon_reserved = %s "
        f"WHERE id = %s RETURNING {DATASET_COLUMNS}",
        (epsilon_spent, epsilon_reserved, dataset_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None

    if epsilon_spent > 0:
        await conn.execute(
            "INSERT INTO spend_records (dataset_id, job_id, idempotency_key, "
            "epsilon_reserved, epsilon_committed, status, strategy, epsilon_source, "
            "settled_at) VALUES (%s, 'seed', 'seed-committed', %s, %s, 'committed', "
            "'seed', 'seed', now())",
            (dataset_id, epsilon_spent, epsilon_spent),
        )
    if epsilon_reserved > 0:
        await conn.execute(
            "INSERT INTO spend_records (dataset_id, job_id, idempotency_key, "
            "epsilon_reserved, status, strategy, epsilon_source) "
            "VALUES (%s, 'seed', 'seed-reserved', %s, 'reserved', 'seed', 'seed')",
            (dataset_id, epsilon_reserved),
        )
    return row


async def get_record(conn: AsyncConnection, record_id: UUID) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"SELECT {RECORD_COLUMNS} FROM spend_records WHERE id = %s", (record_id,)
    )
    return await cur.fetchone()


async def list_records(
    conn: AsyncConnection, dataset_id: UUID, limit: int = 500
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {RECORD_COLUMNS} FROM spend_records WHERE dataset_id = %s "
        "ORDER BY reserved_at ASC LIMIT %s",
        (dataset_id, limit),
    )
    return await cur.fetchall()


async def check_invariants(
    conn: AsyncConnection, dataset_id: UUID
) -> dict[str, Any] | None:
    """I1-I4 for one dataset, computed from committed state."""
    cur = await conn.execute(
        """
        SELECT d.epsilon_cap,
               d.epsilon_spent,
               d.epsilon_reserved,
               COALESCE((SELECT SUM(epsilon_committed) FROM spend_records
                          WHERE dataset_id = d.id AND status = 'committed'), 0)
                   AS records_committed,
               COALESCE((SELECT SUM(epsilon_reserved) FROM spend_records
                          WHERE dataset_id = d.id AND status = 'reserved'), 0)
                   AS records_reserved,
               (SELECT count(*) FROM spend_records
                 WHERE dataset_id = d.id
                   AND status NOT IN ('reserved','committed','released','denied'))
                   AS bad_status_rows,
               (SELECT count(*) FROM spend_records
                 WHERE dataset_id = d.id
                   AND ((status = 'committed' AND epsilon_committed IS NULL)
                     OR (status <> 'committed' AND epsilon_committed IS NOT NULL)))
                   AS inconsistent_settlement_rows,
               (SELECT count(*) FROM (
                    SELECT 1 FROM spend_records WHERE dataset_id = d.id
                     GROUP BY idempotency_key HAVING count(*) > 1) x)
                   AS duplicate_key_rows
          FROM datasets d
         WHERE d.id = %s
        """,
        (dataset_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None

    cap = row["epsilon_cap"]
    spent = row["epsilon_spent"]
    reserved = row["epsilon_reserved"]
    total = spent + reserved

    i1 = total <= cap
    i2 = spent == row["records_committed"] and reserved == row["records_reserved"]
    # I3 is a property over time (a settled row never changes again); the
    # snapshot half of it is that settled rows are internally consistent. The
    # behavioural half is exercised by crash_test.py's release-after-commit.
    i3_snapshot = row["bad_status_rows"] == 0 and row["inconsistent_settlement_rows"] == 0
    i4 = row["duplicate_key_rows"] == 0

    return {
        "epsilon_cap": cap,
        "epsilon_spent": spent,
        "epsilon_reserved": reserved,
        "total": total,
        "overshoot": max(Decimal(0), total - cap),
        "records_committed": row["records_committed"],
        "records_reserved": row["records_reserved"],
        "i1_cap_safety": i1,
        "i2_ledger_agreement": i2,
        "i3_terminal_states_snapshot": i3_snapshot,
        "i4_exactly_once": i4,
        "all_hold": i1 and i2 and i3_snapshot and i4,
    }


async def breaching_datasets(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Datasets whose running columns already exceed their cap.

    Non-empty only after an Experiment 1 run, which is exactly when re-adding
    the within_cap CHECK will fail.
    """
    cur = await conn.execute(
        "SELECT id, name, epsilon_cap, epsilon_spent, epsilon_reserved, "
        "epsilon_spent + epsilon_reserved - epsilon_cap AS overshoot "
        "FROM datasets WHERE epsilon_spent + epsilon_reserved > epsilon_cap "
        "ORDER BY overshoot DESC"
    )
    return await cur.fetchall()


async def status_counts(conn: AsyncConnection, dataset_id: UUID) -> dict[str, int]:
    cur = await conn.execute(
        "SELECT status, count(*) AS n FROM spend_records WHERE dataset_id = %s "
        "GROUP BY status",
        (dataset_id,),
    )
    return {r["status"]: r["n"] for r in await cur.fetchall()}
