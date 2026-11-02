# concur-ledger

**Safe enforcement of shared privacy budgets under concurrent use.**

A finite, per-dataset differential-privacy budget (ε) shared by many concurrent
jobs. This repository shows that a plausible naive enforcement implementation
**can be raced into overspending the cap**, that a correct one **cannot**, and
that a crash mid-protocol **cannot corrupt the ledger**.

This covers Experiments 1–4: four reserve strategies, the cost sweep, a duration
calibrated from real DP-SGD training, the isolation, epsilon and extended fault
evidence, and a governance layer (a tamper-evident audit log, role-based access
and a web dashboard).

The planning documents (`IMPLEMENTATION_PLAN.md`, `WORKFLOWS.md`,
`DEMO_CHECKLIST.md`) are kept outside this repository.

---

## Results, reproduced on this machine

**The canonical, always-current view is `make report`** — it regenerates
`results/report.html` from the CSVs, organized by experiment, so
no number in it can drift from the evidence. The tables below are a snapshot
from one such run and will differ in the tails from yours; breach/no-breach
verdicts are stable, the exact overshoot figures are not.

`results/` is gitignored, so the CSVs and figures are not in this repository. The
`make` targets below and `scripts/run_final.sh` regenerate them; the dashboard's
summary of Experiment 3 is committed as `dashboard/public/results.json`.

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
25 | 75 | 151 | 119 | 127 | 79 | 73 retries in total at N = 2, 3, 5, 10, 20, 50, 100, with no give-ups. They fall at
high N because late arrivals see a full budget and are denied without a conflict.

**Experiment 4 — the crash is survivable.** Budget survives a crash as a hold,
never as a loss and never as a double-spend; a retried commit charges exactly
once; releasing a committed reservation is rejected. `make exp4-ext` adds crash
points the reserve→commit gap cannot reach, 10 repetitions each. All of these ran
on the final build, dashboard included:

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

In db-kill, 1207 acknowledged, 0 missing, and 166 committed but never acknowledged: the commit
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
- **How far `serializable` falls is not stable between runs.** At N=64, 10 ms it
  gave 78/s in the pilot and 89/s in the four-worker trial on 29 Oct, 15–20/s in
  this sweep, and about 10/s unpinned on 1 Nov. Pinning was ruled out as the
  cause; the rest is unexplained. In every run it was slower than `atomic` and
  the only strategy that gave up.
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

**The long operation, calibrated.** The 1 s duration above was an assumption.
`experiments/calibrate_dpsgd.py` measures a real job: DP-SGD on MNIST, CPU only,
with the Opacus example settings (15 epochs, 3 runs). Training took 206.8–234.6 s,
median 212.6 s, so a real job is about 200 times longer than 1 s. One epoch took
about 14 s. It reached ε = 0.922 at δ = 1e-5 and 94.3–94.9% test accuracy, on
Opacus 1.6.0 and torch 2.15.0. The published figure for these settings is ε = 1.16,
from an older RDP conversion; the same accountant gives both.

Experiment 3 was then repeated at 14 s, one real epoch, in both regimes: 72 cells
(4 strategies × 3 values of N × 2 regimes × 3 reps), 84 s measured per cell. Every
cell held I1 and I2, with 0 client timeouts and 0 deadlocks. Median completed
cycles/s:

| 14 s operation | unconstrained N=8 | 32 | 100 | near cap N=8 | 32 | 100 |
|---|---:|---:|---:|---:|---:|---:|
| atomic | 0.57 | 2.29 | 7.14 | 0.57 | 0.71 | 0.71 |
| for_update | 0.57 | 2.29 | 7.14 | 0.57 | 0.71 | 0.71 |
| serializable | 0.57 | 2.29 | 7.14 | 0.57 | 0.71 | 0.71 |
| naive (unsafe) | 0.57 | 2.29 | 7.14 | 0.57 | 1.37 | 0.81 |

- **Operation length does not reorder the strategies.** It removes `serializable`'s
  throughput penalty once the request rate is low: all four are level at every N
  here, where at 1 s it had fallen to 12.9/s at N=100. The drop seems to follow
  N ÷ duration rather than N alone, which fits these three durations but was not
  tested directly. `serializable` is not free even so: it gave up 20 times at
  unconstrained N=100.
