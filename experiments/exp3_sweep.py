"""Experiment 3 -- what does safety cost? A closed-loop sweep over /run.

Where Experiments 1 and 2 fire one burst and inspect the cap, this measures
sustained throughput and latency: N workers each loop POST /run back to back
(zero think time) for a fixed window, against one dataset, for every strategy.

Design choices that the method section has to state, so they are stated here:

  * It drives /run, the full reserve -> operation -> commit cycle, and every
    strategy goes through the same endpoint so the comparison is fair.
  * The operation is the `synthetic` adapter: a known sleep, a fixed epsilon.
    Duration is a factor (10 ms, 1 s); the mechanism is not what is measured.
  * Warm-up is discarded. Throughput counts completions that END inside the
    measured window; latency uses requests that START inside it, including those
    that finish after it, so slow requests are not dropped from the tail.
  * A client timeout is counted as a timeout, never silently dropped. If a
    queue exceeds the timeout, latency is censored there and the CSV says how
    many were.
  * A timeout abandons the request, not the work: the server still finishes it.
    So after each cell the driver waits until Postgres has no busy sessions
    (`quiesce_s`) before reading the invariants, and the next cell starts on an
    idle server rather than on the previous cell's backlog.
  * After each cell the invariants are read (I1, I2 must hold) and the cell's
    rows are deleted, so the ledger tables do not grow across the sweep and
    bias later cells.
  * Cells run in a shuffled order (fixed --seed), repetition by repetition, so
    slow drift on the machine spreads across strategies instead of landing on
    whichever ran last.
  * `--endpoint noop` runs the same loop against /noop: the ceiling this HTTP
    stack, pool and harness can reach at each N with no ledger work at all.
  * `--keep-awake K` (default 4) runs K busy loops at SCHED_IDLE priority for the
    whole sweep. Measured on this machine (WSL2): an atomic reserve that takes
    ~4 ms back to back takes ~9 ms after a 10 ms idle gap and ~20 ms after 50 ms,
    because idle vCPUs are slow to wake. That inflates latency at low N (where
    the machine idles between round trips) and not at high N, which would bend
    the N-trend for a reason that has nothing to do with the ledger. With the
    spinners the gap effect disappears (~3-4 ms at every gap). SCHED_IDLE runs
    only when nothing else wants the core, so they do not compete with the app,
    the database or the harness; they are outside both containers' cgroups and
    this process, so the recorded CPU figures exclude them.

Regimes:
    unconstrained  cap 900000, epsilon 0.001 -- the cap never binds; this is the
                   pure cost of enforcement.
    near_cap       cap 10, epsilon 1, committed actual 0 -- every operation
                   holds 1 while it runs and gives it back. The budget never
                   depletes; at most 10 holds fit at once, so denials come only
                   from headroom held by in-flight operations.

Usage:
    python experiments/exp3_sweep.py --strategies atomic,serializable \\
        --ns 1,8,64 --durations 10,1000 --reps 1 --out results/exp3_pilot.csv
"""

import argparse
import asyncio
import csv
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

REGIMES = {
    "unconstrained": {"cap": 900000, "spec": {"epsilon": 0.001}},
    "near_cap": {"cap": 10, "spec": {"epsilon": 1, "actual_epsilon": 0}},
}

KEY = ["endpoint", "strategy", "regime", "n", "duration_ms", "rep"]
FIELDS = KEY + [
    "order",
    "started",
    "warmup_s",
    "measure_s",
    "timeout_s",
    "cap",
    "isolation_level",
    "db_constraint_present",
    "keep_awake",
    "uvicorn_workers",
    "pool_max_size",
    # observed, not intended: os.sched_getaffinity and each container's cpuset
    "cpus_harness",
    "cpus_app",
    "cpus_db",
    # throughput, from completions that end inside the measured window
    "completed",
    "completed_per_s",
    "denied",
    "denied_per_s",
    "denial_rate",
    "gave_up",
    "errors",
    "timeouts",
    # client-side full-cycle latency of completed requests started in the window
    "lat_samples",
    "lat_p50_ms",
    "lat_p95_ms",
    "lat_p99_ms",
    "lat_max_ms",
    # server-side, from timing_ms: the reserve step
    "srv_reserve_p50_ms",
    "srv_reserve_p99_ms",
    "srv_total_p50_ms",
    "srv_total_p99_ms",
    "retries_total",
    "retries_per_success",
    "deadlocks",
    # seconds the server kept working after the client stopped (abandoned
    # requests finishing); the invariants are read after this
    "quiesce_s",
    # cores used during the measured window (1.0 = one core fully busy)
    "cpu_harness",
    "cpu_app",
    "cpu_db",
    "i1_cap_safety",
    "i2_ledger_agreement",
    "all_hold",
    "final_reserved",
    "error_sample",
]

