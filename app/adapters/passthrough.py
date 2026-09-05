"""Passthrough adapter -- THIS IS A STUB, and is labelled as one everywhere.

Epsilon is whatever the caller passed in; no mechanism runs. It exists as the
documented fallback (plan section 10, Phase 1) so the enforcement logic can be
exercised independently of any DP library, and so Experiments 1/2/4 are not
blocked on library setup. The enforcement path is identical regardless of where
epsilon came from -- which is exactly why swapping in the real adapter behind
this same interface changes nothing about the result.

Every record it produces carries epsilon_source='passed_in'.
"""

from decimal import Decimal
from typing import Any

from app.adapters.base import OperationResult, register


class PassthroughAdapter:
    name = "passthrough"
    is_stub = True

    def available(self) -> tuple[bool, str]:
        return True, "stub: epsilon is supplied by the caller, no mechanism runs"

    def epsilon_for(self, spec: dict[str, Any]) -> Decimal:
        if "epsilon" not in spec:
            raise ValueError("passthrough adapter requires spec.epsilon")
        return Decimal(str(spec["epsilon"]))

    async def run_operation(self, spec: dict[str, Any]) -> OperationResult:
        return OperationResult(
            epsilon=self.epsilon_for(spec),
            source="passed_in",
            result=None,
            detail={"stub": True},
        )


register(PassthroughAdapter())