- **Near the cap, budget sets the rate.** The safe strategies sit at 0.71/s, which
  is 10 holds ÷ 14 s. `naive` reaches 1.37/s at N=32, which means about 19 holds
  running at once against a cap that allows 10.
- **A full 213 s job was not swept.** Each cell would take well over 15 minutes.
  At that length N=100 is about 0.5 cycles/s, so the same result is expected, but
  that is extrapolation.

```bash
../.venv-opacus/bin/python experiments/calibrate_dpsgd.py   # see requirements-opacus.txt
make pin
ARGS="--ns 8,32,100 --durations 14000 --reps 3 --warmup 20 --measure 84 --timeout 120 --resume"
taskset -c 10,11 .venv/bin/python experiments/exp3_sweep.py --regime unconstrained $ARGS --out results/exp3_calibrated.csv
taskset -c 10,11 .venv/bin/python experiments/exp3_sweep.py --regime near_cap $ARGS --out results/exp3_calibrated_nearcap.csv
make unpin
```

**Real ε, end to end.** An OpenDP noisy count at Laplace scale 0.5 costs
ε = 2.0. Against cap 10 / spent 6, two such queries commit (reaching exactly
10/10) and the third is denied with HTTP 429. The cost enforced is the cost
OpenDP reported.

> Empirically demonstrated **under the tested conditions**, not proven.

---

## Quick start

```bash
cp .env.example .env          # set DB_PORT if 5432 is already taken locally
docker buildx build --allow network.host --network host -t concur-ledger-app --load .
docker compose up -d          # Postgres (named volume) + the service
make health
```

