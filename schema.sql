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
