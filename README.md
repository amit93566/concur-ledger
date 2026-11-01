# concur-ledger

**Safe enforcement of shared privacy budgets under concurrent use.**

A finite, per-dataset differential-privacy budget (ε) shared by many concurrent
jobs. This repository shows that a plausible naive enforcement implementation
**can be raced into overspending the cap**, that a correct one **cannot**, and
that a crash mid-protocol **cannot corrupt the ledger**.

This covers Experiments 1–4: four reserve strategies, the cost sweep, the
isolation, epsilon and extended fault evidence, and a governance layer (a
tamper-evident audit log and role-based access).

Companion documents: `IMPLEMENTATION_PLAN.md` (what to build),
`WORKFLOWS.md` (how to run it), `DEMO_CHECKLIST.md` (what to prove).

---

## Results, reproduced on this machine

**The canonical, always-current view is `make report`** — it regenerates
`results/report.html` from the CSVs, organized by experiment, so
no number in it can drift from the evidence. The tables below are a snapshot
from one such run and will differ in the tails from yours; breach/no-breach
verdicts are stable, the exact overshoot figures are not.

**Experiment 1 — the breach is real.** Cap 10, seeded at spent 6 (4 of
headroom), N concurrent reserves of ε=2. Only 2 should ever succeed.

| N | runs | breach rate | mean overshoot | max overshoot | mean granted | max granted |
|---:|---:|---:|---:|---:|---:|---:|
| 2 | 15 | 0% | 0.000 | 0.000 | 2.00 | 2 |
| 3 | 15 | 100% | 2.000 | 2.000 | 3.00 | 3 |
| 5 | 15 | 100% | 6.000 | 6.000 | 5.00 | 5 |
| 10 | 15 | 100% | 6.800 | 16.000 | 5.40 | 10 |
| 20 | 15 | 100% | 3.600 | 8.000 | 3.80 | 6 |
| 50 | 15 | 93% | 2.933 | 6.000 | 3.47 | 5 |

N=2 is the control: two reserves of 2 fit exactly into 4 of headroom, so no
breach occurs and none should. Above it the naive path breaches in almost every
run, granting up to **10 reservations where 2 fit** and overshooting the cap
by 16 ε.

Overshoot is not monotonic in N: it peaks around N=10 and then falls. The
mechanism is **measured, not proposed**. Every naive caller reports the values it
read in statement 1, and because each reservation increments `epsilon_reserved`
from a seeded zero, that read is a position in the write sequence: `reserved = 0`
means no peer's write was visible yet, so the caller was inside the race window.
Counted per run (`reads_in_window`):

| N | read inside the window | share of N | mean granted |
|---:|---:|---:|---:|
| 2 | 2.00 | 100% | 2.00 |
| 3 | 3.00 | 100% | 3.00 |
| 5 | 3.93 | 79% | 5.00 |
| 10 | 3.73 | 37% | 5.40 |
| 20 | 2.80 | 14% | 3.80 |
| 50 | 2.80 | 6% | 3.47 |

More contention does not produce more successful overspending: it produces more
callers arriving *after* the writes have landed, who are correctly denied. The
overspend is bounded by how many callers fit inside the window, not by how many
compete.

`make exp1c` supplies the intervention — hold N fixed and widen the window with
`NAIVE_RACE_DELAY_MS` instead of raising N:

| delay (ms) | read inside the window | mean granted | mean overshoot |
|---:|---:|---:|---:|
| 0 | 3.20 / 20 | 4.00 | 4.00 |
| 5 | 4.50 / 20 | 5.60 | 7.20 |
| 25 | 10.30 / 20 | 13.00 | 22.00 |
| 100 | 20.00 / 20 | 20.00 | 36.00 |

`make exp1c-n50`, the same intervention at N=50:

| delay (ms) | read inside the window | mean granted | mean overshoot |
|---:|---:|---:|---:|
| 0 | 3.10 / 50 | 4.60 | 5.20 |
| 1 | 3.10 / 50 | 3.70 | 3.40 |
| 5 | 4.20 / 50 | 5.00 | 6.00 |
| 20 | 7.10 / 50 | 7.70 | 11.40 |