The explicit build is explained in the [build note](#build-note-wsl2).

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
make exp2-safe    # Experiment 2 for all three safe strategies
make exp3         # the cost sweep (overnight); exp3-nearcap for the near-cap regime
make exp4-ext     # crash points inside and after the transactions
make demo-locks   # PRESENTATION: lock behaviour, live
make demo-audit   # edit a sealed audit row; verification fails at that row
make demo-rbac    # viewer gets 403; actor comes from the key
make verify-audit # walk the audit hash chain
make dashboard    # the web console, with demo keys
make watch        # live ledger view for a second terminal during the demo
```

`scripts/run_final.sh` runs every experiment behind the final numbers in order, and
resumes after an interruption. A sweep takes about 10 hours.

---

## Dashboard

`make dashboard` rebuilds the results summary and the image, then serves a React and
Vite console at <http://localhost:8000/dashboard/> with three demo keys:

| Key | Role | Can do |
|---|---|---|
| `demo-viewer-key` | viewer | See everything |
| `demo-analyst-key` | analyst | Also verify the audit chain |
| `demo-admin-key` | admin | Also release or charge a stale hold |

- **Burn-down:** spent, held and free budget per dataset, live, with datasets past
  their cap listed first. Opening a dataset shows its invariants and recent records.
- **Stale holds:** reservations older than a chosen age. Release returns the
  budget; Charge commits the full hold, the policy when the outcome is unknown.
- **Audit log:** recent rows, who settled each hold, and a Verify chain button.
  Verifying took about 13 s at 1.36 million rows, and the service stayed responsive.
- **Results:** the calibration and the Experiment 3 charts. These are stored
  results, not live data.

The harnesses cannot run with RBAC on, so `make dashboard` leaves it on and tells you
to turn it off with `docker compose up -d app` before any experiment. With RBAC off the
console opens with no sign-in, shows a warning, and every action is open.

These keys are for demos only. A real deployment needs its own `API_KEYS`.

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

### The reserve strategies

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

### Isolation level

Pinned to **READ COMMITTED** in `app/db.py`, not inherited. The atomic strategy's
safety argument is specific to it: when a concurrent `UPDATE` commits first,
Postgres re-evaluates the second updater's `WHERE` against the newly committed
row version and returns rowcount 0 if it no longer fits. At REPEATABLE READ or
SERIALIZABLE the same situation raises SQLSTATE `40001` instead, which is safe
only with a retry loop — that is the `serializable` strategy. The server
default was already READ COMMITTED, so pinning it invalidates no earlier result;
it makes a dependency explicit. Every results row now records the level in force.

### Invariants

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

Docker Compose 5.6 with buildx 0.38 refuses that setting in `docker compose build`
and asks for `--allow=network.host`, which it does not accept. Build the image
directly instead, as `make dashboard` does:

```bash
docker buildx build --allow network.host --network host -t concur-ledger-app --load .
```

### Measurement-validity caveats

The harness and the server share one machine, and four things about that bear on
how the numbers are read.

- **Harness bug, fixed; mid-term figures are superseded.** The mid-term harness
  sized its client connection limit from the first N in a sweep, so "N=100" had
  at most 22 requests in flight. Every number here is from the fixed harness,
  which records `max_in_flight`: all N requests overlapped in every run. The
  mid-term safety conclusions still held.
- **Admission spread.** The single service process admits requests one at a time,
  a few ms apart, so `arrival_spread_ms` grows with N: ~13 ms at N=5,
  ~216 ms at N=50, ~714 ms at N=100 for `atomic`. This does not affect the safety
  conclusions (a breach either happened or it did not), but it is why only a few
  backends wait on the row lock at any instant.
- **Idle-vCPU wake-up latency (WSL2).** An atomic reserve takes ~4 ms back to back,
  ~9 ms after a 10 ms idle gap and ~20 ms after 50 ms. Experiment 3 runs SCHED_IDLE
  spinners to remove this (`--keep-awake`, recorded per row). Experiments 1, 2 and
  4 do not, so treat their latencies as upper bounds; their verdicts are unaffected.
- **Ceilings in Experiment 3.** At 10 ms operations, throughput is capped by the one
  hot dataset row, naive included. In one repetition at N=64, four uvicorn workers did
  not raise it for `atomic` (143/s against 147/s), but `serializable` gave up far less
  (302 against 1,563) and reached 89/s. That was not followed up (`UVICORN_WORKERS`
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
  affected cell held its invariants. The 14 s sweep had 1 more, cause also unknown.
- **The 14 s sweep ran across two days.** It stopped after 70 of 72 cells on 1 Nov,
  cause unknown. Two near-cap cells (`atomic` N=8 and `serializable` N=32, rep 3) ran
  on 2 Nov on the same image, on battery. Their throughput matched reps 1 and 2
  exactly. `serializable` N=32 rep 3 denied 165 requests/s against 224 and 229, the
  lowest of the 12 cells at that N, so quote near-cap denial rates there as a range.
- **Experiment 3's main sweep predates the governance layer.** Its numbers come from
  a build without the audit trigger or RBAC. The 14 s sweep had the trigger on, so
  the two are not from one build. A 6-cell check with the trigger on (1 rep)
  was inside the measured range for `atomic` and `serializable` at N=64, and
  5–10% below the bottom of it for `for_update` at N=64 and `serializable` at
  N=8, so a small audit overhead is possible. It was not measured properly.
- **What ran on the final build.** Experiment 4 (72/72 checks), `exp2-safe` (525
  runs, exactly 2 granted in each), the audit demo and the RBAC demo were re-run on
  the build with the dashboard. Experiments 1 and 3 and the isolation and ε evidence
  come from earlier builds. The dashboard's routes are not on the reserve path; the
  schema gained one nullable column, `resolved_by`.

---

## Layout

```
app/enforcement/   naive.py, naive_single_txn.py, atomic.py, for_update.py, serializable.py,
                   settle.py (commit/release), base.py
app/auth.py        API keys and roles
app/faults.py      named crash points for Experiment 4
app/adapters/      opendp_adapter.py (real eps), passthrough.py, synthetic.py (stubs)
app/ledger/        init_db.py (schema + CHECK flag), queries.py (invariants),
                   audit.py (audit view, chain check, stale holds)
app/main.py        reserve / commit / release / run, fault injection, audit and hold routes
dashboard/         the web console (React + Vite), served at /dashboard
experiments/       harness_asyncio.py (Exp 1&2), exp3_sweep.py, crash_test.py (Exp 4),
                   calibrate_dpsgd.py, dashboard_results.py, opendp_demo.py,
                   verify_audit.py, demo_audit.py, demo_rbac.py
docs/              privatekube-comparison.md (Gate A)
results/           generated CSVs and figures, gitignored
```

## Known limitations

Stated here rather than discovered in the viva. None of them undermines the
safety result; each bounds what it covers.

- **An orphaned hold is not reclaimed automatically.** A crash between reserve
  and commit leaves budget `reserved` — correctly, since the operation may have
  run — but nothing expires it. Recovery is an operator's decision: `GET /holds` and
  the dashboard list stale holds, and an admin releases or charges each one through
  `POST /holds/{id}/resolve`, which records who did it. The designed automatic fix is a **lease**: give each reservation an `expires_at`, exclude
  expired rows from the reserve predicate, and have a reaper release them. That
  moves the failure from "budget locked forever" to "budget locked for at most
  one lease period", and it introduces a real trade-off — too short a lease
  releases a hold while the operation is still running, which is the one way
  this design could double-spend. Leases are not built, because they would change
  the measured reserve code. On an unknown outcome the policy is to charge the hold,
  not release it (fail closed for privacy).
- **One contended row.** Every experiment targets a single dataset row. That is
  the worst case for safety and the right choice for proving the cap holds, but
  it says nothing about throughput across many datasets, where the hotspot
  disappears and the strategies should converge. Experiment 3 has no
  multi-dataset arm.
- **The audit chain proves less than it may seem to.** It shows that sealed rows
  were not changed. Rows are unprotected until sealed, about a second later.
  Someone with full database access can rewrite the whole chain or cut rows off
  its end; only comparing the head shown by `make verify-audit` or the dashboard
  with a copy kept elsewhere catches that.
- **RBAC is off by default.** The harnesses send no key, so it runs only when
  `API_KEYS` is set. Any real deployment must set it. Keys are static, with no
  rotation or expiry.
- **Basic composition, δ = 0.** The ledger sums ε linearly. `delta` is stored
  but no predicate reads it, and no advanced-composition or RDP accountant is
  wired in. This is deliberate — composition is the DP library's job, not the
  ledger's — and the reserve predicate generalises to a conjunction over both
  dimensions (or over per-α RDP terms) without changing the concurrency
  argument. But as it stands the system enforces the simplest accounting only.
  A DP-SGD job shows the gap: its cost is ε = 0.922 at δ = 1e-5, and the ledger could
  charge only the ε.
- **Zero breaches is evidence, not proof.** 175 atomic runs found no violation.
  That is an empirical result over the tested conditions. The *argument* for why
  it cannot breach is PostgreSQL's documented READ COMMITTED behaviour: a
  concurrent `UPDATE` to the same row blocks the second updater until the first
  commits, after which the second **re-evaluates its `WHERE` against the newly
  committed row version** and affects zero rows if the predicate no longer
  holds. The measurement corroborates the argument; it does not replace it.
- **Three process kills.** The controlled-abort path has many runs, but the real
  `os._exit` path has only 3, because each one costs a container restart. The
  extended kill points have 10 each. Enough to show the protocol survives; too few
  to characterise anything rarer.
- **Single machine.** Harness, service and database share one host, so
  high-N latency includes client-side scheduling. Safety conclusions are
  unaffected — a breach either happened or it did not — and Experiment 3's cost
  numbers should be read against the no-op ceiling above.

---

## Scope

**In:** two-phase protocol, all reserve strategies, OpenDP and synthetic adapters,
Experiments 1–4 and their extensions, a DP-SGD calibration through Opacus, a
hash-chained audit log, RBAC, operator handling of stale holds, and a web dashboard.

**Not built:** automatic leases for orphaned holds, δ enforcement, and an RDP
accountant in the ledger. The calibration measures a job's cost; it does not run
through the ledger.

**Never in scope:** implementing DP mathematics, and deriving the cap. The cap is
an organizational **policy input**; this system enforces whatever cap it is
given.
