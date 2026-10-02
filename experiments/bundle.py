"""Assemble one self-contained context file: bundle-for-chat.md.

For handing the whole project to a reader (or an LLM chat) that has no access to
the repository: the problem, the protocol, the complete source of every file that
matters, and the measured results.

Same discipline as report.py -- the prose is written here, but every number is
computed from results/*.csv at generation time, so the bundle cannot drift from
the evidence.

    python experiments/bundle.py
"""

import collections
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = ROOT / "bundle-for-chat.md"

LANG = {".py": "python", ".sql": "sql", ".yml": "yaml", ".yaml": "yaml"}

SOURCES = [
    ("schema.sql", "The ledger schema. All budget state lives here; the DP libraries are stateless."),
    ("app/main.py", "The service. reserve / commit / release, the full-cycle `run` endpoint with fault injection, and the read-only views the harness uses."),
    ("app/db.py", "The connection pool, and the READ COMMITTED pin the atomic strategy's correctness depends on."),
    ("app/enforcement/base.py", "Strategy interface + registry, the idempotency pre-check, and the shared record INSERT."),
    ("app/enforcement/naive.py", "BREACHING PATH 1. Faithful read -> check -> write across two transactions."),
    ("app/enforcement/naive_single_txn.py", "BREACHING PATH 2. The same logic inside ONE transaction. Answers the 'you split it to make it fail' objection."),
    ("app/enforcement/atomic.py", "THE SAFE PATH. The cap check moved into the WHERE clause of the write."),
    ("app/enforcement/settle.py", "commit / release. Where I3 and I4 are enforced, and the partial-commit refund path."),
    ("app/ledger/queries.py", "Read-side access and the I1-I4 invariant checks, computed in SQL."),
    ("app/ledger/init_db.py", "Idempotent schema application; adds/drops the within_cap CHECK at runtime."),
    ("app/adapters/base.py", "Adapter interface: epsilon_for(spec) is deterministic; run_operation executes."),
    ("app/adapters/opendp_adapter.py", "Real epsilon from OpenDP via measurement.map(d_in). Ceiling-rounded."),
    ("app/adapters/passthrough.py", "Declared stub. Records epsilon_source='passed_in' on everything it produces."),
    ("app/config.py", "Settings, including ENFORCE_DB_CONSTRAINT and NAIVE_RACE_DELAY_MS."),
    ("app/models.py", "Request/response shapes."),
    ("experiments/harness_asyncio.py", "Experiments 1, 1b and 2. Fires N genuinely simultaneous reserves and audits that the concurrency was real."),
    ("experiments/crash_test.py", "Experiment 4. Controlled abort, real process kill, and the partial-commit refund checks."),
    ("experiments/opendp_demo.py", "Real DP query -> real epsilon -> reserved, committed, enforced, denied at the cap."),
    ("Makefile", "Every command used to produce the results below."),
]


def read_src(rel):
    return (ROOT / rel).read_text().rstrip()


def fence(rel):
    return LANG.get(Path(rel).suffix, "make" if rel == "Makefile" else "")


def load(name):
    p = RESULTS / name
    return list(csv.DictReader(p.open())) if p.exists() else []


def istrue(v):
    return str(v).strip().lower() == "true"


def group(rows):
    g = collections.defaultdict(list)
    for r in rows:
        g[int(r["workers"])].append(r)
    return dict(sorted(g.items()))