Overshoot rises with the window, saturating at ε 36 at N=20 — the arithmetic
maximum for 20 reserves of 2 against a cap of 10 seeded at 6. At N=50 the rise
holds across the wider range, though the 1 ms point sits within the run-to-run
noise of the 0 ms point. The delay is an instrument, not a claim about production
timings; the breach result at `delay=0` needs no instrument at all. Exact per-N
figures vary run to run; regenerate with `make exp1 exp1c`.

**The naive path is faithful, not sabotaged.** The strongest evidence is the
sequential control (`make exp1-control`): the *same* naive code, the same
requests, issued one at a time instead of together.

| N | runs | breach rate | reservations granted |
|---:|---:|---:|---:|
| 5 | 10 | 0% | 2.00 |
| 20 | 10 | 0% | 2.00 |

Exactly 2 granted, zero breaches. The implementation's logic is correct; only
concurrency breaks it. That is what makes it a plausible first implementation
rather than a strawman.

**Experiment 2 — the fix holds, for every safe strategy.** Identical seed and
load at N = 2, 3, 5, 10, 20, 50, 100, with the database CHECK constraint still dropped
(`make exp2-safe`):

| strategy | runs | breaches | N requests overlapping at every N |
|---|---:|---:|---|
| atomic | 175 | 0 | yes |
| for_update | 175 | 0 | yes |
| serializable | 175 | 0 | yes |

525 runs, zero breaches, and **exactly 2 successes in every single run** — the
denial count is not merely safe, it is exactly correct. `serializable` needed
24 | 75 | 145 | 138 | 106 | 87 | 85 retries in total at N = 2, 3, 5, 10, 20, 50, 100, with no give-ups. They fall at
high N because late arrivals see a full budget and are denied without a conflict.

**Experiment 4 — the crash is survivable.** Budget survives a crash as a hold,
never as a loss and never as a double-spend; a retried commit charges exactly
once; releasing a committed reservation is rejected. `make exp4-ext` adds crash
points the reserve→commit gap cannot reach, 10 repetitions each:

| fault | what it reaches | checks passed |
|---|---|---:|
| controlled-abort | reserve, then fail before commit | 22/22 |
| process-kill | `os._exit` in the reserve→commit gap | 21/21 |
| kill-in-reserve | inside the reserve transaction | 5/5 |
| kill-in-reserve-serializable | the same, at SERIALIZABLE | 5/5 |
| kill-in-commit | between the two UPDATEs of a commit | 6/6 |
| kill-after-commit | committed, client not yet told | 5/5 |
| db-kill | SIGKILL Postgres under N=20 load | 4/4 |
| kill-under-load | service crash with 19 others in flight | 4/4 |

In db-kill, 2126 acknowledged, 0 missing, and 172 committed but never acknowledged: the commit
landed while the client saw an error. That is the case retry by idempotency key
exists for. Not tested: disk loss, a disk that lies about fsync, network
partitions, or clock faults.

**Experiment 3 — what safety costs.** Closed loop over `/run`: 2 × 320 cells
(4 strategies × 8 values of N × 2 durations × 5 reps), 30 s measured per cell. No
safe strategy broke I1 or I2 in either regime, with 0 client timeouts and 0
deadlocks. Median completed cycles/s, unconstrained regime:

| 10 ms operation | N=1 | 2 | 4 | 8 | 16 | 32 | 64 | 100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| atomic | 46.9 | 85.4 | 151.2 | 161.5 | 150.5 | 133.8 | 128.9 | 111.4 |
| for_update | 45.0 | 84.6 | 144.5 | 152.2 | 148.6 | 132.5 | 128.4 | 113.7 |
| serializable | 44.7 | 77.4 | 100.7 | 99.4 | 77.0 | 39.6 | 16.7 | 34.6 |
| naive (unsafe) | 42.8 | 82.2 | 133.3 | 160.9 | 150.1 | 135.5 | 127.3 | 107.3 |
| no-op ceiling | 304 | 317 | 456 | 356 | 283 | 220 | 234 | 210 |

| 1 s operation | N=1 | 2 | 4 | 8 | 16 | 32 | 64 | 100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| atomic | 1.0 | 2.0 | 4.0 | 8.0 | 16.0 | 31.1 | 61.9 | 97.0 |
| for_update | 1.0 | 2.0 | 4.0 | 7.9 | 15.6 | 31.9 | 62.7 | 96.2 |
| serializable | 1.0 | 2.0 | 4.0 | 7.8 | 15.5 | 31.2 | 44.2 | 12.9 |
| naive (unsafe) | 1.0 | 2.0 | 4.0 | 8.0 | 16.0 | 31.6 | 62.7 | 96.3 |

