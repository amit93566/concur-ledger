"""DEMO -- edit one sealed audit row and watch verification fail at that row.

Straight to Postgres, as an attacker with database access would be. The edit is
undone at the end, so the chain is left intact.

    python experiments/demo_audit.py
"""

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_dsn import ledger_dsn  # noqa: E402
from verify_audit import report, verify  # noqa: E402


def main():
    with psycopg.connect(ledger_dsn(), autocommit=True) as conn:
        ds = conn.execute(
            "INSERT INTO datasets (name, epsilon_cap) VALUES ('demo-audit', 10) "
            "RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO spend_records (dataset_id, job_id, idempotency_key, "
            "epsilon_reserved, status, strategy) "
            "VALUES (%s, 'demo', 'demo', 2, 'reserved', 'atomic')", (ds,))
        conn.execute(
            "UPDATE spend_records SET status = 'committed', epsilon_committed = 2 "
            "WHERE dataset_id = %s", (ds,))
        conn.execute("UPDATE datasets SET epsilon_spent = 2 WHERE id = %s", (ds,))

        print("before tampering:")
        report(verify(conn))

        # The commit that spent 2 epsilon: the row an attacker would want gone.
        seq, original = conn.execute(
            "SELECT seq, payload::text FROM audit_log WHERE op = 'UPDATE' "
            "AND payload->>'dataset_id' = %s ORDER BY seq DESC LIMIT 1",
            (str(ds),)).fetchone()
        print(f"\nediting seq {seq}: epsilon_committed 2 -> 0")
        conn.execute(
            "UPDATE audit_log SET payload = jsonb_set(payload, "
            "'{epsilon_committed}', '0') WHERE seq = %s", (seq,))

        print("after tampering:")
        tampered = verify(conn)
        report(tampered)

        # As text: through Python, 2.000000 would come back as 2.0 and the row
        # would still not match its hash.
        conn.execute("UPDATE audit_log SET payload = %s::jsonb WHERE seq = %s",
                     (original, seq))
        print("\nedit undone:")
        restored = verify(conn)
        report(restored)

        conn.execute("DELETE FROM spend_records WHERE dataset_id = %s", (ds,))
        conn.execute("DELETE FROM datasets WHERE id = %s", (ds,))

    if tampered.get("seq") != seq or restored["broken_at"] is not None:
        sys.exit("FAILED: verification did not stop at the edited row")


if __name__ == "__main__":
    main()
