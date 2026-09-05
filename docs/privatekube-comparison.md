# Gate A — How this differs from PrivateKube

**Status:** closed for the mid-term demo. Read from source at
`github.com/columbia/PrivateKube` (branch `main`), paper: Luo et al.,
*Privacy Budget Scheduling*, OSDI '21.

**Files actually read** (so the scope of these claims is auditable):

| File | What it establishes |
|---|---|
| `system/privacyresource/pkg/apis/columbia.github.com/v1/types.go` | the budget state model |
| `system/privacyresource/pkg/framework/block.go` | how a block is snapshotted and mutated |
| `system/dpfscheduler/pkg/scheduler/updater/updater.go` | how a check-then-write is committed |
| `system/privacycontrollers/pkg/blockclaimsync/sync_controller.go` | crash / leak recovery |

Not read in depth: the DPF scheduling algorithm itself (`pkg/scheduler/algorithm/`),
the Python client, the evaluation harness. No claim below depends on them.

---

## The short answer, out loud

> PrivateKube already does two-phase budget accounting — I am not claiming that
> idea. What it does *not* do is measure the concurrency-control discipline
> underneath it. Its safety comes from an unbounded optimistic-retry loop against
> the Kubernetes API server, and its crash recovery comes from lock expiry plus a
> periodic garbage collector. Mine puts the check inside a single conditional SQL
> statement and settles the record and the running totals in one transaction. My
> contribution is not a better allocator — allocation is explicitly their
> contribution and out of my scope — it is the *price* of each safe
> implementation discipline under concurrent load, which their evaluation does
> not report.

---

## 1. Two-phase accounting: they have it too

This must be stated first, because claiming otherwise would be false.
`PrivateDataBlockStatus` (types.go:113–132) carries:

```go
PendingBudget      PrivacyBudget                 // not yet released
AvailableBudget    PrivacyBudget                 // allocatable
AcquiredBudgetMap  map[string]PrivacyBudget      // "this is the source of the truth"
ReservedBudgetMap  map[string]PrivacyBudget
CommittedBudgetMap map[string]PrivacyBudget
LockedBudgetMap    map[string]LockedBudget
```

So the lifecycle is roughly *reserved → acquired → committed*, with a separate
*locked* state, mirrored on the claim side (`AcquiredBudgets`,
`ReservedBudgets`, `CommittedBudgets`, types.go:224–231).

My protocol is the simpler *reserved → committed | released*. **The novelty
claim is not the phase split.** It is where the cap check executes, what
guarantees it, and what it costs.

## 2. How their check-then-write commits: optimistic CAS, unbounded retry

`ResourceUpdater.ApplyOperationToDataBlock` (updater.go:49–83) is the whole
concurrency-control story:

```go
for true {
    block := blockHandler.Snapshot()          // DeepCopy of a local informer cache
    err := operation(block)                   // mutate in memory (the cap check lives here)
    newBlock, err := updater.privacyResourceClient...UpdateStatus(ctx, block, ...)
    if err == nil { ...; return nil }
    if apiStatus.Status().Code != ConflictErrCode { return err }   // 409
    // otherwise: loop forever, re-snapshot, re-apply
}
```

Answering the three questions the plan poses:

- **Is it one transaction?** No — there is no transaction. It is
  read-from-cache → mutate-in-memory → whole-object write.
- **Does it compare-and-swap on a version?** **Yes.** `UpdateStatus` carries the
  object's `resourceVersion`; the API server rejects a stale write with HTTP 409,
  and etcd provides the underlying atomicity. So it is *safe against lost
  updates* — this is not a naive read-check-write, and I should not imply it is.
- **What is the cost?** The retry loop is `for true` with **no attempt cap and no
  backoff**. Under contention on one hot block, writers spin. The OSDI evaluation
  reports scheduling quality (models trained under a fixed global guarantee); it
  does not report this retry rate, nor throughput/latency as a function of
  concurrent claimants on a single budget.

**The check runs against a possibly-stale snapshot.** Correctness rests entirely
on the CAS rejecting the write afterwards. That is architecturally my
`serializable` strategy (Phase 2: retry on SQLSTATE 40001, *counting the
retries*) — not my `atomic` strategy.

## 3. Where the designs actually diverge