- **`atomic` and `for_update` cost nothing above run-to-run noise.** They track
  `naive` at every N and both durations, so once the check is in the database,
  safety is close to free in throughput. At 1 s they scale with N, because no
  lock is held during the operation.
- **`serializable` is the expensive one.** At 10 ms it peaks at about 100/s,
  then falls to 16.7/s at N=64. From N=32 more requests give up than succeed:
  60,751 give-ups across both regimes, none from any other strategy. Its median
  retries per success reach 38. At 1 s it matches the others up to N=32, then
  drops to 12.9/s at N=100 against 97/s for `atomic`.
- **Read `serializable` at high N as a range.** Repetitions split into slow and
  fast groups: 9.6–60.3/s at N=100, 10 ms. The rise in its median from N=64 to
  N=100 is that split, not a recovery.
- **Near the cap, at 1 s, the cap sets the throughput.** Each operation holds 1 of
  a cap of 10, so 10 holds at once allow 10 cycles/s. The safe strategies stay at
  about 10/s and deny over 90% of decisions from N=16. `naive` reaches 12.7/s:
  more than 10 holds were running at once. Its end-of-cell invariants still pass,
  because the actual cost is 0 and the ledger ends empty, so this overshoot shows
  only in the throughput.
- **Near the cap, at 10 ms, denials are noisy and not monotonic in N.** Denial share
  peaks around N=32 for `atomic` and N=64 for `for_update`, then drops. The likely
  reason is that most requests are queued rather than holding budget at high N,
  but this was not tested.

**Real ε, end to end.** An OpenDP noisy count at Laplace scale 0.5 costs
ε = 2.0. Against cap 10 / spent 6, two such queries commit (reaching exactly
10/10) and the third is denied with HTTP 429. The cost enforced is the cost
OpenDP reported.

> Empirically demonstrated **under the tested conditions**, not proven.

---

## Quick start

```bash
cp .env.example .env          # set DB_PORT if 5432 is already taken locally
docker compose up -d          # Postgres (named volume) + the service, one command
make health
```

The experiment harnesses run on the host, so they need a virtualenv either way:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

<details>
<summary>Optional: run the service on the host instead of in Docker</summary>

Useful for editing with a reload loop, and it is the path `WORKFLOWS.md` §0
describes. `make app` supervises the process so it auto-restarts, which is what
Experiment 4's process-kill mode needs (in Docker,
`restart: unless-stopped` does the same job).

```bash
make db && make app
```
</details>

Then:

```bash
make mid-demo     # the whole mid-term evidence run, in order
```

Individual pieces:

```bash
make opendp       # real OpenDP query -> real epsilon -> reserved, committed, enforced
make demo-breach  # PRESENTATION: visual budget bar + per-worker stale-read trace
make demo-safe    # PRESENTATION: identical load on atomic, cap holds
make summary      # results/summary.html -- one-page evidence sheet, exp 1-4
make report       # results/report.html  -- problem, solution, graph per experiment
make docs         # both of the above plus bundle-for-chat.md
make exp1         # the breach, with breach rate vs N
make exp1b        # the same breach inside ONE transaction -> still breaches
make demo-txn     # PRESENTATION: one transaction, no lock, cap still breaks
make exp1-control # the same naive code, run sequentially -> no breach
make exp2         # the fix, escalating to N=100
make exp4         # crash: controlled abort
make exp4-kill    # crash: real process kill
make watch        # live ledger view for a second terminal during the demo
```

---

## How it works

### The protocol

Two-phase **reserve → commit | release**. The cap check lives in `reserve` and
nowhere else. The operation runs *between* reserve and commit, **outside any
database transaction**:

```python
r = await reserve(dataset_id, cost, strategy)   # transaction opens and closes
if r.denied: return 429
try:
    actual = await adapter.run_operation(spec)  # NO transaction held here
    await commit(r.id, actual)                  # new transaction
except Exception:
    await release(r.id)
```

