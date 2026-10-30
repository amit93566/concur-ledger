"""DEMO -- watch one transaction block another on the dataset row.

Two sessions, straight to Postgres, no service in between:

  A  BEGIN; reserves 2 with the atomic conditional UPDATE; does NOT commit.
  B  runs the same conditional UPDATE for 2 more. It blocks: A holds the row.
     The script shows B in pg_stat_activity, waiting on a Lock, and who blocks it.
  Enter -> A commits. B wakes, and under READ COMMITTED re-checks its WHERE
     clause against A's committed row (EvalPlanQual), not the value it first saw.

With cap 10 and spent 7 only one of the two fits, so B returns rowcount 0: the
denial comes from the re-check. That re-check is the whole reason `atomic` is
safe, and this is it happening on screen.

    python experiments/demo_locks.py          # interactive (press Enter)
    python experiments/demo_locks.py --auto   # no prompt, 3 s hold
"""

import argparse
import sys
import threading
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_dsn import ledger_dsn  # noqa: E402

RESERVE = (
    "UPDATE datasets SET epsilon_reserved = epsilon_reserved + 2 "
    "WHERE id = %s AND epsilon_spent + epsilon_reserved + 2 <= epsilon_cap "
    "RETURNING epsilon_spent, epsilon_reserved"
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--auto", action="store_true")
    args = p.parse_args()
    dsn = ledger_dsn()

    with psycopg.connect(dsn, autocommit=True) as admin:
        ds = admin.execute(
            "INSERT INTO datasets (name, epsilon_cap, epsilon_spent) "
            "VALUES ('demo-locks', 10, 7) RETURNING id"
        ).fetchone()[0]
    # Matching committed record, so the invariants stay true for this dataset.
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            "INSERT INTO spend_records (dataset_id, job_id, idempotency_key, "
            "epsilon_reserved, epsilon_committed, status, strategy, epsilon_source, "
            "settled_at) VALUES (%s, 'seed', 'seed', 7, 7, 'committed', 'seed', "
            "'seed', now())", (ds,))
    print("dataset: cap 10, spent 7, reserved 0 -> room for ONE reservation of 2\n")

    a = psycopg.connect(dsn)
    b = psycopg.connect(dsn)
    a_pid, b_pid = a.info.backend_pid, b.info.backend_pid

    row = a.execute(RESERVE, (ds,)).fetchone()
    print(f"A (pid {a_pid}): BEGIN; conditional UPDATE -> fits, now spent={row[0]} "
          f"reserved={row[1]}  [NOT committed, row lock held]")

    result = {}

    def session_b():
        t = time.perf_counter()
        r = b.execute(RESERVE, (ds,)).fetchone()
        result.update(row=r, waited=time.perf_counter() - t)
        b.commit()

    th = threading.Thread(target=session_b)
    th.start()
    time.sleep(0.5)

    with psycopg.connect(dsn, autocommit=True) as watch:
        st = watch.execute(
            "SELECT state, wait_event_type, wait_event, pg_blocking_pids(pid) "
            "FROM pg_stat_activity WHERE pid = %s", (b_pid,)).fetchone()
    print(f"B (pid {b_pid}): same UPDATE -> state={st[0]} wait={st[1]}/{st[2]} "
          f"blocked by {st[3]}   <- queued on A's row lock\n")

    if args.auto:
        time.sleep(3)
    else:
        input("press Enter to COMMIT session A ... ")
    a.commit()
    print("A: COMMIT")
    th.join()

    r = result["row"]
    print(f"B: woke after {result['waited']:.2f} s, re-checked the WHERE clause "
          f"against A's committed row (spent 7 + reserved 2 + 2 > 10) -> "
          f"{'rowcount 0: DENIED' if r is None else f'rowcount 1 (spent={r[0]} reserved={r[1]})'}")
    a.close()
    b.close()

    with psycopg.connect(dsn, autocommit=True) as admin:
        final = admin.execute(
            "SELECT epsilon_spent, epsilon_reserved, epsilon_cap FROM datasets WHERE id = %s",
            (ds,)).fetchone()
        # A's hold has no record row (this demo bypasses the service), so the
        # dataset is removed rather than left behind to fail I2 later.
        admin.execute("DELETE FROM spend_records WHERE dataset_id = %s", (ds,))
        admin.execute("DELETE FROM datasets WHERE id = %s", (ds,))
    print(f"\nfinal: spent={final[0]} reserved={final[1]} cap={final[2]} "
          f"-> total {final[0] + final[1]} <= cap: the cap held")


if __name__ == "__main__":
    main()
