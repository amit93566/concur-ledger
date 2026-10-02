# concur-ledger

**Safe enforcement of shared privacy budgets under concurrent use.**

A finite, per-dataset differential-privacy budget (ε) shared by many concurrent
jobs. This repository shows that a plausible naive enforcement implementation
**can be raced into overspending the cap**, that a correct one **cannot**, and
that a crash mid-protocol **cannot corrupt the ledger**.

This is the **mid-term scope** (plan Phases 0–1, Experiments 1, 2 and 4).
The cost sweep (Experiment 3 — the primary result), the other two strategies,
and the governance layer are Phase 2–4 and deliberately absent.

Companion documents: `IMPLEMENTATION_PLAN.md` (what to build),
`WORKFLOWS.md` (how to run it), `DEMO_CHECKLIST.md` (what to prove).

---

## Results, reproduced on this machine

**The canonical, always-current view is `make report`** — it regenerates
`results/report.html` from the CSVs, organized by experiment (1, 2 and 4), so
no number in it can drift from the evidence. The tables below are a snapshot
from one such run and will differ in the tails from yours; breach/no-breach
verdicts are stable, the exact overshoot figures are not.

**Experiment 1 — the breach is real.** Cap 10, seeded at spent 6 (4 of
headroom), N concurrent reserves of ε=2. Only 2 should ever succeed.

| N | runs | breach rate | mean overshoot | max overshoot | mean granted | max granted |
|---:|---:|---:|---:|---:|---:|---:|
| 2 | 15 | **0%** | 0.000 | 0.000 | 2.00 | 2 |
| 3 | 15 | 100% | 2.000 | 2.000 | 3.00 | 3 |
| 5 | 15 | 100% | 6.000 | 6.000 | 5.00 | 5 |
| 10 | 15 | 100% | 5.600 | 8.000 | 4.80 | 6 |
| 20 | 15 | 100% | 5.333 | 14.000 | 4.67 | 9 |
| 50 | 15 | 100% | 7.200 | 14.000 | 5.60 | 9 |

N=2 is the control: two reserves of 2 fit exactly into 4 of headroom, so no
breach occurs and none should. From N=3 the naive path breaches every time,
granting up to **9 reservations where 2 fit** — the cap is exceeded by 14 ε,
i.e. **2.4× the budget**.

Overshoot is not monotonic in N: it peaks around N=10 and then flattens. The
mechanism is **measured, not proposed**. Every naive caller reports the values it
read in statement 1, and because each reservation increments `epsilon_reserved`
from a seeded zero, that read is a position in the write sequence: `reserved = 0`
means no peer's write was visible yet, so the caller was inside the race window.
Counted per run (`reads_in_window`):

| N | read inside the window | share of N | mean granted |
|---:|---:|---:|---:|
| 2 | 2.00 | 100% | 2.00 |
| 3 | 3.00 | 100% | 3.00 |
| 5 | 4.73 | 95% | 5.00 |
| 10 | 3.47 | 35% | 4.80 |
| 20 | 3.73 | 19% | 4.67 |
| 50 | 4.73 | 9% | 5.60 |

More contention does not produce more successful overspending: it produces more
callers arriving *after* the writes have landed, who are correctly denied. The
overspend is bounded by how many callers fit inside the window, not by how many
compete.

`make exp1c` supplies the intervention — hold N at 20 and widen the window with
`NAIVE_RACE_DELAY_MS` instead of raising N:

| delay (ms) | read inside the window | mean granted | mean overshoot |
|---:|---:|---:|---:|
| 0 | 3.70 / 20 | 4.60 | 5.20 |
| 5 | 6.00 / 20 | 7.10 | 10.20 |
| 25 | 19.00 / 20 | 19.30 | 34.60 |
| 100 | 20.00 / 20 | 20.00 | 36.00 |

Monotonic, saturating at ε 36 — the arithmetic maximum for 20 reserves of 2
against a cap of 10 seeded at 6. The plateau is a property of the window, not of
the concurrency. The delay is an instrument, not a claim about production
timings; the breach result at `delay=0` needs no instrument at all. Exact per-N
figures vary run to run; regenerate with `make exp1 exp1c`.