Commit cannot fail the cap check — the budget was already held — so it only
moves reserved → spent. This is what makes a crash between deciding and
publishing safe. See `app/main.py:run_job`; note that no
`async with pool.connection()` wraps the adapter call.

### The two reserve strategies

`naive` (`app/enforcement/naive.py`) — a faithful read → check → write. The read
runs in its own transaction which then ends; the cap check happens in Python on
values that are already stale; a second transaction applies the increment
unconditionally. The gap between them is the race window. **Nothing is
sabotaged** — the same code is correct under no concurrency, which is exactly why
it is a plausible first implementation.

`naive_txn` (`app/enforcement/naive_single_txn.py`) — the same read → check →
write, but wrapped in a **single** transaction. It breaches identically, and it
exists to answer the obvious objection that the two-transaction split was what
made `naive` fail. At READ COMMITTED a plain `SELECT` takes no lock, each
statement takes a fresh snapshot, and the write carries no predicate, so the
transaction boundary buys nothing. What matters is *where the decision is taken*.

**One name for the bug: write skew.** Each transaction's decision was valid
against the state it read, and the cap is violated only by the combination. That
term is used for the anomaly everywhere — here, in `results/report.html`,
`results/summary.html`, `bundle-for-chat.md` and the source comments.
*"read → check → write"* names the **code shape** that admits it and *"race
window"* names the **gap in time** between the read and the write; neither is a
second name for the anomaly.

It is specifically **not a lost update** — every increment applies, none is
overwritten, and I2 holds throughout. Write skew is the canonical anomaly
permitted by READ COMMITTED and prevented by SERIALIZABLE.

`atomic` (`app/enforcement/atomic.py`) — the check moves out of Python and into
the WHERE clause of the write:

```sql
UPDATE datasets SET epsilon_reserved = epsilon_reserved + :cost
 WHERE id = :id AND epsilon_spent + epsilon_reserved + :cost <= epsilon_cap
RETURNING epsilon_spent, epsilon_reserved;
-- rowcount 1 -> reserved ; rowcount 0 -> denied
```

One statement, so check and write cannot interleave.

Two more safe strategies exist for comparison. `for_update` takes the row lock at
the read and checks in Python, so the lock is held for longer. `serializable`
runs the read, check and write in one SERIALIZABLE transaction and retries
SQLSTATE `40001` up to 10 times with jittered backoff; a give-up returns 503 and
is never counted as a denial.

`naive_txn` is a variant of `naive` used in the race analysis, not a fifth
strategy.

### Invariants

### Isolation level

Pinned to **READ COMMITTED** in `app/db.py`, not inherited. The atomic strategy's
safety argument is specific to it: when a concurrent `UPDATE` commits first,
Postgres re-evaluates the second updater's `WHERE` against the newly committed
row version and returns rowcount 0 if it no longer fits. At REPEATABLE READ or
SERIALIZABLE the same situation raises SQLSTATE `40001` instead, which is safe
only with a retry loop — that is the `serializable` strategy. The server
default was already READ COMMITTED, so pinning it invalidates no earlier result;
it makes a dependency explicit. Every results row now records the level in force.

- **I1** cap safety: `epsilon_spent + epsilon_reserved <= epsilon_cap`
- **I2** ledger agreement: running columns equal the sums over record rows
- **I3** terminal states: a committed or released row never changes again
- **I4** exactly-once: one row per `(dataset_id, idempotency_key)`, committed at most once

Checked in SQL by `GET /datasets/{id}/invariants`, so the harness, the crash
test and the live demo all verify the same thing.

Note the precise Experiment 1 result: the naive path violates **I1 but not I2**.
It breaches the *cap* — a policy violation — while leaving the ledger internally
consistent. Overspending and corruption are different failures.

### The ε source

ε is never computed here. `app/adapters/opendp_adapter.py` asks OpenDP what a
query costs via `measurement.map(d_in)`.

The cost is **deterministic given the spec** even though the noisy result is
random, which is why a real adapter and a reproducible near-cap scenario are
compatible: a noisy count at Laplace scale 0.5 costs ε = 1/0.5 = 2.0 every time.
`POST /adapters/epsilon` returns that price without running anything, so the cap
check can reserve the exact cost up front.