My `atomic` strategy pushes the predicate into the write itself:

```sql
UPDATE datasets SET epsilon_reserved = epsilon_reserved + :cost
 WHERE id = :id AND epsilon_spent + epsilon_reserved + :cost <= epsilon_cap;
-- rowcount 1 -> reserved ; rowcount 0 -> denied
```

There is no retry, because there is nothing to retry: the predicate is
re-evaluated by the database against each writer's own serialized version of the
row. A denial is a *result*, not a conflict.

**PrivateKube's data model cannot express this.** The Kubernetes API server
offers whole-object compare-and-swap on `resourceVersion`; it has no conditional
predicate on a field. Optimistic retry is not a choice they made carelessly — it
is the only primitive their substrate provides. Choosing a transactional ledger
instead is what puts the other three strategies on the table at all, and that is
the design space my Experiment 3 prices.

## 4. Crash behaviour — the sharpest difference

Block status and claim status are **separate API objects with separate
`resourceVersion`s**, written by separate calls (`ApplyOperationToDataBlock`,
`ApplyOperationToClaim`). Nothing spans them atomically. A controller that dies
between the two leaves the block and the claim disagreeing about the same budget.

Their answer is **time-based reclamation**: `LockedBudgetMap` entries carry an
expiry, and `SyncController` runs `BatchCleanExpiredLocks` on a `PeriodSecond`
timer (sync_controller.go:59–91), deleting locks whose request has completed and
whose lock has expired. Recovery is eventual, and its window is a tunable
constant.

Mine:

- The `spend_records` row and the `datasets` running columns move in **one
  Postgres transaction** (`app/enforcement/settle.py`). Invariant **I2** — the
  running columns equal the sum over the record rows — therefore cannot drift;
  a partial write is not a state the database can be left in.
- A hold survives a crash as a hold. It is released **explicitly**, not by
  expiry. Nothing is reclaimed on a timer, so there is no window in which a
  crashed job's budget is both held and reclaimable.
- **Experiment 4 verifies this rather than asserting it** — 30 controlled aborts
  and real `os._exit` kills inside the reserve→commit gap, with I1–I4 checked
  after restart. All pass.

The honest framing: lock expiry is a *legitimate* design, and it is the right
one for a controller whose substrate has no cross-object transaction. It trades
a stronger consistency guarantee for a weaker one plus a reaper. Having a
transactional ledger means I do not need the reaper.

## 5. Idempotency

PrivateKube keys budget by claim id inside maps, so re-applying an operation for
the same claim is naturally overwriting rather than additive. I make it explicit
and enforce it in the schema: `UNIQUE (dataset_id, idempotency_key)` plus the
`AND status = 'reserved'` guard on settle, which is what makes a retried commit
return the prior outcome instead of charging twice (**I4**) and makes a settled
reservation immutable (**I3**). Both are verified in `experiments/crash_test.py`.

## 6. What is deliberately *not* claimed

- **Not a better scheduler.** DPF (Dominant Private Block Fairness) is
  PrivateKube's contribution. Budget *allocation and scheduling* is explicitly
  out of scope here (plan §11) and I do not revisit it.
- **Not a novel two-phase protocol.** See §1.
- **Not a formal proof.** Experiments 1, 2 and 4 show absence of breach *under
  tested conditions*.
- **Not a like-for-like performance comparison.** I have not benchmarked
  PrivateKube. The comparison here is of *mechanism*, read from source. Claiming
  measured superiority over their system would require running it, which I have
  not done.

## 7. The one-sentence viva answer

PrivateKube secures a shared privacy budget with whole-object optimistic
concurrency and an unbounded retry loop, because the Kubernetes API server gives
it no conditional write, and it repairs post-crash inconsistency with expiring
locks and a periodic sweeper; I move the cap check into a single conditional
statement inside a transactional ledger, so denials replace retries and
cross-object drift becomes unrepresentable — and then I measure what each of
those disciplines costs, which is the number neither system currently reports.

---

## Sources

- [columbia/PrivateKube](https://github.com/columbia/PrivateKube)
- [Privacy Budget Scheduling (OSDI '21), USENIX](https://www.usenix.org/conference/osdi21/presentation/luo)
- [arXiv:2106.15335](https://arxiv.org/abs/2106.15335)