RAW_FIELDS = [
    "worker", "t_start_s", "latency_ms", "outcome", "retries",
    "srv_reserve_ms", "srv_operation_ms", "srv_commit_ms", "srv_total_ms",
]


# --------------------------------------------------------------------------
# out-of-band measurements: run outside the request path, via docker
# --------------------------------------------------------------------------


def _compose(*args: str) -> str:
    out = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True,
        timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def _psql(sql: str) -> str:
    return _compose(
        "exec", "-T", "db", "psql", "-U", "postgres", "-d", "ledger", "-tAc", sql
    ).strip()


def _cpu_usec(service: str) -> int:
    # The container's own cgroup counter: cumulative CPU time, every process in
    # the container, read before and after the window. More exact than sampling
    # `docker stats`, and it costs nothing while requests are in flight.
    for line in _compose("exec", "-T", service, "cat", "/sys/fs/cgroup/cpu.stat").splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    raise RuntimeError(f"no usage_usec in {service} cpu.stat")


def _busy_backends() -> int:
    # Sessions doing anything at all, including `idle in transaction`.
    return int(_psql(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND backend_type = 'client backend' AND state <> 'idle' "
        "AND pid <> pg_backend_pid()"
    ))


def _quiesce(limit_s: float) -> float:
    """Wait until the server has finished every request of the cell.

    A client timeout abandons the request, not the work: the server keeps
    queueing and committing it. Without this wait those stragglers would land in
    the next cell's measurement window (and in this cell's cleanup).
    """
    start = time.perf_counter()
    quiet = 0
    while time.perf_counter() - start < limit_s:
        quiet = quiet + 1 if _busy_backends() == 0 else 0
        if quiet >= 3:
            return round(time.perf_counter() - start, 3)
        time.sleep(0.3)
    raise RuntimeError(f"server still busy {limit_s:.0f} s after the cell ended")


def _cpuset(service: str) -> str:
    cid = _compose("ps", "-q", service).strip()
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.HostConfig.CpusetCpus}}", cid],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return out or "all"


def _ranges(cpus) -> str:
    cpus = sorted(cpus)
    out, start = [], None
    for i, c in enumerate(cpus):
        if start is None:
            start = c
        if i + 1 == len(cpus) or cpus[i + 1] != c + 1:
            out.append(f"{start}-{c}" if c != start else f"{c}")
            start = None
    return ",".join(out)


def _deadlocks() -> int:
    return int(_psql(
        "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()"
    ))


async def _sample_start():
    return await asyncio.to_thread(
        lambda: (time.perf_counter(), time.process_time(), _cpu_usec("app"), _cpu_usec("db"))
    )


# --------------------------------------------------------------------------
# the closed loop
# --------------------------------------------------------------------------


async def worker(client, path, body_base, idx, cell_tag, t0, measure_end, samples):
    i = 0
    while time.perf_counter() < measure_end:
        i += 1
        body = dict(body_base, job_id=f"{cell_tag}-w{idx}",
                    idempotency_key=f"{cell_tag}-w{idx}-{i}")
        start = time.perf_counter()
        rec = {"worker": idx, "t_start": start - t0, "retries": 0, "timing": {}}
        try:
            resp = await client.post(path, json=body)
        except httpx.TimeoutException:
            rec.update(outcome="timeout", end=time.perf_counter() - t0)
            samples.append(rec)
            continue
        except Exception as exc:
            rec.update(outcome="error", end=time.perf_counter() - t0, detail=repr(exc))
            samples.append(rec)
            await asyncio.sleep(0.01)  # do not spin on a dead service
            continue
        end = time.perf_counter()
        rec.update(end=end - t0, latency_ms=(end - start) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            rec["outcome"] = "completed"
            rec["retries"] = data.get("retries", 0)
            rec["timing"] = data.get("timing_ms", {})
        elif resp.status_code == 429:
            data = resp.json()
            rec["outcome"] = "denied"
            rec["retries"] = data.get("retries", 0)
            rec["timing"] = data.get("timing_ms", {})
        elif resp.status_code == 503 and resp.json().get("status") == "gave_up":
            rec["outcome"] = "gave_up"
            rec["retries"] = resp.json().get("retries", 0)
        else:
            rec["outcome"] = "error"
            rec["detail"] = f"{resp.status_code} {resp.text[:200]}"
        samples.append(rec)


def _pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 3)


