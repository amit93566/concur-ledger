"""Synthetic duration adapter -- THIS IS A STUB, built for Experiment 3 only.

Experiment 3 prices the protocol, not the mechanism, so the operation has to
take a controlled, known time and nothing else. This adapter sleeps for
`spec.duration_ms` and charges `spec.epsilon`; no mechanism runs and no CPU is
used, so the cost sweep measures the ledger and the lock hold, not OpenDP.

The sleep is an `asyncio.sleep`: it yields the event loop, the way a real
operation awaiting a remote query engine would, and no lock is held during it.

Why the default epsilon is 0.001: the ledger columns are NUMERIC(12,6), so a
cap tops out near 10^6. A 900 000 cap never binds at 0.001 a call, which is the
unconstrained regime. The near-cap regime passes epsilon 1 against cap 10 with
`actual_epsilon` 0, so each operation holds budget while it runs and then gives
it all back (see `actual_epsilon`).

Every record it produces carries epsilon_source='synthetic'.
"""

import asyncio
from decimal import ROUND_CEILING, Decimal
from typing import Any

from app.adapters.base import OperationResult, register

# Ledger precision: NUMERIC(12,6). Round up, as the OpenDP adapter does, so a
# finer epsilon is never under-charged by the column's rounding.
_QUANTUM = Decimal("0.000001")
DEFAULT_EPSILON = "0.001"


class SyntheticAdapter:
    name = "synthetic"
    is_stub = True

    def available(self) -> tuple[bool, str]:
        return True, "stub: sleeps spec.duration_ms, charges spec.epsilon (Experiment 3)"

    def epsilon_for(self, spec: dict[str, Any]) -> Decimal:
        # Validated here too, because epsilon_for is what run_job calls up
        # front: a bad spec is then a 400, not a failed operation and a release.
        self.duration_s(spec)
        if Decimal(str(spec.get("actual_epsilon", 0))) < 0:
            raise ValueError("synthetic adapter requires spec.actual_epsilon >= 0")
        eps = Decimal(str(spec.get("epsilon", DEFAULT_EPSILON)))
        if eps <= 0:
            raise ValueError("synthetic adapter requires spec.epsilon > 0")
        return eps.quantize(_QUANTUM, rounding=ROUND_CEILING)

    def duration_s(self, spec: dict[str, Any]) -> float:
        ms = float(spec.get("duration_ms", 0))
        if ms < 0:
            raise ValueError("synthetic adapter requires spec.duration_ms >= 0")
        return ms / 1000

    def actual_epsilon(self, spec: dict[str, Any]) -> Decimal:
        """What the operation turns out to cost, committed after it runs.

        Defaults to the reserved epsilon. The near-cap regime sets it to 0: the
        hold is taken and returned, the budget never depletes, and the only
        source of denials is headroom held by in-flight operations.
        """
        if "actual_epsilon" not in spec:
            return self.epsilon_for(spec)
        actual = Decimal(str(spec["actual_epsilon"]))
        if actual < 0:
            raise ValueError("synthetic adapter requires spec.actual_epsilon >= 0")
        return actual.quantize(_QUANTUM, rounding=ROUND_CEILING)

    async def run_operation(self, spec: dict[str, Any]) -> OperationResult:
        duration = self.duration_s(spec)
        await asyncio.sleep(duration)
        return OperationResult(
            epsilon=self.actual_epsilon(spec),
            source="synthetic",
            result=None,
            detail={"stub": True, "duration_ms": duration * 1000},
        )


register(SyntheticAdapter())
