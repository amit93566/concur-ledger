"""Runtime configuration.

Every experiment-relevant switch is an environment variable, so a run can be
described completely by its environment and reproduced from it.
"""

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    enforce_db_constraint: bool
    pool_min_size: int
    pool_max_size: int
    naive_race_delay_ms: float
    allow_admin: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/ledger",
            ),
            # Safety net during development; MUST be off for Experiment 1.
            enforce_db_constraint=_flag("ENFORCE_DB_CONSTRAINT", True),
            pool_min_size=int(os.getenv("POOL_MIN_SIZE", "10")),
            # Must be >= the harness worker count, or requests serialise at the
            # pool instead of at the database and the experiment measures the
            # wrong thing.
            pool_max_size=int(os.getenv("POOL_MAX_SIZE", "120")),
            # Simulated application-level work inside the naive read->write gap.
            # DEFAULT 0: the breach must reproduce without it. Recorded in the
            # results CSV whenever it is non-zero.
            naive_race_delay_ms=float(os.getenv("NAIVE_RACE_DELAY_MS", "0")),
            # Test-only endpoints (seeding, constraint toggle, fault injection).
            allow_admin=_flag("ALLOW_ADMIN_ENDPOINTS", True),
        )


settings = Settings.from_env()