async def run_cell(client, args, cell, cfg, constraint_present):
    endpoint, strategy, regime, n, duration, rep = (cell[k] for k in KEY)
    regime_cfg = REGIMES[regime]
    ds = (await client.post("/datasets", json={
        "name": f"exp3-{endpoint}-{strategy}-{regime}-n{n}-d{duration}-r{rep}",
        "epsilon_cap": regime_cfg["cap"],
    })).json()
    dataset_id = ds["id"]

    path = f"/datasets/{dataset_id}/{'noop' if endpoint == 'noop' else 'run'}"
    body_base = {
        "strategy": strategy if endpoint == "run" else "atomic",
        "adapter": "synthetic",
        "spec": dict(regime_cfg["spec"], duration_ms=duration),
        "actor": "exp3",
    }

    # One warm connection per worker before the clock starts.
    await asyncio.gather(*(client.get("/health") for _ in range(n)))
    deadlocks_before = await asyncio.to_thread(_deadlocks)

    samples: list[dict] = []
    t0 = time.perf_counter()
    warm_end = t0 + args.warmup
    measure_end = warm_end + args.measure
    cell_tag = f"{dataset_id[:8]}"
    tasks = [
        asyncio.create_task(
            worker(client, path, body_base, w, cell_tag, t0, measure_end, samples)
        )
        for w in range(n)
    ]

    await asyncio.sleep(args.warmup)
    wall_a, proc_a, app_a, db_a = await _sample_start()
    await asyncio.sleep(max(0.0, measure_end - time.perf_counter()))
    wall_b, proc_b, app_b, db_b = await _sample_start()
    # Drain: each worker finishes its in-flight request (bounded by the client
    # timeout) and stops. Nothing is cancelled, so nothing is silently lost.
    await asyncio.gather(*tasks)

    window = wall_b - wall_a
    w0, w1 = warm_end - t0, measure_end - t0
    ended_in = [s for s in samples if w0 <= s["end"] <= w1]
    started_in = [s for s in samples if w0 <= s["t_start"] < w1]

    completed = [s for s in ended_in if s["outcome"] == "completed"]
    denied = [s for s in ended_in if s["outcome"] == "denied"]
    lat = [s["latency_ms"] for s in started_in if s["outcome"] == "completed"]
    srv_reserve = [s["timing"]["reserve"] for s in started_in if "reserve" in s["timing"]]
    srv_total = [s["timing"]["total"] for s in started_in if "total" in s["timing"]]
    retries = sum(s["retries"] for s in started_in)
    successes = sum(1 for s in started_in if s["outcome"] == "completed")
    # Every error and timeout in the cell, warm-up and drain included: a queue
    # long enough to time out fills during warm-up and expires during the drain,
    # so counting only the measured window would report none.
    errors = [s for s in samples if s["outcome"] == "error"]

    quiesce_s = await asyncio.to_thread(_quiesce, args.quiesce_limit)
    inv = (await client.get(f"/datasets/{dataset_id}/invariants")).json()
    deadlocks_after = await asyncio.to_thread(_deadlocks)
    if not args.keep:
        await asyncio.to_thread(
            _psql,
            f"DELETE FROM spend_records WHERE dataset_id = '{dataset_id}'; "
            f"DELETE FROM datasets WHERE id = '{dataset_id}';",
        )

    decided = len(completed) + len(denied)
    row = dict(cell)
    row.update(
        started=datetime.now().isoformat(timespec="seconds"),
        warmup_s=args.warmup,
        measure_s=round(window, 3),
        timeout_s=args.timeout,
        cap=regime_cfg["cap"],
        isolation_level=cfg.get("isolation_level"),
        db_constraint_present=constraint_present,
        keep_awake=args.keep_awake,
        uvicorn_workers=cfg.get("uvicorn_workers", 1),
        cpus_harness=args.cpus["harness"],
        cpus_app=args.cpus["app"],
        cpus_db=args.cpus["db"],
        pool_max_size=cfg["pool_max_size"],
        completed=len(completed),
        completed_per_s=round(len(completed) / window, 3),
        denied=len(denied),
        denied_per_s=round(len(denied) / window, 3),
        denial_rate=round(len(denied) / decided, 4) if decided else None,
        gave_up=sum(1 for s in started_in if s["outcome"] == "gave_up"),
        errors=len(errors),
        timeouts=sum(1 for s in samples if s["outcome"] == "timeout"),
        lat_samples=len(lat),
        lat_p50_ms=_pct(lat, 50),
        lat_p95_ms=_pct(lat, 95),
        lat_p99_ms=_pct(lat, 99),
        lat_max_ms=round(max(lat), 3) if lat else None,
        srv_reserve_p50_ms=_pct(srv_reserve, 50),
        srv_reserve_p99_ms=_pct(srv_reserve, 99),
        srv_total_p50_ms=_pct(srv_total, 50),
        srv_total_p99_ms=_pct(srv_total, 99),
        retries_total=retries,
        retries_per_success=round(retries / successes, 4) if successes else None,
        deadlocks=deadlocks_after - deadlocks_before,
        quiesce_s=quiesce_s,
        cpu_harness=round((proc_b - proc_a) / window, 3),
        cpu_app=round((app_b - app_a) / 1e6 / window, 3),
        cpu_db=round((db_b - db_a) / 1e6 / window, 3),
        i1_cap_safety=inv.get("i1_cap_safety"),
        i2_ledger_agreement=inv.get("i2_ledger_agreement"),
        all_hold=inv.get("all_hold"),
        final_reserved=inv.get("epsilon_reserved"),
        error_sample=errors[0].get("detail", "")[:200] if errors else "",
    )

    if args.raw:
        raw_dir = Path(args.raw)
        raw_dir.mkdir(parents=True, exist_ok=True)
        name = "_".join(str(cell[k]) for k in KEY) + ".csv"
        with open(raw_dir / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=RAW_FIELDS)
            w.writeheader()
            for s in samples:
                t = s["timing"]
                w.writerow({
                    "worker": s["worker"],
                    "t_start_s": round(s["t_start"], 6),
                    "latency_ms": round(s.get("latency_ms", 0), 3),
                    "outcome": s["outcome"],
                    "retries": s["retries"],
                    "srv_reserve_ms": t.get(reserve_key),
                    "srv_operation_ms": t.get("operation"),
                    "srv_commit_ms": t.get("commit"),
                    "srv_total_ms": t.get("total"),
                })
    return row


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------


