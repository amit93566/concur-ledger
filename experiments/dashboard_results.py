"""Summarise the Experiment 3 CSVs for the dashboard's Results page.

The dashboard is a static build and cannot read results/, so this writes the
medians and ranges it shows to dashboard/public/results.json.

    python experiments/dashboard_results.py
"""

import csv
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUT = ROOT / "dashboard" / "public" / "results.json"
STRATEGIES = ["atomic", "for_update", "serializable", "naive"]


def read(name):
    path = RESULTS / name
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def panel(rows, title, regime, duration, source):
    """One chart: for each strategy, per N, the median and range over reps."""
    series = {}
    for s in STRATEGIES:
        points = []
        for n in sorted({int(r["n"]) for r in rows}):
            cell = [r for r in rows if r["strategy"] == s and int(r["n"]) == n
                    and int(r["duration_ms"]) == duration]
            if cell:
                done = [float(r["completed_per_s"]) for r in cell]
                points.append({
                    "n": n, "median": round(st.median(done), 2), "min": min(done),
                    "max": max(done), "reps": len(done),
                    "gave_up_per_s": round(st.median(int(r["gave_up"]) / float(r["measure_s"]) for r in cell), 2),
                    "denial_rate": round(st.median(float(r["denial_rate"] or 0) for r in cell), 3),
                    "p99_ms": round(st.median(float(r["lat_p99_ms"] or 0) for r in cell), 1),
                })
        if points:
            series[s] = points
    return {"title": title, "regime": regime, "duration_ms": duration, "source": source, "series": series}


def calibration():
    rows = read("calibration_dpsgd.csv")
    if not rows:
        return None
    last = max(int(r["epoch"]) for r in rows)
    final = [r for r in rows if int(r["epoch"]) == last]
    return {
        "epochs": last, "reps": len(final),
        "train_s_median": st.median(float(r["cum_s"]) for r in final),
        "train_s_min": min(float(r["cum_s"]) for r in final),
        "train_s_max": max(float(r["cum_s"]) for r in final),
        "epoch_s_median": st.median(float(r["train_s"]) for r in rows),
        "epsilon": float(final[0]["epsilon"]), "delta": float(final[0]["delta"]),
        "accuracy_median": st.median(float(r["test_accuracy"]) for r in final),
        "opacus": final[0]["opacus"],
    }


def main():
    main_sweep = read("exp3_cost_sweep.csv")
    near = read("exp3_nearcap.csv")
    cal = read("exp3_calibrated.csv")
    cal_near = read("exp3_calibrated_nearcap.csv")
    panels = [
        panel(main_sweep, "10 ms operation", "unconstrained", 10, "final sweep"),
        panel(main_sweep, "1 s operation", "unconstrained", 1000, "final sweep"),
        panel(cal, "14 s operation: one real DP-SGD epoch", "unconstrained", 14000, "calibrated sweep, audit log on"),
        panel(near, "1 s operation, near the cap", "near_cap", 1000, "final sweep"),
        panel(cal_near, "14 s operation, near the cap", "near_cap", 14000, "calibrated sweep, audit log on"),
    ]
    panels = [p for p in panels if p["series"]]
    # results/ is gitignored, so a fresh clone has no CSVs but does have this file.
    if not panels:
        print(f"no Experiment 3 CSVs in results/, keeping {OUT.relative_to(ROOT)}")
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"calibration": calibration(), "panels": panels}, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(panels)} charts")


if __name__ == "__main__":
    main()
