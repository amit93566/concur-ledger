"""OpenDP query adapter (plan section 7a) -- the primary, real epsilon source.

Runs a noisy count or bounded sum and returns the epsilon OpenDP itself reports
via `measurement.map(d_in)`. No privacy mathematics is implemented here; the
library is a black box that is handed a spec and asked what it costs.

Why a real adapter and a reproducible near-cap scenario are not in conflict:
epsilon is DETERMINISTIC given the query spec. The noisy *result* is random,
its *cost* is not. For Laplace, eps = sensitivity / scale, so the spec picks the
price:

    count, scale 0.5   -> eps = 2.0     (the cap-10 / spent-6 demo scenario)
    count, scale 10    -> eps = 0.1
    sum (0,100), 50    -> eps = 2.0000000186...

That last one matters. OpenDP reports a slightly *inflated* epsilon because its
arithmetic rounds conservatively. The ledger stores NUMERIC(12,6), so quantising
must round UP -- rounding to nearest would silently under-charge the budget by a
hair. Conservative in the safe direction is the only acceptable rounding here.
"""

import asyncio
import threading
from decimal import ROUND_CEILING, Decimal
from typing import Any

from app.adapters.base import AdapterUnavailable, OperationResult, register

# Ledger precision: NUMERIC(12,6).
_QUANTUM = Decimal("0.000001")

_lock = threading.Lock()
_cache: dict[tuple, Any] = {}
_import_error: str | None = None
_dp = None


def _opendp():
    """Import lazily so the service still starts if OpenDP is not installed."""
    global _dp, _import_error
    if _dp is not None:
        return _dp
    if _import_error is not None:
        raise AdapterUnavailable(_import_error)
    try:
        import opendp.prelude as dp

        # "contrib" enables constructors that have not completed the vetting
        # process. Standard for research use; recorded here so it is disclosed.
        dp.enable_features("contrib")
        _dp = dp
        return _dp
    except Exception as exc:  # pragma: no cover - environment dependent
        _import_error = f"opendp import failed: {exc}"
        raise AdapterUnavailable(_import_error) from exc


def _spec_key(spec: dict[str, Any]) -> tuple:
    kind = str(spec.get("kind", "count"))
    scale = float(spec.get("scale", 1.0))
    bounds = spec.get("bounds")
    return (kind, scale, tuple(bounds) if bounds else None)


def _build(spec: dict[str, Any]):
    """Construct the measurement for a spec. Cached: same spec, same cost."""
    dp = _opendp()
    key = _spec_key(spec)
    with _lock:
        if key in _cache:
            return _cache[key]

    kind, scale, bounds = key
    if scale <= 0:
        raise ValueError("spec.scale must be > 0")

    if kind == "count":
        space = dp.vector_domain(dp.atom_domain(T=int)), dp.symmetric_distance()
        meas = space >> dp.t.then_count() >> dp.m.then_laplace(scale=scale)
    elif kind == "sum":
        lo, hi = bounds if bounds else (0.0, 100.0)
        space = (
            dp.vector_domain(dp.atom_domain(bounds=(float(lo), float(hi)))),
            dp.symmetric_distance(),
        )
        meas = space >> dp.t.then_sum() >> dp.m.then_laplace(scale=scale)
    else:
        raise ValueError(f"unsupported spec.kind '{kind}'; use 'count' or 'sum'")

    with _lock:
        _cache[key] = meas
    return meas


def _synthetic_data(spec: dict[str, Any], kind: str) -> list:
    """Stand-in data. The dataset contents do not affect the cost, only the
    noisy answer, so a deterministic synthetic vector is sufficient and keeps
    the experiment self-contained."""
    n = int(spec.get("n_rows", 1000))
    if kind == "count":
        return list(range(n))
    lo, hi = spec.get("bounds", (0.0, 100.0))
    span = float(hi) - float(lo)
    return [float(lo) + (i % 97) / 97.0 * span for i in range(n)]


class OpenDPAdapter:
    name = "opendp"
    is_stub = False

    def available(self) -> tuple[bool, str]:
        try:
            _opendp()
        except AdapterUnavailable as exc:
            return False, str(exc)
        return True, "opendp available (features: contrib)"

    def epsilon_for(self, spec: dict[str, Any]) -> Decimal:
        """The cost, known before the operation runs -- this is what is reserved."""
        meas = _build(spec)
        # d_in = 1: one individual may add or remove one record (symmetric
        # distance). This is the privacy unit, and it is an input, not something
        # the system derives.
        epsilon = meas.map(int(spec.get("contributions", 1)))
        # Round UP to ledger precision: never charge less than OpenDP reported.
        return Decimal(str(epsilon)).quantize(_QUANTUM, rounding=ROUND_CEILING)

    async def run_operation(self, spec: dict[str, Any]) -> OperationResult:
        kind = str(spec.get("kind", "count"))
        meas = _build(spec)
        data = _synthetic_data(spec, kind)

        # Runs off the event loop; no database connection is held here.
        value = await asyncio.to_thread(meas, data)

        return OperationResult(
            epsilon=self.epsilon_for(spec),
            source="opendp",
            result=value,
            detail={
                "kind": kind,
                "scale": float(spec.get("scale", 1.0)),
                "n_rows": len(data),
                "mechanism": "laplace",
                "raw_epsilon": meas.map(int(spec.get("contributions", 1))),
            },
        )


register(OpenDPAdapter())
