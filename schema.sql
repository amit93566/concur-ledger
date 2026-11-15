-- Ledger schema (IMPLEMENTATION_PLAN.md section 5).
-- All budget state lives here. The DP libraries are stateless and store nothing.
-- Applied idempotently at service startup by app/ledger/init_db.py.

CREATE TABLE IF NOT EXISTS datasets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    epsilon_cap      NUMERIC(12,6) NOT NULL,           -- the hard cap (policy input)
    epsilon_spent    NUMERIC(12,6) NOT NULL DEFAULT 0, -- committed, permanent
    epsilon_reserved NUMERIC(12,6) NOT NULL DEFAULT 0, -- held, not yet spent
    -- RECORDED BUT NOT ENFORCED. Under (eps,delta)-DP the budget is
    -- two-dimensional, but nothing reads this column: no delta_cap,
    -- delta_spent or delta_reserved exists and no predicate references it.
    -- Phase 2. The reserve predicate generalises to a conjunction over both
    -- dimensions without changing the concurrency argument -- the same way it
    -- generalises to a per-alpha conjunction under RDP.
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
    -- naive|naive_txn|atomic|for_update|serializable.
    -- naive and naive_txn both breach; they differ only in whether the
    -- read/check/write spans one transaction or two, which is the point.
    strategy          TEXT NOT NULL,
    epsilon_source    TEXT,                     -- which adapter produced the cost
    actor             TEXT,
    reserved_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at        TIMESTAMPTZ,              -- when committed or released
    UNIQUE (dataset_id, idempotency_key)
);

-- Who settled a hold from the dashboard. Set while the row is still `reserved`,
-- so a settled row never changes and the audit log shows who acted.
ALTER TABLE spend_records ADD COLUMN IF NOT EXISTS resolved_by TEXT;

CREATE INDEX IF NOT EXISTS spend_records_dataset_status_idx
    ON spend_records (dataset_id, status);

-- Tamper-evident audit log. Two steps, on purpose:
--
--   capture  audit_capture() copies every spend_records change here in the same
--            transaction, so a change and its audit row commit or vanish
--            together. It takes no lock.
--   seal     audit_seal() later links finished rows into the hash chain.
--
-- Chaining inside the trigger does not work. A global lock there deadlocks:
-- reserve holds the dataset row and waits for it, while commit holds it and
-- waits for the dataset row. And a SERIALIZABLE transaction reads the chain
-- head from its own old snapshot, so two of them would fork the chain.
--
-- Rows are tamper-evident only once sealed, so the app seals about once a second.
CREATE TABLE IF NOT EXISTS audit_log (
    seq       BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    record_id UUID NOT NULL,                  -- no FK: it must outlive a DELETE
    op        TEXT NOT NULL,                  -- INSERT|UPDATE|DELETE
    payload   JSONB NOT NULL,                 -- the row after the change
    txid      XID8 NOT NULL DEFAULT pg_current_xact_id(),
    -- NULL until sealed. The chain follows chain_pos, not seq: a transaction
    -- can draw a low seq and still commit after higher ones are sealed.
    chain_pos BIGINT UNIQUE,
    prev_hash TEXT,
    hash      TEXT
);

CREATE INDEX IF NOT EXISTS audit_log_unsealed_idx
    ON audit_log (seq) WHERE chain_pos IS NULL;

CREATE OR REPLACE FUNCTION audit_capture() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO audit_log (record_id, op, payload)
        VALUES (OLD.id, TG_OP, to_jsonb(OLD));
        RETURN OLD;
    END IF;
    INSERT INTO audit_log (record_id, op, payload)
    VALUES (NEW.id, TG_OP, to_jsonb(NEW));
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER spend_records_audit
    AFTER INSERT OR UPDATE OR DELETE ON spend_records
    FOR EACH ROW EXECUTE FUNCTION audit_capture();

-- Seals every row whose transaction has finished; returns how many. Call it at
-- READ COMMITTED: each statement must see what the previous sealer committed.
-- The hash format is repeated in experiments/verify_audit.py, which recomputes
-- it outside the database rather than trusting this function.
CREATE OR REPLACE FUNCTION audit_seal() RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    r       audit_log;
    pos     BIGINT;
    head    TEXT;
    horizon XID8;
    sealed  BIGINT := 0;