def build_cells(args):
    strategies = ["noop"] if args.endpoint == "noop" else args.strategies
    durations = [0] if args.endpoint == "noop" else args.durations
    cells = []
    order = 0
    rng = random.Random(args.seed)
    for rep in range(1, args.reps + 1):
        block = [
            {"endpoint": args.endpoint, "strategy": s, "regime": args.regime,
             "n": n, "duration_ms": d, "rep": rep}
            for s in strategies for n in args.ns for d in durations
        ]
        rng.shuffle(block)
        for cell in block:
            order += 1
            cell["order"] = order
            cells.append(cell)
    return cells


def done_keys(path: Path) -> set[tuple]:
    if not path.exists():
        return set()
    with open(path, newline="") as fh:
        return {tuple(str(r[k]) for k in KEY) for r in csv.DictReader(fh)}


def fmt(v, spec):
    return "-" if v is None else format(v, spec)


def start_spinners(k: int) -> list[subprocess.Popen]:
    # SCHED_IDLE: scheduled only when a core would otherwise sit idle. Given
    # every CPU explicitly: under `taskset` (make exp3) they would otherwise
    # inherit the harness's two cores and keep only those awake.
    every_cpu = set(range(os.cpu_count() or 1))
    return [
        subprocess.Popen(
            ["chrt", "-i", "0", sys.executable, "-c", "while True: pass"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=lambda: os.sched_setaffinity(0, every_cpu),
        )
        for _ in range(k)
    ]


def main(args):
    spinners = start_spinners(args.keep_awake)
    try:
        asyncio.run(main_async(args))
    finally:
        for p in spinners:
            p.terminate()
        for p in spinners:
            p.wait()


async def main_async(args):
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not args.resume and out.exists():
        out.unlink()
    skip = done_keys(out)
    cells = [c for c in build_cells(args) if tuple(str(c[k]) for k in KEY) not in skip]

    limits = httpx.Limits(
        max_connections=max(args.ns) + 20, max_keepalive_connections=max(args.ns) + 20
    )
    async with httpx.AsyncClient(
        base_url=args.base_url, limits=limits, timeout=args.timeout
    ) as client:
        cfg = (await client.get("/config")).json()
        workers = cfg.get("uvicorn_workers", 1)
        # Keep-alive connections are not spread evenly over workers, so allow
        # headroom: requests must queue in Postgres, never at a pool.
        if cfg["pool_max_size"] * workers < max(args.ns) * 1.2 or (
            workers == 1 and cfg["pool_max_size"] < max(args.ns)
        ):
            sys.exit(
                f"pool too small: {workers} worker(s) x POOL_MAX_SIZE="
                f"{cfg['pool_max_size']} for max N={max(args.ns)}"
            )
        # Off, as in Experiments 1 and 2: the result must come from the strategy,
        # and `naive` must not be propped up (or failed) by the database CHECK.
        state = await client.post("/admin/db-constraint", json={"enabled": False})
        constraint_present = state.json()["within_cap_constraint_present"]

        args.cpus = {
            "harness": _ranges(os.sched_getaffinity(0)),
            "app": await asyncio.to_thread(_cpuset, "app"),
            "db": await asyncio.to_thread(_cpuset, "db"),
        }
        per_cell = args.warmup + args.measure
        print(
            f"exp3: {len(cells)} cells to run ({len(skip)} already in {out}), "
            f"~{len(cells) * (per_cell + 3) / 60:.0f} min plus drains\n"
            f"endpoint={args.endpoint} regime={args.regime} "
            f"isolation={cfg.get('isolation_level')} CHECK={constraint_present} "
            f"keep_awake={args.keep_awake} uvicorn_workers={workers} "
            f"pool={cfg['pool_max_size']}/worker\n"
            f"cpus: harness={args.cpus['harness']} app={args.cpus['app']} db={args.cpus['db']}\n"
        )

        new_file = not out.exists()
        with open(out, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            for i, cell in enumerate(cells, 1):
                row = await run_cell(client, args, cell, cfg, constraint_present)
                writer.writerow(row)
                fh.flush()  # a crash mid-sweep keeps every finished cell
                flag = "ok" if row["all_hold"] else "INVARIANT FAIL"
                print(
                    f"[{i:>3}/{len(cells)}] {row['strategy']:13s} n={row['n']:>3} "
                    f"d={row['duration_ms']:>4}ms rep={row['rep']}  "
                    f"done/s={row['completed_per_s']:>8.1f}  "
                    f"denied/s={row['denied_per_s']:>8.1f}  "
                    f"p50={fmt(row['lat_p50_ms'], '>8.1f')} p99={fmt(row['lat_p99_ms'], '>8.1f')}ms  "
                    f"retry/ok={fmt(row['retries_per_success'], '.2f')}  "
                    f"to={row['timeouts']} err={row['errors']} dl={row['deadlocks']} quiesce={row['quiesce_s']}s  "
                    f"cpu h/a/d={row['cpu_harness']:.2f}/{row['cpu_app']:.2f}/{row['cpu_db']:.2f}  "
                    f"{flag}",
                    flush=True,  # live progress when stdout is a log file
                )
                if row["error_sample"]:
                    print(f"        error: {row['error_sample']}")
    print(f"\nwrote {out}")


def _ints(s):
    return [int(x) for x in s.split(",") if x]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--endpoint", choices=["run", "noop"], default="run")
    p.add_argument(
        "--strategies", default="naive,atomic,for_update,serializable",
        type=lambda s: s.split(","),
    )
    p.add_argument("--ns", default="1,2,4,8,16,32,64,100", type=_ints)
    p.add_argument("--durations", default="10,1000", type=_ints, help="operation ms")
    p.add_argument("--regime", choices=sorted(REGIMES), default="unconstrained")
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--warmup", type=float, default=5.0, help="seconds discarded")
    p.add_argument("--measure", type=float, default=30.0, help="seconds measured")
    p.add_argument("--timeout", type=float, default=60.0, help="client timeout, s")
    p.add_argument("--seed", type=int, default=3, help="cell-order shuffle seed")
    p.add_argument("--out", default="results/exp3_cost_sweep.csv")
    p.add_argument("--raw", help="directory for per-request rows, one CSV per cell")
    p.add_argument("--resume", action="store_true",
                   help="append to --out and skip cells already in it")
    p.add_argument("--keep", action="store_true",
                   help="keep each cell's dataset and records instead of deleting them")
    p.add_argument("--quiesce-limit", type=float, default=600.0,
                   help="max seconds to wait for abandoned requests to finish")
    p.add_argument("--keep-awake", type=int, default=4, metavar="K",
                   help="SCHED_IDLE busy loops that stop idle-vCPU wake-up latency "
                        "from inflating low-N results; 0 disables (see docstring)")
    return p.parse_args()


if __name__ == "__main__":
    main(parse_args())
