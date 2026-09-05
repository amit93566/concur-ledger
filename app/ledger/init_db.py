"""Schema application and the ENFORCE_DB_CONSTRAINT flag.

The `within_cap` CHECK is a loud safety net during development, but it MUST be
dropped for Experiment 1 -- with it on, the naive path's overspending UPDATE is
rejected by the database and no breach is observable. Experiment 2 also runs
with it off, so that the absence of a breach is attributable to the strategy and
not to the database backstopping it.
"""

from pathlib import Path

from psycopg import AsyncConnection

CONSTRAINT_NAME = "within_cap"
CONSTRAINT_SQL = (
    "ALTER TABLE datasets ADD CONSTRAINT within_cap "
    "CHECK (epsilon_spent + epsilon_reserved <= epsilon_cap)"
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"


async def apply_schema(conn: AsyncConnection) -> None:
    await conn.execute(SCHEMA_PATH.read_text())


async def constraint_enabled(conn: AsyncConnection) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM pg_constraint WHERE conname = %s AND conrelid = 'datasets'::regclass",
        (CONSTRAINT_NAME,),
    )
    return await cur.fetchone() is not None


async def set_constraint(conn: AsyncConnection, enabled: bool) -> bool:
    """Add or drop the CHECK. Returns the resulting state.

    Adding it can fail if the table already violates it (i.e. a breach is
    already recorded). That is surfaced, not swallowed: it is evidence.
    """
    if enabled:
        if not await constraint_enabled(conn):
            await conn.execute(CONSTRAINT_SQL)
    else:
        await conn.execute(
            f"ALTER TABLE datasets DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
        )
    return await constraint_enabled(conn)
