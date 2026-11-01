"""Walk the audit hash chain and report the first broken link.

Recomputes every hash here rather than calling a database function, so a
tampered function cannot vouch for a tampered row. The format must match
audit_seal() in schema.sql.

Limits: a chain proves rows were not changed after sealing. Rows not yet sealed
are unprotected, and someone with full database access can rewrite the whole
chain, or cut rows off its end. Comparing the printed head with a copy kept
elsewhere is what catches those two.

    python experiments/verify_audit.py
"""

import hashlib
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_dsn import ledger_dsn  # noqa: E402

PAGE = 10000


def verify(conn) -> dict:
    """Returns checked, head, unsealed, and broken_at/reason if the chain breaks."""
    conn.execute("SELECT audit_seal()")
    pos, head, checked = 0, "", 0
    while True:
        rows = conn.execute(
            "SELECT chain_pos, seq, extract(epoch FROM at)::text, record_id::text, "
            "op, payload::text, prev_hash, hash FROM audit_log "
            "WHERE chain_pos > %s ORDER BY chain_pos LIMIT %s", (pos, PAGE)).fetchall()
        if not rows:
            break
        for chain_pos, seq, at, record_id, op, payload, prev_hash, stored in rows:
            problem = None
            if chain_pos != pos + 1:
                problem = f"row missing: expected position {pos + 1}, found {chain_pos}"
            elif prev_hash != head:
                problem = "prev_hash does not match the previous row"
            else:
                text = "|".join([head, str(seq), at, record_id, op, payload])
                if hashlib.sha256(text.encode()).hexdigest() != stored:
                    problem = "row contents do not match its hash"
            if problem:
                return {"checked": checked, "broken_at": pos + 1, "seq": seq,
                        "reason": problem, "head": head}
            pos, head, checked = chain_pos, stored, checked + 1
    unsealed = conn.execute(
        "SELECT count(*) FROM audit_log WHERE chain_pos IS NULL").fetchone()[0]
    return {"checked": checked, "broken_at": None, "head": head, "unsealed": unsealed}


def report(result: dict) -> None:
    if result["broken_at"] is None:
        print(f"chain OK: {result['checked']} rows, {result['unsealed']} not yet "
              f"sealed, head {result['head'][:16] or '(empty)'}")
    else:
        print(f"chain BROKEN at position {result['broken_at']} (seq {result['seq']}): "
              f"{result['reason']}. {result['checked']} rows before it verified.")


def main():
    with psycopg.connect(ledger_dsn(), autocommit=True) as conn:
        result = verify(conn)
    report(result)
    sys.exit(0 if result["broken_at"] is None else 1)


if __name__ == "__main__":
    main()
