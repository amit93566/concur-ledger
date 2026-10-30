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
  crash_in_reserve_txn  atomic.py, for_update.py: after the record INSERT,
                        before COMMIT. Expect: no record, no hold.
  crash_in_commit_txn   settle.py: between the record UPDATE and the dataset
                        UPDATE; locked_1phase.py: after the charge, before
                        COMMIT. Expect: the record unchanged, nothing charged.
  crash_after_commit    main.py run_job: after commit returned, before the
                        response. Expect: committed exactly once; a retried
                        commit returns already_settled.
  abort_before_commit   (an exception, via abort_point) locked_1phase.py:
                        after the operation, before the charge. Expect: the
                        transaction rolls back -- no record, no charge.
"""

import os
from contextvars import ContextVar

POINTS = ("crash_in_reserve_txn", "crash_in_commit_txn", "crash_after_commit")

active_fault: ContextVar[str] = ContextVar("active_fault", default="none")


def point(name: str) -> None:
    if active_fault.get() == name:
        os._exit(3)


def abort_point(name: str) -> None:
    """A failure the application sees (an exception), not a dead process."""
    if active_fault.get() == name:
        raise RuntimeError(f"injected fault: {name}")


# Which faults reach a real point on which path. A fault requested on a path
# that never calls its point would "pass" while testing nothing, so run_job
# refuses those combinations instead.
SUPPORTED = {
    "crash_in_reserve_txn": {"atomic", "for_update"},
    "crash_before_commit": "two_phase",
    "abort_before_commit": "any",
    "crash_in_commit_txn": "any",
    "crash_after_commit": "any",
}


def supported(fault: str, strategy: str, two_phase: bool) -> bool:
    rule = SUPPORTED.get(fault)
    if rule == "any":
        return True
    if rule == "two_phase":
        return two_phase
    return rule is not None and strategy in rule
