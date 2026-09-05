-- Ledger schema (IMPLEMENTATION_PLAN.md section 5).
-- All budget state lives here. The DP libraries are stateless and store nothing.
-- Applied idempotently at service startup by app/ledger/init_db.py.

CREATE TABLE IF NOT EXISTS datasets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    epsilon_cap      NUMERIC(12,6) NOT NULL,           -- the hard cap (policy input)
    epsilon_spent    NUMERIC(12,6) NOT NULL DEFAULT 0, -- committed, permanent
    epsilon_reserved NUMERIC(12,6) NOT NULL DEFAULT 0, -- held, not yet spent
    delta            NUMERIC(12,10) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NOTE: the within_cap CHECK is added/dropped at runtime by init_db.py according
-- to ENFORCE_DB_CONSTRAINT. It is a safety net, NOT the enforcement mechanism --
-- enforcement is the strategy logic in app/enforcement/. It must be OFF for
-- Experiment 1 (or the naive path cannot breach) and is also kept OFF for
-- Experiment 2, so that safety is demonstrated by the strategy alone.

CREATE TABLE IF NOT EXISTS spend_records (  -- one row per reservation attempt
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id        UUID NOT NULL REFERENCES datasets(id),
    job_id            TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,            -- a retried job reuses the same key
    epsilon_reserved  NUMERIC(12,6) NOT NULL,   -- amount held at reserve time
    epsilon_committed NUMERIC(12,6),            -- actual cost; NULL until committed
    status            TEXT NOT NULL,            -- reserved|committed|released|denied
    strategy          TEXT NOT NULL,            -- naive|atomic|for_update|serializable
    epsilon_source    TEXT,                     -- which adapter produced the cost
    actor             TEXT,
    reserved_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at        TIMESTAMPTZ,              -- when committed or released
    UNIQUE (dataset_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS spend_records_dataset_status_idx
    ON spend_records (dataset_id, status);
