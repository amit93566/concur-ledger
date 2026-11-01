"""Named crash points for Experiment 4 -- test-only, admin-gated.

The mid-term crash test killed the process in one place: the gap between
reserve and commit, where no transaction is open. These points reach the places
that gap does not: INSIDE the reserve and commit transactions, and after commit
but before the client hears about it. Each one is a hard `os._exit` -- no
exception handler, no `finally`, no rollback issued by the application -- so
whatever survives is what Postgres itself guarantees.

`run_job` sets `active_fault` from the request body (admin-only, as before).
It is a ContextVar, so it belongs to that one request: concurrent requests on
the same event loop never see it, which is what lets `kill-under-load` crash
one request while others are in flight.

Points (where `point(name)` is called):
  crash_in_reserve_txn  atomic.py, for_update.py, serializable.py: after the
                        record INSERT, before COMMIT. Expect: no record, no hold.
  crash_in_commit_txn   settle.py: between the record UPDATE and the dataset
                        UPDATE. Expect: the record unchanged, nothing charged.
  crash_after_commit    main.py run_job: after commit returned, before the
                        response. Expect: committed exactly once; a retried
                        commit returns already_settled.
"""

import os
from contextvars import ContextVar

POINTS = ("crash_in_reserve_txn", "crash_in_commit_txn", "crash_after_commit")

active_fault: ContextVar[str] = ContextVar("active_fault", default="none")


def point(name: str) -> None:
    if active_fault.get() == name:
        os._exit(3)


# Which faults reach a real point on which path. A fault requested on a path
# that never calls its point would "pass" while testing nothing, so run_job
# refuses those combinations instead.
SUPPORTED = {
    "crash_in_reserve_txn": {"atomic", "for_update", "serializable"},
    "crash_before_commit": "any",
    "abort_before_commit": "any",
    "crash_in_commit_txn": "any",
    "crash_after_commit": "any",
}


def supported(fault: str, strategy: str) -> bool:
    rule = SUPPORTED.get(fault)
    if rule == "any":
        return True
    return rule is not None and strategy in rule