OpenDP rounds conservatively — a bounded sum at scale 50 reports
2.0000000186…, not 2.0. The ledger stores `NUMERIC(12,6)`, so quantisation
**rounds up** (`ROUND_CEILING`); rounding to nearest would silently under-charge
the budget.

`passthrough` is a **stub**, labelled as one in `/adapters` and recorded as
`epsilon_source='passed_in'` on every record it produces. It is the documented
fallback so the enforcement logic can be exercised without a DP library.

---

## Running the experiments honestly

- **`ENFORCE_DB_CONSTRAINT`** toggles the `within_cap` CHECK. It is a safety net,
  **not** the enforcement mechanism. It must be off for Experiment 1 or the naive
  path cannot breach — and the harness also drops it for **Experiment 2**, so
  that the absence of a breach is attributable to the strategy rather than to the
  database backstopping it.
- After a breach run the ledger is over its cap, so the CHECK can no longer be
  re-added. The service logs this loudly and **runs without the net** rather than
  refusing to boot. `make reset-db` clears it.
- **Verify the concurrency is real.** Every run reports `reserved_at_spread_ms`.
  At N=5 the timestamps cluster within ~2 ms. If they were spread out, requests
  were serialised and the run proves nothing; the harness warns above 250 ms.
- **`POOL_MAX_SIZE` must be ≥ the worker count**, or requests serialise at the
  connection pool instead of at the database. The harness checks this and warns.
- **Don't detect a restart by PID.** In Docker the service is PID 1 both before
  and after a restart, so `/config` exposes an `instance_id` regenerated per
  process, and the crash test compares that instead.

### Build note (WSL2)

`docker-compose.yml` sets `build.network: host`. On WSL2 the host resolver is
`10.255.255.254`, reachable from the host network namespace but not from the
default bridge, so `pip install` inside the image build cannot resolve pypi.org
without it. It affects the build only, never the running container's networking,
and is harmless on other platforms.

### Measurement-validity caveats

The harness and the server share one machine, and four things about that bear on
how the numbers are read.

- **Harness bug, fixed; mid-term figures are superseded.** The mid-term harness
  sized its client connection limit from the first N in a sweep, so "N=100" had
  at most 22 requests in flight. Every number here is from the fixed harness,
  which records `max_in_flight`: all N requests overlapped in every run. The
  mid-term safety conclusions still held.
- **Admission spread.** The single service process admits requests one at a time,
  about 2–5 ms apart, so `arrival_spread_ms` grows with N: ~15 ms at N=5,
  ~208 ms at N=50, ~482 ms at N=100. This does not affect the safety
  conclusions (a breach either happened or it did not), but it is why only a few
  backends wait on the row lock at any instant.
- **Idle-vCPU wake-up latency (WSL2).** An atomic reserve takes ~4 ms back to back,
  ~9 ms after a 10 ms idle gap and ~20 ms after 50 ms. Experiment 3 runs SCHED_IDLE
  spinners to remove this (`--keep-awake`, recorded per row). Experiments 1, 2 and
  4 do not, so treat their latencies as upper bounds; their verdicts are unaffected.
- **Ceilings in Experiment 3.** At 10 ms operations, throughput is capped by the one
  hot dataset row, naive included; four uvicorn workers did not raise it (`UVICORN_WORKERS`
  stays, default 1). The no-op ceiling is itself limited by the single-threaded
  client. Core pinning separates processes onto vCPUs, not physical cores; each
  row records the affinity observed.
- **Experiment 3 ran in two sessions, partly on battery.** The cells were measured
  on 30 Oct and 1 Nov, and 81 of the unconstrained cells ran on battery. Those
  cells have a median ratio of 0.99 to the other reps of the same cell, with the
  largest gaps in the cells that are noisy anyway, so none were re-run. Power
  state was not logged for the 30 Oct cells.
- **Dropped connections.** 34 requests across the 640 cells failed with `Server
  disconnected`; in the unconstrained regime that is 10 of 758,585. They appear
  mainly at high N and in 10 ms cells, 6 of the 10 within 1.4 s of a cell
  starting. A keep-alive race would fit, but the cause is not established. Every
  affected cell held its invariants.
