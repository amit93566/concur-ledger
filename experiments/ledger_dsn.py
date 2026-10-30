"""Direct connection string for scripts that talk to Postgres, not the service.

Same resolution as docker compose: DB_PORT from the environment, else from
.env, else 5432. LEDGER_DSN overrides the whole thing.
"""

import os
from pathlib import Path


def ledger_dsn() -> str:
    if os.getenv("LEDGER_DSN"):
        return os.environ["LEDGER_DSN"]
    port = os.getenv("DB_PORT")
    env = Path(__file__).resolve().parents[1] / ".env"
    if port is None and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("DB_PORT="):
                port = line.split("=", 1)[1].strip()
    return f"postgresql://postgres:postgres@localhost:{port or 5432}/ledger"
