"""Experiments 1 and 2 -- the deterministic breach / safety harness.

Fires N genuinely simultaneous reserves at one shared dataset budget and reports
what happened to the cap.

The concurrency is the evidence, so two things are done deliberately:

  * every worker owns a pre-warmed HTTP connection before the run starts, so no
    request pays for TCP setup while its peers are already in flight;
  * all workers park on one asyncio.Event and are released together.

`reserved_at_spread_ms` is reported for every run precisely so this claim can be
audited: if the timestamps are spread out rather than clustered, the requests
were serialised and the run proves nothing.

Usage:
    python experiments/harness_asyncio.py --strategy naive  --workers 5  --runs 20
    python experiments/harness_asyncio.py --strategy atomic --workers 50 --runs 50
"""

import argparse
import asyncio
import csv
import statistics
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

import httpx

FIELDS = [
    "run",
    "strategy",
    "workers",
    "cap",
    "seed_spent",
    "cost",
    "adapter",
    "epsilon_source",
    "db_constraint_present",
    "isolation_level",
    "naive_race_delay_ms",
    "reserved_count",
    "denied_count",
    "error_count",
    "expected_max_success",
    # Where each caller's statement-1 read sat in the write sequence. These are
    # what turn the overshoot plateau from a proposed mechanism into a measured
    # one -- see _read_positions.
    "reads_in_window",
    "reads_after_write",
    "denied_after_write_read",
    "max_observed_reserved",
    "final_spent",
    "final_reserved",
    "total",
    "overshoot",
    "breach",
    "i1_cap_safety",
    "i2_ledger_agreement",
    "i4_exactly_once",
    "reserved_at_spread_ms",
    "wall_ms",
    "latency_p50_ms",
    "latency_max_ms",
]


async def one_worker(client, ev, dataset_id, run, idx, cost, strategy, source):
    await ev.wait()
    t0 = perf_counter()
    try:
        resp = await client.post(
            f"/datasets/{dataset_id}/reserve",
            json={
                "job_id": f"r{run}-w{idx}",
                "idempotency_key": f"run{run}-worker{idx}",
                "epsilon_cost": float(cost),
                "strategy": strategy,
                "epsilon_source": source,
                "actor": f"worker-{idx}",
            },
        )
    except Exception as exc:
        return {"worker": idx, "outcome": "error", "detail": str(exc), "ms": 0.0}
    ms = (perf_counter() - t0) * 1000
    if resp.status_code in (200, 429):
        body = resp.json()
        return {
            "worker": idx,
            "outcome": body["status"],
            "ms": ms,
            # What this caller observed when it decided. For `naive` this is the
            # stale read that explains the breach.
            "observed_spent": body.get("observed_spent"),
            "observed_reserved": body.get("observed_reserved"),
        }
    return {
        "worker": idx,
        "outcome": "error",
        "detail": f"{resp.status_code} {resp.text}",
        "ms": ms,
    }


def _read_positions(results, strategy):
    """How far into the write sequence each caller's read was -- the race window,
    counted rather than asserted.

    The naive family reports `observed_reserved` from statement 1, i.e. the
    PRE-write read. Every caller increments `epsilon_reserved` by the same cost
    from a seeded 0, so that value is a position in the sequence: 0 means no
    peer's write was visible yet (the caller was inside the race window), and
    anything above 0 means at least one increment had already landed when it
    read, so it lost the window and any denial it got was legitimate.

    `atomic` returns POST-update values from RETURNING, which describe the state
    the database evaluated its predicate against, not a window. Reporting 0 for
    it would invent a measurement, so it gets None -- the same discipline the
    report applies to unrun concurrency levels.
    """
    if strategy not in ("naive", "naive_txn"):
        return dict.fromkeys(
            (
                "reads_in_window",
                "reads_after_write",
                "denied_after_write_read",
                "max_observed_reserved",
            )
        )
    # Errored callers never reported a read, so they are counted in neither
    # bucket; the two need not sum to N.
    seen = [r for r in results if r.get("observed_reserved") is not None]
    after = [r for r in seen if float(r["observed_reserved"]) > 0.0]
    return {
        "reads_in_window": sum(1 for r in seen if float(r["observed_reserved"]) == 0.0),
        "reads_after_write": len(after),
        "denied_after_write_read": sum(1 for r in after if r["outcome"] == "denied"),
        "max_observed_reserved": max(
            (float(r["observed_reserved"]) for r in seen), default=0.0
        ),
    }


