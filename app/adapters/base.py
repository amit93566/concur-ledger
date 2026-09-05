"""Mechanism adapter boundary (plan section 7).

The system never computes privacy. An adapter is handed a spec, returns an
epsilon, and remembers nothing -- all budget state lives in the Postgres ledger.

Two methods, and the split matters:

  * `epsilon_for(spec)` -- the *cost*, which is deterministic given the spec.
    This is what the cap check reserves against, BEFORE the operation runs. The
    noisy *result* is random; its price is not.
  * `run_operation(spec)` -- actually runs the mechanism and returns the same
    epsilon alongside the result. This is called between reserve and commit,
    with NO database transaction held.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class AdapterUnavailable(Exception):
    """The adapter's library is not installed or failed to initialise."""


@dataclass
class OperationResult:
    epsilon: Decimal
    source: str
    result: Any = None
    detail: dict[str, Any] = field(default_factory=dict)


class MechanismAdapter(Protocol):
    name: str
    is_stub: bool

    def available(self) -> tuple[bool, str]: ...

    def epsilon_for(self, spec: dict[str, Any]) -> Decimal: ...

    async def run_operation(self, spec: dict[str, Any]) -> OperationResult: ...


registry: dict[str, MechanismAdapter] = {}


def register(adapter: MechanismAdapter) -> MechanismAdapter:
    registry[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> MechanismAdapter:
    if name not in registry:
        raise ValueError(
            f"unknown adapter '{name}'; available: {', '.join(sorted(registry))}"
        )
    return registry[name]
