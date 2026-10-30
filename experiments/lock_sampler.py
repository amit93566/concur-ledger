"""Lock waits, observed -- what the database is doing during a burst.

Runs the Experiment 2 harness (one burst of N simultaneous reserves per run)
while a separate connection, outside the service's pool, samples
pg_stat_activity about every millisecond:

    backends waiting on a lock  (wait_event_type = 'Lock')
    backends active             (state = 'active')

For `atomic` the waiters are queued on the dataset row inside the conditional
UPDATE; for `for_update` they are queued at the SELECT ... FOR UPDATE and the
lock is held for longer (read, Python check, write, commit). The figure is the
waiting count over time; the summary is the peak and the time spent with any
backend waiting.

Usage:
    python experiments/lock_sampler.py --strategies atomic,for_update --workers 50 --runs 5
"""

import argparse
import csv
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_dsn import ledger_dsn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

SAMPLE_SQL = """
    SELECT count(*) FILTER (WHERE wait_event_type = 'Lock'),
           count(*) FILTER (WHERE state = 'active')
      FROM pg_stat_activity
     WHERE datname = current_database()
       AND backend_type = 'client backend'
       AND pid <> pg_backend_pid()
"""


def sample(stop: threading.Event, rows: list, strategy: str, t0: float, interval: float):
    with psycopg.connect(ledger_dsn(), autocommit=True) as conn:
        while not stop.is_set():
            waiting, active = conn.execute(SAMPLE_SQL).fetchone()
            rows.append((strategy, round(time.perf_counter() - t0, 6), waiting, active))
            time.sleep(interval)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--strategies", default="atomic,for_update", type=lambda s: s.split(","))
    p.add_argument("--workers", type=int, default=50)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--interval-ms", type=float, default=1.0)
    p.add_argument("--out", default="results/exp_lock_waits.csv")
    args = p.parse_args()

    all_rows = []
    for strategy in args.strategies:
        rows: list = []
        stop = threading.Event()
        t0 = time.perf_counter()
        th = threading.Thread(
            target=sample, args=(stop, rows, strategy, t0, args.interval_ms / 1000)
        )
        th.start()
        subprocess.run(
            [sys.executable, "experiments/harness_asyncio.py", "--strategy", strategy,
             "--cap", "10", "--seed-spent", "6", "--cost", "2",
             "--workers", str(args.workers), "--runs", str(args.runs)],
            cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
        )
        stop.set()
        th.join()

        busy = [r for r in rows if r[2] > 0]
        dt = (rows[-1][1] - rows[0][1]) / max(len(rows) - 1, 1) if rows else 0
        print(
            f"{strategy:12s} samples={len(rows):>6} (~{dt * 1000:.2f} ms apart)  "
            f"peak waiting={max((r[2] for r in rows), default=0):>3}  "
            f"peak active={max((r[3] for r in rows), default=0):>3}  "
            f"time with any waiter ~{len(busy) * dt * 1000:.0f} ms over {args.runs} bursts"
        )
        all_rows.extend(rows)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["strategy", "t_s", "waiting_on_lock", "active"])
        w.writerows(all_rows)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