BEGIN
    PERFORM pg_advisory_xact_lock(7342002);
    SELECT chain_pos, hash INTO pos, head
      FROM audit_log WHERE chain_pos IS NOT NULL
     ORDER BY chain_pos DESC LIMIT 1;
    pos := COALESCE(pos, 0);
    head := COALESCE(head, '');
    -- Anything older than this has committed or aborted, so no row sealed now
    -- can be overtaken by one still in flight.
    horizon := pg_snapshot_xmin(pg_current_snapshot());
    FOR r IN
        SELECT * FROM audit_log
         WHERE chain_pos IS NULL AND txid < horizon
         ORDER BY seq LIMIT 10000
    LOOP
        pos := pos + 1;
        UPDATE audit_log
           SET chain_pos = pos,
               prev_hash = head,
               hash = encode(sha256(convert_to(concat_ws('|',
                          head, r.seq, extract(epoch FROM r.at), r.record_id,
                          r.op, r.payload::text), 'UTF8')), 'hex')
         WHERE seq = r.seq
        RETURNING hash INTO head;
        sealed := sealed + 1;
    END LOOP;
    RETURN sealed;
END $$;

-- Demo console state (app/demo.py). Nothing on the reserve path reads these.
--
-- demo_setup  the admin's choice of cap, epsilon per query and number of teams.
--             One row; empty after `make reset-db`, which sends the admin to Setup.
-- demo_runs   one row per experiment run started from the console, with its
--             progress, so every window watches the same run.
-- access_log  who called the API, when, and what came back. Written only while
--             RBAC is on, so the harness experiments run without it.
CREATE TABLE IF NOT EXISTS demo_setup (
    id          INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    epsilon_cap NUMERIC(12,6) NOT NULL,
    epsilon     NUMERIC(12,6) NOT NULL,
    teams       INT NOT NULL,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS demo_runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exp         INT NOT NULL,
    mode        TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    dataset_id  UUID,                      -- no FK: reset-db deletes datasets first
    started_by  TEXT,
    status      TEXT NOT NULL,             -- running, breached, exhausted, done, stopped, interrupted, failed
    params      JSONB NOT NULL,
    progress    JSONB NOT NULL DEFAULT '[]',
    detail      TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS demo_runs_exp_idx ON demo_runs (exp, started_at DESC);

CREATE TABLE IF NOT EXISTS access_log (
    seq     BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ NOT NULL,
    actor   TEXT,
    role    TEXT,
    method  TEXT NOT NULL,
    path    TEXT NOT NULL,
    route   TEXT,
    status  INT NOT NULL,
    ms      REAL
);

-- The private dataset the console's teams query (data/adult.data, UCI Adult).
-- Loaded once at start-up by app/demo.py; read only through OpenDP
-- (app/adapters/adult.py). No endpoint returns a row of it.
CREATE TABLE IF NOT EXISTS adult (
    id             SERIAL PRIMARY KEY,
    age            INT NOT NULL,
    workclass      TEXT,
    fnlwgt         INT,
    education      TEXT,
    education_num  INT NOT NULL,
    marital_status TEXT,
    occupation     TEXT,
    relationship   TEXT,
    race           TEXT,
    sex            TEXT NOT NULL,
    capital_gain   INT,
    capital_loss   INT,
    hours_per_week INT NOT NULL,
    native_country TEXT,
    income         TEXT NOT NULL
);

-- The standard question the automatic runs ask, and the budget the two teams
-- share on the Shared dataset page.
ALTER TABLE demo_setup ADD COLUMN IF NOT EXISTS query JSONB;
ALTER TABLE demo_setup ADD COLUMN IF NOT EXISTS shared_dataset_id UUID;

-- Each answer released to a team on an Adult question, with the question
-- itself, so the data owner (admin) can set it beside the true value. The
-- answer is already public to the team that asked; the true value is computed
-- on demand and never stored or sent to a team.
CREATE TABLE IF NOT EXISTS adult_answers (
    reservation_id UUID PRIMARY KEY,
    dataset_id     UUID NOT NULL,
    actor          TEXT,
    spec           JSONB NOT NULL,
    answer         DOUBLE PRECISION,
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS adult_answers_dataset_idx ON adult_answers (dataset_id);
