"""Read side of the audit log and of held budget, for the dashboard.

Nothing here runs on the enforcement path. `verify` recomputes each hash in
Python, in the format audit_seal() uses in schema.sql, the same way
experiments/verify_audit.py does from outside the service.
"""

import hashlib
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

PAGE = 10000

AUDIT_COLUMNS = (
    "seq, at, record_id, op, payload->>'status' AS status, "
    "payload->>'strategy' AS strategy, payload->>'actor' AS actor, "
    "payload->>'resolved_by' AS resolved_by, payload->>'epsilon_reserved' AS epsilon, "
    "chain_pos, hash"
)


async def seal(conn: AsyncConnection) -> None:
    # Explicit, because LEDGER_ISOLATION can change the pool's level and
    # audit_seal() needs READ COMMITTED.
    await conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    await conn.execute("SELECT audit_seal()")


async def recent(conn: AsyncConnection, limit: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"SELECT {AUDIT_COLUMNS} FROM audit_log ORDER BY seq DESC LIMIT %s", (limit,)
    )
    return await cur.fetchall()


async def verify(pool: AsyncConnectionPool) -> dict[str, Any]:
    """Walk the chain. A connection is held per page, not for the whole walk."""
    async with pool.connection() as conn:
        await seal(conn)
    pos, head, checked = 0, "", 0
    while True:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT chain_pos, seq, extract(epoch FROM at)::text AS at, "
                "record_id::text AS record_id, op, payload::text AS payload, "
                "prev_hash, hash FROM audit_log WHERE chain_pos > %s "
                "ORDER BY chain_pos LIMIT %s",
                (pos, PAGE),
            )
            rows = await cur.fetchall()
        if not rows:
            break
        for r in rows:
            problem = None
            if r["chain_pos"] != pos + 1:
                problem = f"row missing: expected position {pos + 1}"
            elif r["prev_hash"] != head:
                problem = "prev_hash does not match the previous row"
            else:
                text = "|".join(
                    [head, str(r["seq"]), r["at"], r["record_id"], r["op"], r["payload"]]
                )
                if hashlib.sha256(text.encode()).hexdigest() != r["hash"]:
                    problem = "row contents do not match its hash"
            if problem:
                return {"valid": False, "checked": checked, "broken_at": pos + 1,
                        "seq": r["seq"], "reason": problem, "head": head, "unsealed": None}
            pos, head, checked = r["chain_pos"], r["hash"], checked + 1
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM audit_log WHERE chain_pos IS NULL"
        )
        unsealed = (await cur.fetchone())["n"]
    return {"valid": True, "checked": checked, "broken_at": None, "seq": None,
            "reason": None, "head": head, "unsealed": unsealed}


async def stale_holds(
    conn: AsyncConnection, older_than_s: float, limit: int
) -> tuple[int, list[dict[str, Any]]]:
    where = (
        "r.status = 'reserved' AND r.reserved_at < now() - make_interval(secs => %s)"
    )
    cur = await conn.execute(
        f"SELECT count(*) AS n FROM spend_records r WHERE {where}", (older_than_s,)
    )
    total = (await cur.fetchone())["n"]
    cur = await conn.execute(
        "SELECT r.id, r.dataset_id, d.name AS dataset_name, r.job_id, "
        "r.epsilon_reserved, r.strategy, r.actor, r.reserved_at, "
        "extract(epoch FROM now() - r.reserved_at) AS age_s "
        f"FROM spend_records r JOIN datasets d ON d.id = r.dataset_id WHERE {where} "
        "ORDER BY r.reserved_at LIMIT %s",
        (older_than_s, limit),
    )
    return total, await cur.fetchall()