**The naive path is faithful, not sabotaged.** The strongest evidence is the
sequential control (`make exp1-control`): the *same* naive code, the same
requests, issued one at a time instead of together.

| N | runs | breach rate | reservations granted |
|---:|---:|---:|---:|
| 5 | 10 | **0%** | 2.00 |
| 20 | 10 | **0%** | 2.00 |

Exactly 2 granted, zero breaches. The implementation's logic is correct; only
concurrency breaks it. That is what makes it a plausible first implementation
rather than a strawman.

**Experiment 2 — the fix holds.** Identical seed and load, `strategy=atomic`,
with the database CHECK constraint still dropped:

| N | 2 | 3 | 5 | 10 | 20 | 50 | 100 |
|---|---|---|---|---|---|---|---|
| breach rate (25 runs each) | 0% | 0% | 0% | 0% | 0% | 0% | 0% |

175 runs, zero breaches, and **exactly 2 successes in every single run** — the
denial count is not merely safe, it is exactly correct. I1, I2 and I4 hold in
175/175.

**Experiment 4 — the crash is survivable.** 15/15 checks pass under controlled
abort, 14/14 under real process kill — `os._exit` inside the reserve→commit gap,
with Docker's own `RestartCount` going 0 → 3 as independent corroboration that
the container really died and came back. Budget survives a crash as a hold,
never as a loss and never as a double-spend; a retried commit charges exactly
once; releasing a committed reservation is rejected.

The ledger also survives a full `docker compose down && up` — 209 records
persisted through container recreation, which is what the named volume is for.

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
make report       # results/report.html  -- the argued, explanatory version
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

One statement, so check and write cannot interleave. `for_update` and
`serializable` are Phase 2 and return HTTP 501 rather than silently falling back
to something safe.

### Invariants

### Isolation level

Pinned to **READ COMMITTED** in `app/db.py`, not inherited. The atomic strategy's
safety argument is specific to it: when a concurrent `UPDATE` commits first,
Postgres re-evaluates the second updater's `WHERE` against the newly committed
row version and returns rowcount 0 if it no longer fits. At REPEATABLE READ or
SERIALIZABLE the same situation raises SQLSTATE `40001` instead, which is safe
only with a retry loop — that is the Phase 2 `serializable` strategy. The server
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

### Measurement-validity caveat

The harness and the server share one machine. `reserved_at_spread_ms` grows with
N — ~2 ms at N=5, ~102 ms at N=50, ~224 ms at N=100 — so at the top of the range
some of that spread is client-side scheduling, not database behaviour. This does
not affect the Experiment 1 and 2 *safety* conclusions (a breach either happened
or it did not), but it is why Experiment 3's latency numbers will need a no-op
baseline and a stated saturation point.

---

## Layout

```
app/enforcement/   naive.py, atomic.py, settle.py (commit/release), base.py
app/adapters/      opendp_adapter.py (real eps), passthrough.py (stub)
app/ledger/        init_db.py (schema + CHECK flag), queries.py (invariants)
app/main.py        reserve / commit / release / run, fault injection
experiments/       harness_asyncio.py (Exp 1&2), crash_test.py (Exp 4), opendp_demo.py
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
  this design could double-spend. Not built; Phase 2.
- **One contended row.** Every experiment targets a single dataset row. That is
  the worst case for safety and the right choice for proving the cap holds, but
  it says nothing about throughput across many datasets, where the hotspot
  disappears and the strategies should converge. Experiment 3 should carry at
  least one multi-dataset arm.
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

**In (mid-term):** two-phase protocol, naive + atomic, OpenDP adapter,
Experiments 1/2/4, Gate A.

**Out (Phase 2–4):** `for_update` and `serializable` strategies, the synthetic
duration adapter, Experiment 3 (the cost sweep — the primary result), Opacus,
RBAC, hash-chained audit log, dashboard.

**Never in scope:** implementing DP mathematics, and deriving the cap. The cap is
an organizational **policy input**; this system enforces whatever cap it is
given.
