"""Where epsilon comes from -- the table behind the epsilon-selection section.

The ledger never chooses epsilon; it enforces a sum. This script makes the
choice visible by asking the real OpenDP adapter what each query spec costs,
and what that cost buys in accuracy:

  * raw_epsilon     -- what OpenDP reports (`measurement.map(d_in)`)
  * ledger_epsilon  -- what the ledger charges: rounded UP to NUMERIC(12,6)
  * rounded_up_by   -- the difference; non-zero only where OpenDP's own
                       conservative float arithmetic inflates the cost
  * noise_std       -- the theoretical noise standard deviation. A count is an
                       integer query, so OpenDP adds DISCRETE Laplace noise:
                       std = sqrt(2 e^(-1/b)) / (1 - e^(-1/b)), which is below
                       the continuous sqrt(2) b at small b (0.602 vs 0.707 at
                       b = 0.5). The float sum gets continuous Laplace, sqrt(2) b.
  * empirical_std   -- the same, measured from --samples real releases, so the
                       accuracy column is evidence and not only a formula
  * uses_of_cap     -- how many such queries a cap fits (basic composition)

The sensitivity column is the analyst-facing link: for Laplace,
epsilon = sensitivity / scale. A count has sensitivity 1 per contribution; a
sum clamped to [lo, hi] has max(|lo|, |hi|) per contribution. Raising
`contributions` (the privacy unit) multiplies the cost.

Usage:
    python experiments/eps_table.py [--samples 2000] [--cap 10]
"""

import argparse
import csv
import math
import statistics
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.opendp_adapter import OpenDPAdapter, _build, _synthetic_data  # noqa: E402

SPECS = [
    {"kind": "count", "scale": 10},
    {"kind": "count", "scale": 2},
    {"kind": "count", "scale": 1},
    {"kind": "count", "scale": 0.5},  # Experiments 1, 2, 4: eps 2.0
    {"kind": "count", "scale": 5},  # exp-eps-scale: eps 0.2 against cap 1
    {"kind": "count", "scale": 0.5, "contributions": 2},  # privacy unit doubled
    {"kind": "sum", "scale": 50, "bounds": [0, 100]},  # exp-eps-round: 2.000001
]

FIELDS = [
    "kind", "scale", "bounds", "contributions", "sensitivity", "raw_epsilon",
    "ledger_epsilon", "rounded_up_by", "noise_std", "empirical_std", "samples",
    "cap", "uses_of_cap",
]


def noise_std(spec) -> float:
    b = float(spec["scale"])
    if spec["kind"] == "count":
        q = math.exp(-1 / b)
        return math.sqrt(2 * q) / (1 - q)
    return math.sqrt(2) * b


def sensitivity(spec) -> float:
    k = int(spec.get("contributions", 1))
    if spec["kind"] == "count":
        return 1.0 * k
    lo, hi = spec.get("bounds", (0, 100))
    return max(abs(lo), abs(hi)) * k


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--samples", type=int, default=2000)
    p.add_argument("--cap", type=Decimal, default=Decimal(10))
    p.add_argument("--out", default="results/eps_table.csv")
    args = p.parse_args()

    adapter = OpenDPAdapter()
    ok, detail = adapter.available()
    if not ok:
        sys.exit(f"OpenDP unavailable: {detail}")

    rows = []
    for spec in SPECS:
        meas = _build(spec)
        raw = meas.map(int(spec.get("contributions", 1)))
        ledger = adapter.epsilon_for(spec)
        data = _synthetic_data(spec, spec["kind"])
        # The true answer is fixed, so the spread of releases is the noise.
        releases = [float(meas(data)) for _ in range(args.samples)]
        rows.append({
            "kind": spec["kind"],
            "scale": spec["scale"],
            "bounds": "-".join(map(str, spec["bounds"])) if "bounds" in spec else "",
            "contributions": spec.get("contributions", 1),
            "sensitivity": sensitivity(spec),
            "raw_epsilon": repr(raw),
            "ledger_epsilon": str(ledger),
            "rounded_up_by": f"{ledger - Decimal(repr(raw)):.10f}",
            "noise_std": round(noise_std(spec), 3),
            "empirical_std": round(statistics.stdev(releases), 3),
            "samples": args.samples,
            "cap": str(args.cap),
            "uses_of_cap": int(args.cap // ledger),
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"{'spec':34s}{'sens':>6}{'raw eps':>22}{'ledger':>11}{'std':>8}{'emp std':>9}{'uses/cap':>9}")
    for r in rows:
        spec = f"{r['kind']} scale={r['scale']}" + (f" [{r['bounds']}]" if r["bounds"] else "") + (
            f" x{r['contributions']}" if r["contributions"] != 1 else "")
        print(f"{spec:34s}{r['sensitivity']:>6g}{r['raw_epsilon']:>22}{r['ledger_epsilon']:>11}"
              f"{r['noise_std']:>8}{r['empirical_std']:>9}{r['uses_of_cap']:>9}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