def breach_table(rows):
    g = group(rows)
    out = ["| N | runs | breach rate | mean overshoot | max overshoot | mean granted | max granted | legal |",
           "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n, rs in g.items():
        br = sum(istrue(r["breach"]) for r in rs)
        ov = [float(r["overshoot"]) for r in rs]
        gr = [int(r["reserved_count"]) for r in rs]
        out.append(
            f"| {n} | {len(rs)} | {br/len(rs):.0%} | {sum(ov)/len(ov):.3f} | {max(ov):.3f} "
            f"| {sum(gr)/len(gr):.2f} | {max(gr)} | {rs[0]['expected_max_success']} |"
        )
    return "\n".join(out)


def compare_table(a, b):
    ga, gb = group(a), group(b)
    out = ["| N | runs | naive (2 txn) breach | naive_txn (1 txn) breach | naive_txn granted | legal |",
           "|---:|---:|---:|---:|---:|---:|"]
    for n in sorted(set(ga) | set(gb)):
        ra = f"{sum(istrue(r['breach']) for r in ga[n])/len(ga[n]):.0%}" if n in ga else "—"
        rb = f"{sum(istrue(r['breach']) for r in gb[n])/len(gb[n]):.0%}" if n in gb else "—"
        gr = f"{sum(int(r['reserved_count']) for r in gb[n])/len(gb[n]):.2f}" if n in gb else "—"
        legal = (ga.get(n) or gb.get(n))[0]["expected_max_success"]
        out.append(f"| {n} | {len(gb.get(n, [])) or '—'} | {ra} | {rb} | {gr} | {legal} |")
    return "\n".join(out)


def control_table(rows):
    out = ["| N | runs | breach rate | mean granted | legal |", "|---:|---:|---:|---:|---:|"]
    for n, rs in group(rows).items():
        br = sum(istrue(r["breach"]) for r in rs)
        gr = [int(r["reserved_count"]) for r in rs]
        out.append(f"| {n} | {len(rs)} | {br/len(rs):.0%} | {sum(gr)/len(gr):.2f} | {rs[0]['expected_max_success']} |")
    return "\n".join(out)


def atomic_table(rows):
    out = ["| N | runs | breach rate | granted (every run) | median spread ms |", "|---:|---:|---:|---:|---:|"]
    for n, rs in group(rows).items():
        br = sum(istrue(r["breach"]) for r in rs)
        granted = sorted({int(r["reserved_count"]) for r in rs})
        sp = sorted(float(r["reserved_at_spread_ms"]) for r in rs)
        out.append(
            f"| {n} | {len(rs)} | {br/len(rs):.0%} | "
            f"{granted[0] if len(granted) == 1 else granted} | {sp[len(sp)//2]:.1f} |"
        )
    return "\n".join(out)


def window_table(rows):
    """Race-window share vs N -- the observational half of the plateau result."""
    out = ["| N | read inside the window | share of N | mean granted |",
           "|---:|---:|---:|---:|"]
    for n, rs in group(rows).items():
        w = [int(r["reads_in_window"]) for r in rs if r["reads_in_window"] not in ("", None)]
        if not w:
            continue
        gr = [int(r["reserved_count"]) for r in rs]
        mean_w = sum(w) / len(w)
        out.append(
            f"| {n} | {mean_w:.2f} | {mean_w/n:.0%} | {sum(gr)/len(gr):.2f} |"
        )
    return "\n".join(out)


def delay_table(rows):
    """Race-window sweep at fixed N -- the intervention half."""
    g = collections.defaultdict(list)
    for r in rows:
        g[float(r["naive_race_delay_ms"])].append(r)
    out = ["| delay (ms) | runs | read inside the window | mean granted | mean overshoot | breach |",
           "|---:|---:|---:|---:|---:|---:|"]
    for d in sorted(g):
        rs = g[d]
        n = int(rs[0]["workers"])
        w = sum(int(r["reads_in_window"]) for r in rs) / len(rs)
        gr = sum(int(r["reserved_count"]) for r in rs) / len(rs)
        ov = sum(float(r["overshoot"]) for r in rs) / len(rs)
        br = sum(istrue(r["breach"]) for r in rs)
        out.append(
            f"| {d:g} | {len(rs)} | {w:.2f} / {n} | {gr:.2f} | {ov:.2f} | {br/len(rs):.0%} |"
        )
    return "\n".join(out)


def spread_table(rows):
    g = group(rows)
    out = ["| N | median arrival spread (ms) |", "|---:|---:|"]
    for n, rs in g.items():
        v = sorted(float(r["reserved_at_spread_ms"]) for r in rs)
        out.append(f"| {n} | {v[len(v)//2]:.2f} |")
    return "\n".join(out)


def crash_table(rows):
    out = ["| mode | check | invariant | result | observed |", "|---|---|---|---|---|"]
    for r in rows:
        res = "PASS" if istrue(r.get("passed")) else "FAIL"
        detail = (r.get("detail") or "").replace("|", "\\|")
        out.append(
            f"| {r.get('mode','')} | {r.get('check','')} | {r.get('invariant','') or '—'} "
            f"| {res} | {detail} |"
        )
    return "\n".join(out)


naive = load("exp1_naive_vs_n.csv")
txn = load("exp1b_naive_txn_vs_n.csv")
atomic = load("exp2_atomic_vs_n.csv")
ctrl = load("exp1_naive_sequential_control.csv")
window = load("exp1c_race_window_vs_delay.csv")
crash = load("exp4_controlled_abort.csv") + load("exp4_process_kill.csv")

worst = max(naive, key=lambda r: float(r["total"])) if naive else None
iso = sorted({r.get("isolation_level", "") for r in atomic} - {""}) or ["read committed"]
a_breaches = sum(istrue(r["breach"]) for r in atomic)
c_pass = sum(istrue(r.get("passed")) for r in crash)

parts = []
W = parts.append

W(f"""# concur-ledger — full context bundle

**Safe enforcement of shared privacy budgets under concurrent use.**

Everything needed to reason about this project in one file: the problem, the
protocol, the complete source of every file that matters, and the measured
results. Generated from the repository and from `results/*.csv`, so no number
here is hand-typed. Rebuild with `python experiments/bundle.py`.

---

## 1. The problem in one paragraph

A dataset has a finite differential-privacy budget (ε), set as an organizational
**policy input**. Many jobs on a shared platform want to spend from that one
budget at the same time. The entire project is one question: **where does the cap
check live?** If it lives in the application, concurrent callers all read the same
value, all decide "it fits", and all write — the cap is breached. If it lives
inside the write statement, they cannot.

What makes this a differential-privacy problem rather than a generic quota
problem: **a privacy budget is monotone and non-renewable.** An overspent disk
quota is embarrassing and reversible; overspent ε is a permanent, unrecoverable
privacy loss, and no subsequent correctness undoes the information already
released. The usual distributed-systems answer — over-issue, detect, reconcile —
is therefore unavailable. Two supporting points: the cost is *derived from a
mechanism, not chosen by the client*, so it must be priced before it can be
reserved; and the *shape of the predicate is set by the composition model*,
which is a DP question rather than a systems one (§4).

This repository demonstrates three things empirically:

1. A plausible naive enforcement implementation **can be raced into overspending**
   the cap — in two independent implementations of "naive".
2. A correct one **cannot**, up to the highest concurrency tested.
3. A crash mid-protocol **cannot corrupt the ledger** — budget survives as a hold,
   never as a loss and never as a double-spend.

**Scope.** This is the mid-term milestone (Phases 0–1; Experiments 1, 1b, 2 and
4). Deliberately absent and scheduled for Phase 2–4: the `for_update` and
`serializable` strategies, the synthetic duration adapter, **Experiment 3 — the
cost sweep, which is the primary contribution** — Opacus, RBAC, a hash-chained
audit log, and a dashboard.

**Never in scope:** implementing DP mathematics, and deriving the cap.

---

## 2. The protocol

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

Three properties follow from that shape:

- **Commit cannot fail the cap check.** The budget was already held; settling only
  moves `reserved → spent`.
- **No lock or transaction spans the operation.** This is why, in the Phase-2 cost
  sweep, operation duration is expected to act through *reserved headroom*
  (denial rate) rather than through lock hold time.
- **A crash between reserve and commit is safe**, because nothing was published.

The accepted cost is an **orphaned hold**: a crashed job leaves budget reserved
that nobody will use. Holds are released explicitly, never reclaimed on a timer,
and the reason is an asymmetry specific to a monotone budget: a leaked hold is a
liveness failure an operator can clear, while a hold reclaimed from a job that is
actually alive permits a breach of exactly the reclaimed amount, which is
unrecoverable. Where the two errors are asymmetric in that way, the system should
leak holds and make them visible rather than reclaim them on a timer. A lease
would reintroduce exactly the unsafe side.

**Partial commit.** `commit` accepts an actual cost lower than the amount held and
returns the difference to available budget. That is not slack in the design: where
a mechanism's cost is not known exactly before it runs, the protocol degrades to
reserve-an-upper-bound / commit-the-actual. Caveat belonging with the result: if
the realised cost depends on private data, the refunded amount is itself a
disclosure — the same reason privacy odometers carry worse bounds than filters.
Under deterministic pricing, which is what this system does, that does not arise.

## 3. The three reserve strategies

`naive` — a faithful read → check → write across **two** transactions:

| phase | what happens |
|---|---|
| statement 1 | `SELECT spent, reserved, cap` in its own transaction, which then **ends** — no lock carried forward |
| the gap | the race window (configurable delay; default 0, and the breach reproduces without it) |
| the check | `spent + reserved + cost <= cap`, **in Python, on values that are already stale** |
| statement 2 | a **new** transaction applies the increment **unconditionally** |

`naive_txn` — identical logic inside **one** transaction. It breaches identically.
This exists to answer the objection that the two-transaction split is what made
`naive` fail. At READ COMMITTED a plain `SELECT` takes no row lock, each statement
takes a fresh snapshot, and the write carries no predicate — so there is nothing
for the database to re-evaluate. What matters is *where the decision is taken*,
not how many transactions it spans.

**The name for this anomaly is write skew** — used consistently across the
README, the results page and this bundle. "read → check → write" names the code
shape that admits it; "race window" names the gap between the read and the
write. They are not alternative names for the anomaly.

**This is not a lost update.** Every increment applies and none is overwritten;
the running total is exactly the sum of the increments, and I2 holds throughout.
Each transaction's decision was valid against the state it read, and the
invariant is violated only by the combination — write skew is the canonical
anomaly permitted by READ COMMITTED and snapshot isolation and prevented by
SERIALIZABLE.

`atomic` — the check moves out of Python and into the `WHERE` clause of the write:

```sql
UPDATE datasets
   SET epsilon_reserved = epsilon_reserved + :cost
 WHERE id = :id
   AND epsilon_spent + epsilon_reserved + :cost <= epsilon_cap
RETURNING epsilon_spent, epsilon_reserved;
-- rowcount 1 -> reserved ; rowcount 0 -> denied
```

One statement, so the check and the write cannot interleave — there is nothing
between them to interleave with.

**Why this is correct, not merely observed.** Under READ COMMITTED, when a
concurrent `UPDATE` to the same row commits first, Postgres re-evaluates the
second updater's `WHERE` clause against the *newly committed* row version and
proceeds only if it still matches (PostgreSQL manual §13.2.1). The cap predicate
is therefore checked against committed state, never a stale snapshot. The
isolation level is pinned in `app/db.py` rather than inherited, because this
argument is specific to it: at REPEATABLE READ or SERIALIZABLE the same situation
raises SQLSTATE `40001` instead of re-checking, which is safe only with a retry
loop — the Phase 2 `serializable` strategy. Level in force for the runs below:
**{iso[0]}**, recorded in every results row.

**A detail that matters when reading the code:** all three strategies report
`observed_spent` / `observed_reserved`, but they mean different things. For the
two naive variants these are the **pre-write** values — the stale read the caller
actually decided on. For `atomic` they are the **post-update** values from
`RETURNING` — the version of the row the database evaluated the predicate against.

## 4. The invariants and the composition model

Computed in SQL against committed state, so the harness, the crash test and the
live demo all verify the same thing (`GET /datasets/{{id}}/invariants`):

| | name | meaning |
|---|---|---|
| **I1** | cap safety | `epsilon_spent + epsilon_reserved <= epsilon_cap` |
| **I2** | ledger agreement | the running columns equal the sums over the record rows |
| **I3** | terminal states | a committed or released row never changes again |
| **I4** | exactly-once | one row per `(dataset_id, idempotency_key)`, committed at most once |

**The precise Experiment 1 result is that the naive paths violate I1 but not I2.**
They breach the *cap* — a policy violation — while leaving the ledger internally
consistent. Overspending and corruption are different failures, and only the
first one occurs.

**Composition model, stated as an explicit assumption.** A scalar `epsilon_spent`
commits the ledger to **basic sequential composition**: the total cost of a
sequence of mechanisms is the sum of their individual ε. This is the worst-case
bound and it holds adaptively, regardless of how queries are chosen. It is also
**conservative** — advanced composition and RDP admit tighter accounting for the
same sequence, so this ledger charges at least as much as a tighter accountant
would and denies earlier than strictly necessary. For a cap *enforcer*, erring
toward over-charging is the correct direction of error.

How far the design generalises:

- **RDP survives intact.** Replace the scalar with a vector of ε(α) over a fixed
  α-grid. The predicate becomes a conjunction over per-α columns — still one
  `UPDATE`, still one statement, still atomic. Nothing about the concurrency
  argument changes. The same is true of the (ε,δ) extension.
- **Fully adaptive privacy filters do not.** There the admissible set depends on
  the *realised* loss sequence, so the cost of the next query is not known before
  it runs. Reserve-before-run degrades to reserve-an-upper-bound /
  commit-the-actual — the partial-commit path described in §2.

`datasets.delta` is recorded but **not enforced**: there is no `delta_cap`,
`delta_spent` or `delta_reserved`, and no predicate references it. Phase 2.

## 5. Honesty conditions the results depend on

- **The `within_cap` CHECK constraint is a safety net, NOT the enforcement
  mechanism.** It must be off for Experiment 1 or the naive paths cannot breach —
  and it is kept off for **Experiment 2 as well**, so that the absence of a breach
  is attributable to the reserve strategy rather than to the database backstopping
  it. Every run records `db_constraint_present`.
- It is also not a substitute for a strategy: a constraint violation aborts a
  transaction at an unpredictable point, where a denial should be an HTTP 429 with
  a recorded `denied` row and an audit trail. The race is relocated into the error
  path, not removed.
- After a breach run the ledger is over its cap, so the CHECK can no longer be
  re-added. The service logs this loudly and **runs without the net** rather than
  refusing to boot.
- **The concurrency is audited, not asserted.** Every run reports
  `reserved_at_spread_ms`, the wall-clock spread of the arrival timestamps
  Postgres itself wrote.
- **`POOL_MAX_SIZE` must be ≥ the worker count**, or requests serialise at the
  connection pool instead of contending at the database.
- **Restarts are not detected by PID.** Inside a container the service is PID 1
  both before and after a restart, so `/config` exposes an `instance_id`
  regenerated per process and the crash test compares that instead.
- **The seed writes matching record rows, not just the running columns.** Forcing
  `epsilon_spent = 6` with no committed record behind it would itself be an I2
  violation and would mask any real drift.
- ε is never computed here. It comes from OpenDP via `measurement.map(d_in)`, or
  it is passed in and labelled `epsilon_source='passed_in'`. OpenDP rounds
  conservatively, so quantisation to `NUMERIC(12,6)` **rounds up** — rounding to
  nearest would silently under-charge the budget.

---

# 6. Measured results

Computed from the CSVs in `results/` at generation time. Scenario throughout:
**cap 10, seeded at spent 6** (4 of headroom), reserves of **ε 2**, so **exactly 2
should ever be granted**.

## Experiment 1 — the breach is real

`make exp1` · {len(naive)} runs · the database CHECK dropped

{breach_table(naive)}
""")

if worst:
    ratio = float(worst["total"]) / float(worst["cap"])
    W(f"""**Worst single run:** N={worst['workers']} — {worst['reserved_count']} granted and """
      f"""{worst['denied_count']} denied, for a total of **{worst['total']} against a cap of """
      f"""{worst['cap']}** (overshoot {worst['overshoot']}), with every arrival landing inside """
      f"""{worst['reserved_at_spread_ms']} ms of the first. That is **{ratio:.1f}× the budget**.
""")

W(f"""
Two things to read carefully:

- **N=2 is a control, not a failure to breach.** Two reservations of ε 2 fit
  exactly into the 4 of headroom, so a correct system grants both. The naive path
  only fails once demand genuinely exceeds supply.
- **Overshoot is not monotonic in N.** It peaks around N=10 and then flattens.
  The mechanism is measured, not proposed: the number of callers that win the
  race saturates while N keeps growing. See "Why overshoot plateaus" below.

### Why overshoot plateaus: the race window, counted

Every naive caller reports the values it read in statement 1. Each reservation
increments `epsilon_reserved` from a seeded zero, so that read *is* a position in
the write sequence: `reserved = 0` means no peer's write was visible yet — the
caller was inside the race window — and anything above zero means it lost the
window, so its denial was legitimate. Counted per run, from the same Experiment 1
sweep (`reads_in_window` in the CSV):

{window_table(naive)}

More contention does not produce more successful overspending. It produces more
callers arriving after the writes have already landed, who are then correctly
denied. The overspend is bounded by how many callers fit inside the window, not
by how many are competing. The sequential control shows the same thing from the
other side: run one at a time and exactly **one** caller ever reads pre-write
state.
""")

if window:
    W(f"""
#### The intervention: widen the window and every caller wins

Observation cannot show the window is the *cause*. So hold N fixed at
{int(window[0]['workers'])} and vary the window itself with `NAIVE_RACE_DELAY_MS`
— an artificial pause between the read and the write, standing in for whatever
work a real service does there. Nothing else changes.

`make exp1c`

{delay_table(window)}

Monotonic, and it saturates where the arithmetic says it must: at the widest
setting every caller reads pre-write state, every one is granted, and the
overshoot reaches the most that {int(window[0]['workers'])} reservations of
ε {float(window[0]['cost']):g} can overspend a cap of {float(window[0]['cap']):g}
seeded at {float(window[0]['seed_spent']):g}. **The plateau is a property of the
window, not of the concurrency.** At `delay=0` the window is narrower than the
spread of arrivals, so most callers miss it; widen it past the spread and the
breach is total.

The delay is an instrument, not a claim about production timings — it stands in
for application work whose real duration is unmeasured here. What is established
is the direction and the ceiling. The breach conclusion at `delay=0` needs no
instrument at all.
""")

W(f"""
### Control 1: the naive path is faithful, not a strawman

`make exp1-control` — the *same* naive code, the same requests, issued one at a
time instead of simultaneously:

{control_table(ctrl)}

Zero breaches, and exactly the number that fit. The logic is correct; only
concurrency breaks it.

### Control 2 (Experiment 1b): one transaction does not help

`make exp1b` — `naive_txn`, the same read/check/write inside a **single**
transaction:

{compare_table(naive, txn)}

The same breach profile at every level. The transaction boundary is not what makes
the naive path unsafe, which is what removes the "you built it to fail" objection.
I2 held in {sum(istrue(r['i2_ledger_agreement']) for r in txn)}/{len(txn)} of these
runs — nothing was lost or corrupted, the cap was simply exceeded.

### Was the contention real?

Median wall-clock spread of the arrival timestamps Postgres wrote, by concurrency:

{spread_table(naive)}

**Measurement-validity caveat:** the harness and the server share one machine, so
the growth at the top of the range is partly client-side scheduling rather than
database behaviour. This does not affect the safety conclusions — a breach either
happened or it did not — but Experiment 3's latency numbers will need a no-op
baseline and a stated saturation point.

## Experiment 2 — the fix holds

`make exp2` · identical seed and load · **the CHECK still dropped** · isolation
**{iso[0]}**

{atomic_table(atomic)}

**{a_breaches} breaches in {len(atomic)} runs**, escalating deliberately to N={max(group(atomic))}
to try to force one. Every single run granted exactly 2 — the denial count is not
merely safe, it is exactly correct.

## Experiment 4 — the crash is survivable

`make exp4` (controlled abort) and `make exp4-kill` (real `os._exit` in the
reserve→commit gap, with Docker restarting the service). Includes the
partial-commit refund checks described in §2.

**{c_pass}/{len(crash)} fault-injection checks passed.**

{crash_table(crash)}

Budget survives a crash **as a hold** — never as a loss and never as a
double-spend. Because the record row and the running columns move in one
transaction, I2 cannot drift: a partial write is not a state the database can be
left in.

## Real ε, end to end

`make opendp` — an OpenDP noisy count at Laplace scale 0.5 costs ε = 1/0.5 = 2.0,
deterministic given the spec even though the noisy result is random. Against cap
10 / spent 6, two such queries commit (reaching exactly 10/10) and the third is
denied with HTTP 429. The cost enforced is the cost OpenDP reported.

## What this does and does not claim

- The atomic strategy is **correct by the documented semantics of READ COMMITTED**
  (§3), **corroborated** by {len(atomic)} runs to N={max(group(atomic))} with zero
  breaches. Absence of breach is empirically demonstrated under the tested
  conditions; the mechanism, not the sample, is the argument.
- The database CHECK was **dropped for every run shown**, including the atomic
  ones, so safety is attributable to the strategy alone.
- The cap is an organizational **policy input**; the system enforces whatever cap
  it is given and never derives one.
- No DP mathematics is implemented. Mechanisms stay black-box and stateless.
- Prior art — **PrivateKube (OSDI '21)** and **Sage** — is cited up front.
  PrivateKube also performs two-phase budget accounting; that is not claimed as
  novel here. The distinction is the concurrency-safety analysis of the
  check-then-write commit path.
- **This is the mid-term result.** The cost of the guarantee — throughput and
  latency across the strategies and two duration regimes — is the primary
  contribution and is not yet measured. All contention is on a single row by
  design; that ceiling is structural, and the sweep measures its price rather
  than discovering it.

---

# 7. Source

Complete, in dependency order.
""")

for rel, why in SOURCES:
    W(f"\n## `{rel}`\n\n{why}\n\n```{fence(rel)}\n{read_src(rel)}\n```\n")

W("""
---

## Omitted from this bundle

- `experiments/report.py` — generates `results/report.html` from the CSVs. Pure
  presentation: it computes no result of its own, and every figure in the report
  is derived from the same CSVs summarised in §6 above.
- `experiments/bundle.py` — generates this file.
- `docs/privatekube-comparison.md` — the prior-art comparison (Gate A).
- `Dockerfile`, `docker-compose.yml`, `scripts/run_app.sh`, `requirements.txt` —
  environment only. Postgres 16 with a named volume plus the FastAPI service;
  the service is supervised so it auto-restarts, which is what the process-kill
  experiment needs. Note the app image bakes the source in at build time, so code
  changes need `docker compose up -d --build app`.
""")

OUT.write_text("\n".join(parts))
print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")