async def run_once(client, args, dataset_id, run, cost, source):
    # Reset to the near-cap precondition. Without it there is room to succeed
    # legitimately and no breach is observable.
    await client.post(
        f"/datasets/{dataset_id}/seed",
        json={
            "epsilon_spent": float(args.seed_spent),
            "epsilon_reserved": 0,
            "delete_records": True,
        },
    )

    ev = asyncio.Event()

    if args.sequential:
        # The control for "is the naive path artificially broken?". Identical
        # code, identical requests, one at a time. A correct implementation must
        # grant exactly the number that fit -- so if this breaches, the bug is in
        # the implementation, and if only the concurrent version breaches, the
        # race is the sole cause.
        ev.set()
        t0 = perf_counter()
        results = [
            await one_worker(
                client, ev, dataset_id, run, i, cost, args.strategy, source
            )
            for i in range(args.workers)
        ]
        wall_ms = (perf_counter() - t0) * 1000
    else:
        tasks = [
            asyncio.create_task(
                one_worker(client, ev, dataset_id, run, i, cost, args.strategy, source)
            )
            for i in range(args.workers)
        ]
        # Let every task reach `await ev.wait()` before releasing them together.
        for _ in range(5):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)

        t0 = perf_counter()
        ev.set()
        results = await asyncio.gather(*tasks)
        wall_ms = (perf_counter() - t0) * 1000

    inv = (await client.get(f"/datasets/{dataset_id}/invariants")).json()
    recs = (await client.get(f"/datasets/{dataset_id}/records")).json()

    # Only this run's own attempts: the seeded precondition rows carry an older
    # timestamp and would inflate the spread into meaninglessness.
    stamps = sorted(
        datetime.fromisoformat(r["reserved_at"])
        for r in recs
        if r["strategy"] != "seed"
    )
    spread_ms = (
        (stamps[-1] - stamps[0]).total_seconds() * 1000 if len(stamps) > 1 else 0.0
    )

    lat = [r["ms"] for r in results if r["ms"] > 0]
    headroom = args.cap - args.seed_spent

    return {
        "run": run,
        "strategy": args.strategy,
        "workers": args.workers,
        "cap": args.cap,
        "seed_spent": args.seed_spent,
        "cost": float(cost),
        "adapter": args.adapter,
        "epsilon_source": source,
        "db_constraint_present": args.constraint_state,
        # Recorded per run: the atomic strategy's safety argument is specific to
        # READ COMMITTED, so the level in force is part of the result.
        "isolation_level": args.isolation_level,
        "naive_race_delay_ms": args.naive_race_delay_ms,
        "reserved_count": sum(1 for r in results if r["outcome"] == "reserved"),
        "denied_count": sum(1 for r in results if r["outcome"] == "denied"),
        "error_count": sum(1 for r in results if r["outcome"] == "error"),
        "expected_max_success": int(headroom // float(cost)),
        **_read_positions(results, args.strategy),
        "final_spent": inv["epsilon_spent"],
        "final_reserved": inv["epsilon_reserved"],
        "total": inv["total"],
        "overshoot": inv["overshoot"],
        "breach": not inv["i1_cap_safety"],
        "i1_cap_safety": inv["i1_cap_safety"],
        "i2_ledger_agreement": inv["i2_ledger_agreement"],
        "i4_exactly_once": inv["i4_exactly_once"],
        "reserved_at_spread_ms": round(spread_ms, 3),
        "wall_ms": round(wall_ms, 3),
        "latency_p50_ms": round(statistics.median(lat), 3) if lat else 0.0,
        "latency_max_ms": round(max(lat), 3) if lat else 0.0,
        "_errors": [r.get("detail") for r in results if r["outcome"] == "error"],
        "_results": results,
    }


async def main_async(args):
    limits = httpx.Limits(
        max_connections=args.workers + 20, max_keepalive_connections=args.workers + 20
    )
    async with httpx.AsyncClient(
        base_url=args.base_url, limits=limits, timeout=60.0
    ) as client:
        cfg = (await client.get("/config")).json()
        args.naive_race_delay_ms = cfg["naive_race_delay_ms"]
        args.isolation_level = cfg.get("isolation_level", "unknown")
        if cfg["pool_max_size"] < args.workers:
            print(
                f"!! POOL_MAX_SIZE={cfg['pool_max_size']} < workers={args.workers}: "
                "requests will serialise at the connection pool, not the database.",
                file=sys.stderr,
            )

        # Both experiments run with the CHECK dropped: Experiment 1 needs it off
        # to breach at all, and Experiment 2 is stronger without a database
        # backstop propping up the result.
        state = await client.post(
            "/admin/db-constraint", json={"enabled": args.enforce_db_constraint}
        )
        args.constraint_state = state.json()["within_cap_constraint_present"]

        # Cost: either passed in, or the real deterministic price of a DP query.
        source = "passed_in"
        cost = args.cost
        if args.adapter == "opendp":
            spec = {"kind": args.spec_kind, "scale": args.spec_scale}
            resp = await client.post(
                "/adapters/epsilon", json={"adapter": "opendp", "spec": spec}
            )
            resp.raise_for_status()
            cost = resp.json()["epsilon"]
            source = "opendp"
            print(f"OpenDP {spec} -> epsilon = {cost} (deterministic given the spec)")

        ds = (
            await client.post(
                "/datasets", json={"name": f"exp-{args.strategy}", "epsilon_cap": args.cap}
            )
        ).json()
        dataset_id = ds["id"]

        print(
            f"\nstrategy={args.strategy}  workers={args.workers}  runs={args.runs}\n"
            f"cap={args.cap}  seeded spent={args.seed_spent}  "
            f"headroom={args.cap - args.seed_spent}  cost={cost}\n"
            f"within_cap CHECK present: {args.constraint_state}   "
            f"isolation: {args.isolation_level}   "
            f"naive_race_delay_ms: {args.naive_race_delay_ms}\n"
            f"dataset: {dataset_id}\n"
        )

        all_rows = []
        for workers in args.worker_levels:
            args.workers = workers
            # One warm connection per worker, so no request pays for TCP setup
            # while its peers are already in flight.
            await asyncio.gather(*(client.get("/health") for _ in range(workers)))

            print(f"\n--- N = {workers} ---")
            rows = []
            for run in range(1, args.runs + 1):
                row = await run_once(client, args, dataset_id, run, cost, source)
                rows.append(row)
                if args.demo:
                    render_demo_run(row, args, cost)
                    continue
                flag = "BREACH" if row["breach"] else "ok    "
                print(
                    f"run {run:>3}  {flag}  reserved={row['reserved_count']:>3} "
                    f"denied={row['denied_count']:>3} err={row['error_count']:>2}  "
                    f"total={row['total']:>8.3f}/{args.cap}  "
                    f"overshoot={row['overshoot']:>7.3f}  "
                    f"spread={row['reserved_at_spread_ms']:>7.2f}ms"
                )
                if row["_errors"]:
                    print(f"        errors: {row['_errors'][:2]}")
            if not args.demo:
                summarise(rows, args)
            all_rows.extend(rows)

        if len(args.worker_levels) > 1:
            vs_n(all_rows, args)
        if args.out:
            write_csv(all_rows, Path(args.out), append=args.append)
            print(f"\n{'appended to' if args.append else 'wrote'} {args.out}")


def summarise(rows, args):
    n = len(rows)
    breaches = [r for r in rows if r["breach"]]
    overshoots = [r["overshoot"] for r in rows]
    spreads = [r["reserved_at_spread_ms"] for r in rows]

    print("\n" + "=" * 68)
    print(f"strategy={args.strategy}  workers={args.workers}  runs={n}")
    print(f"  breach rate            : {len(breaches)}/{n} = {len(breaches)/n:.0%}")
    print(
        f"  overshoot (mean/max)   : {statistics.mean(overshoots):.3f} / {max(overshoots):.3f}"
    )
    print(f"  expected max successes : {rows[0]['expected_max_success']}")
    print(
        f"  actual successes (mean): "
        f"{statistics.mean(r['reserved_count'] for r in rows):.2f}"
    )
    if rows[0]["reads_in_window"] is not None:
        inw = statistics.mean(r["reads_in_window"] for r in rows)
        aft = statistics.mean(r["reads_after_write"] for r in rows)
        den = statistics.mean(r["denied_after_write_read"] for r in rows)
        print(
            f"  read inside the window : {inw:.2f}/{args.workers} "
            f"= {inw/args.workers:.0%}  (mean per run)"
        )
        print(f"  read after a write     : {aft:.2f}, of which {den:.2f} denied")
    print(f"  I1 held in             : {sum(r['i1_cap_safety'] for r in rows)}/{n} runs")
    print(
        f"  I2 held in             : {sum(r['i2_ledger_agreement'] for r in rows)}/{n} runs"
    )
    print(f"  I4 held in             : {sum(r['i4_exactly_once'] for r in rows)}/{n} runs")
    print(
        f"  reserved_at spread     : median {statistics.median(spreads):.2f}ms  "
        f"max {max(spreads):.2f}ms"
    )
    if statistics.median(spreads) > 250:
        print(
            "  !! timestamps are NOT clustered -- requests may have been\n"
            "     serialised. Treat this run as invalid evidence."
        )
    print("=" * 68)
    print(
        "Empirically observed under the tested conditions above; not a formal proof."
    )


# ---------------------------------------------------------------------------
# demo presentation mode
#
# Pure formatting over the same run data the CSV gets -- it computes nothing of
# its own and asserts nothing the ledger did not report. The budget bar is drawn
# from the invariants endpoint, and the per-worker trace from the observed reads
# each caller returned. Its only job is to remove the mental arithmetic between
# "total=16.000/10.0" and "the cap was breached".
# ---------------------------------------------------------------------------

BAR_CAP_WIDTH = 36  # characters used to draw the cap; overshoot extends past it


def _bar(value, cap, width=BAR_CAP_WIDTH):
    per_unit = width / cap if cap else 0
    filled = int(round(value * per_unit))
    if filled <= width:
        return "█" * filled + " " * (width - filled) + "│"
    return "█" * width + "│" + "█" * (filled - width)


def render_demo_run(row, args, cost):
    cap, seeded = args.cap, args.seed_spent
    headroom = cap - seeded
    legal = int(headroom // float(cost))
    results = row["_results"]
    breach = row["breach"]

    print()
    print("═" * 72)
    print(
        f" STRATEGY {args.strategy.upper():<10}"
        f"  within_cap CHECK: {'ON' if row['db_constraint_present'] else 'OFF'}"
        f"  ·  {args.workers} jobs × ε {float(cost)}"
    )
    print(f" isolation: {row['isolation_level']}")
    print(
        f" cap {cap}  │  already spent {seeded}  │  headroom {headroom}"
        f"  │  only {legal} can legally be granted"
    )
    print("═" * 72)
    print()
    print(f"  cap    {'├' + '─' * (BAR_CAP_WIDTH - 2) + '┤'} {cap}")
    print(f"  before {_bar(seeded, cap)} {seeded}   spent")
    print()
    print(f"  … firing {args.workers} simultaneous reserves …")
    print()

    # Order by arrival so the trace reads like a timeline.
    ordered = sorted(results, key=lambda r: r["ms"])
    first_read = None
    for r in ordered[: args.demo_trace]:
        if r["outcome"] == "error":
            print(f"  w{r['worker']:<3} ERROR {r.get('detail', '')[:50]}")
            continue
        os_, or_ = r.get("observed_spent"), r.get("observed_reserved")
        mark = ""

        if args.strategy in ("naive", "naive_txn"):
            # observed = the PRE-write read, so the projection it computed is
            # exactly the (stale) decision it made. Both naive variants report
            # the pre-write values; only `atomic` returns post-update ones, so
            # this branch must cover both or the trace prints atomic's wording
            # over naive's numbers.
            if os_ is None:
                line = "read(—)"
            else:
                projected = os_ + or_ + float(cost)
                line = (
                    f"read(spent {os_}, reserved {or_}) → "
                    f"{projected} ≤ {cap} {'✓' if projected <= cap else '✗'}"
                )
                if r["outcome"] == "reserved":
                    if first_read is None:
                        first_read = (os_, or_)
                    elif (os_, or_) == first_read:
                        mark = "   ← same stale read"
        else:
            # observed = the POST-update row from RETURNING. Re-projecting it
            # would be meaningless arithmetic (and would print things like
            # "12.0 <= 10.0"); what it actually shows is the committed state the
            # database evaluated the predicate against.
            if os_ is None:
                line = "predicate false at the database"
            else:
                line = f"ledger now spent {os_} + reserved {or_} = {os_ + or_} ≤ {cap}"

        action = "RESERVED" if r["outcome"] == "reserved" else "DENIED"
        print(f"  w{r['worker']:<3} {line} → {action}{mark}")
    if len(ordered) > args.demo_trace:
        print(f"  … {len(ordered) - args.demo_trace} more")

    print()
    total = row["total"]
    print(f"  after  {_bar(total, cap)} {total}")
    if breach:
        # 9 = len("  after  "), the bar's own left offset, so the caret lands
        # exactly under the cap boundary drawn inside the bar.
        print(f"{' ' * (BAR_CAP_WIDTH + 9)}^ cap   OVER BY {row['overshoot']}")
    print()

    if breach:
        print(f"  ✗ CAP BREACHED   {total} / {cap}   (over by {row['overshoot']})")
        print(
            f"    {row['reserved_count']} granted, only {legal} were legal"
            f"  —  {row['reserved_count'] / max(legal, 1):.1f}× the budget"
        )
    else:
        print(f"  ✓ CAP HELD   {total} / {cap}")
        print(
            f"    {row['reserved_count']} granted (exactly the {legal} that fit), "
            f"{row['denied_count']} denied"
        )
    print(
        f"    all attempts landed within {row['reserved_at_spread_ms']} ms"
        "  —  the contention was real"
    )
    print()

def vs_n(rows, args):
    """Breach rate and overshoot as functions of N -- the Experiment 1 metric."""
    print("\n" + "=" * 68)
    print(f"{args.strategy}: breach rate and overshoot vs concurrency")
    print(
        f"{'N':>5} {'runs':>5} {'breach rate':>12} {'overshoot mean':>15} {'max':>8} "
        f"{'granted':>8} {'in window':>10}"
    )
    for n in args.worker_levels:
        sub = [r for r in rows if r["workers"] == n]
        br = sum(r["breach"] for r in sub) / len(sub)
        ov = [r["overshoot"] for r in sub]
        gr = statistics.mean(r["reserved_count"] for r in sub)
        # The plateau, if there is one, shows up as `granted` flattening while
        # `in window` falls -- the same number of winners out of a larger field.
        w = (
            f"{statistics.mean(r['reads_in_window'] for r in sub)/n:>10.0%}"
            if sub[0]["reads_in_window"] is not None
            else f"{'—':>10}"
        )
        print(
            f"{n:>5} {len(sub):>5} {br:>11.0%} {statistics.mean(ov):>15.3f} "
            f"{max(ov):>8.3f} {gr:>8.2f} {w}"
        )
    print("=" * 68)


def write_csv(rows, path: Path, append=False):
    """Append exists for sweeps of a *server* setting: NAIVE_RACE_DELAY_MS is
    read from the service's /config, so each point of that sweep is a separate
    process against a restarted service, accumulating into one CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = append and path.exists() and path.stat().st_size > 0
    with path.open("a" if existing else "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not existing:
            w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in FIELDS})


def parse_args():
    p = argparse.ArgumentParser(description="Experiments 1 & 2")
    p.add_argument(
        "--strategy", default="naive", choices=["naive", "naive_txn", "atomic"]
    )
    p.add_argument("--cap", type=float, default=10.0)
    p.add_argument("--seed-spent", dest="seed_spent", type=float, default=6.0)
    p.add_argument("--cost", type=float, default=2.0)
    p.add_argument(
        "--workers",
        default="5",
        help="concurrency level, or a comma-separated list (e.g. 2,5,10,20,50)",
    )
    p.add_argument("--runs", type=int, default=20)
    p.add_argument("--base-url", dest="base_url", default="http://localhost:8000")
    p.add_argument("--out", default=None, help="write results CSV here")
    p.add_argument(
        "--append",
        action="store_true",
        help="append to --out rather than replacing it -- for sweeping a server "
        "setting (NAIVE_RACE_DELAY_MS), where each point needs its own process",
    )
    p.add_argument(
        "--adapter",
        default="none",
        choices=["none", "opendp"],
        help="'opendp' prices the reservation with a real DP query spec",
    )
    p.add_argument("--spec-kind", dest="spec_kind", default="count")
    p.add_argument("--spec-scale", dest="spec_scale", type=float, default=0.5)
    p.add_argument(
        "--demo",
        action="store_true",
        help="presentation mode: budget bar + per-worker stale-read trace",
    )
    p.add_argument(
        "--demo-trace",
        dest="demo_trace",
        type=int,
        default=8,
        help="how many workers to show in the demo trace (default 8)",
    )
    p.add_argument(
        "--sequential",
        action="store_true",
        help="issue requests one at a time instead of concurrently -- the control "
        "showing the naive path is correct absent a race",
    )
    p.add_argument(
        "--enforce-db-constraint",
        dest="enforce_db_constraint",
        action="store_true",
        help="keep the within_cap CHECK on (default: OFF for both experiments)",
    )
    args = p.parse_args()
    args.worker_levels = [int(w) for w in str(args.workers).split(",") if w.strip()]
    args.workers = args.worker_levels[0]
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