- **Experiment 3 predates the governance layer.** Its numbers come from a build
  without the audit trigger or RBAC. A 6-cell check with the trigger on (1 rep)
  was inside the measured range for `atomic` and `serializable` at N=64, and
  5–10% below the bottom of it for `for_update` at N=64 and `serializable` at
  N=8, so a small audit overhead is possible. It was not measured properly.

---

## Layout

```
app/enforcement/   naive.py, naive_single_txn.py, atomic.py, for_update.py, serializable.py,
                   settle.py (commit/release), base.py
app/auth.py        API keys and roles
app/faults.py      named crash points for Experiment 4
app/adapters/      opendp_adapter.py (real eps), passthrough.py, synthetic.py (stubs)
app/ledger/        init_db.py (schema + CHECK flag), queries.py (invariants)
app/main.py        reserve / commit / release / run, fault injection
experiments/       harness_asyncio.py (Exp 1&2), exp3_sweep.py, crash_test.py (Exp 4),
                   opendp_demo.py, verify_audit.py, demo_audit.py, demo_rbac.py
docs/              privatekube-comparison.md (Gate A)
results/           generated CSVs
```

## Known limitations

Stated here rather than discovered in the viva. None of them undermines the
safety result; each bounds what it covers.

- **An orphaned hold is not reclaimed automatically.** A crash between reserve
  and commit leaves budget `reserved` — correctly, since the operation may have
  run — but nothing expires it. Recovery today is a manual `POST
  /reservations/{id}/release`, which is what Experiment 4 exercises. The
  designed fix is a **lease**: give each reservation an `expires_at`, exclude
  expired rows from the reserve predicate, and have a reaper release them. That
  moves the failure from "budget locked forever" to "budget locked for at most
  one lease period", and it introduces a real trade-off — too short a lease
  releases a hold while the operation is still running, which is the one way
  this design could double-spend. Not built: on an unknown outcome the specified
  policy is to charge the hold, not release it (fail closed for privacy).
- **One contended row.** Every experiment targets a single dataset row. That is
  the worst case for safety and the right choice for proving the cap holds, but
  it says nothing about throughput across many datasets, where the hotspot
  disappears and the strategies should converge. Experiment 3 has no
  multi-dataset arm.
- **The audit chain proves less than it may seem to.** It shows that sealed rows
  were not changed. Rows are unprotected until sealed, about a second later.
  Someone with full database access can rewrite the whole chain or cut rows off
  its end; only comparing the head printed by `make verify-audit` with a copy kept
  elsewhere catches that.
- **RBAC is off by default.** The harnesses send no key, so it runs only when
  `API_KEYS` is set. Any real deployment must set it. Keys are static, with no
  rotation or expiry.
- **Basic composition, δ = 0.** The ledger sums ε linearly. `delta` is stored
  but no predicate reads it, and no advanced-composition or RDP accountant is
  wired in. This is deliberate — composition is the DP library's job, not the
  ledger's — and the reserve predicate generalises to a conjunction over both
  dimensions (or over per-α RDP terms) without changing the concurrency
  argument. But as it stands the system enforces the simplest accounting only.
- **Zero breaches is evidence, not proof.** 175 atomic runs found no violation.
  That is an empirical result over the tested conditions. The *argument* for why
  it cannot breach is PostgreSQL's documented READ COMMITTED behaviour: a
  concurrent `UPDATE` to the same row blocks the second updater until the first
  commits, after which the second **re-evaluates its `WHERE` against the newly
  committed row version** and affects zero rows if the predicate no longer
  holds. The measurement corroborates the argument; it does not replace it.
- **Three process kills.** The controlled-abort path has many runs, but the real
  `os._exit` path has only 3, because each one costs a container restart. Enough
  to show the protocol survives; too few to characterise anything rarer. Raise
  the count before the final report.
- **Single machine.** Harness, service and database share one host, so
  high-N latency includes client-side scheduling. Safety conclusions are
  unaffected — a breach either happened or it did not — but Experiment 3's cost
  numbers will need a no-op baseline and a stated saturation point.

---

## Scope

**In:** two-phase protocol, all reserve strategies, OpenDP and synthetic adapters,
Experiments 1–4 and their extensions, a hash-chained audit log, and RBAC.

**Not built:** dashboard, Opacus.

**Never in scope:** implementing DP mathematics, and deriving the cap. The cap is
an organizational **policy input**; this system enforces whatever cap it is
given.
