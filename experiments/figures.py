"""CSV -> PNG figures for the slide deck and the dissertation.

Separate from report.py on purpose: report.py renders the argued HTML page with
hand-rolled inline SVG, which stays dependency-free. This module is the
matplotlib path for figures that have to leave the repo as images, and it is
where Experiment 3's throughput/latency panels will go.

Every figure is derived from results/*.csv -- nothing is hand-entered, so a
figure cannot drift from the evidence the way a pasted number can.

Palette (validated, do not re-pick by eye):
  within cap  #2a78d6   over cap  #d03b3b
Red/green was rejected: deutan Delta E 4.1, far below the separation floor.
Blue vs red measures 23.8 under protan and 31.6 for normal vision, and here
colour is redundant anyway -- x-position and the labelled divider carry the
meaning on their own.
"""

import csv
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SOFT = "#52514e"
GRID = "#e4e3df"
WITHIN_CAP = "#2a78d6"
OVER_CAP = "#d03b3b"


def read(name):
    path = RESULTS / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def granted_histogram(rows):
    """Share of runs keyed by reservations granted, plus how many were legal.

    `expected_max_success` is the harness's own headroom // cost, so the legal
    count comes from the run rather than from a constant restated here.
    """
    counts = Counter(int(r["reserved_count"]) for r in rows)
    total = sum(counts.values())
    legal = {int(r["expected_max_success"]) for r in rows}
    if len(legal) != 1:
        raise ValueError(f"mixed headroom in one figure: expected_max_success={legal}")
    return {k: 100.0 * v / total for k, v in counts.items()}, total, legal.pop()


def granted_distribution(out="fig_granted_distribution.png"):
    """Reservations granted per run, per strategy.

    The headline figure for Experiment 2: the naive variants scatter across
    outcomes, atomic is a single spike on the only legal one. Shares rather than
    raw counts, because the strategies were not run the same number of times.
    """
    panels = [
        ("naive", "exp1_naive_vs_n", "read, check, write \u2014 two transactions"),
        ("naive_txn", "exp1b_naive_txn_vs_n", "the same, inside one transaction"),
        ("atomic", "exp2_atomic_vs_n", "the check is the WHERE clause"),
    ]
    data = []
    for label, csv_name, blurb in panels:
        rows = read(csv_name)
        if not rows:
            print(f"!! missing results/{csv_name}.csv -- skipping {label}")
            continue
        hist, total, legal = granted_histogram(rows)
        data.append((label, blurb, hist, total, legal))
    if not data:
        return None

    legals = {d[4] for d in data}
    if len(legals) != 1:
        raise ValueError(f"strategies ran against different headroom: {legals}")
    legal = legals.pop()
    xs = sorted({k for _, _, h, _, _ in data for k in h})
    xmin, xmax = min(xs), max(xs)

    fig, axes = plt.subplots(
        len(data), 1, figsize=(10, 6.6), sharex=True, sharey=True,
        facecolor=SURFACE, gridspec_kw={"hspace": 0.38},
    )
    if len(data) == 1:
        axes = [axes]

    for ax, (label, blurb, hist, total, _) in zip(axes, data):
        ax.set_facecolor(SURFACE)
        for k in range(xmin, xmax + 1):
            pct = hist.get(k, 0.0)
            if pct == 0:
                continue
            # Colour is redundant with position: anything past `legal` is a breach.
            ax.bar(
                k, pct, width=0.62,
                color=WITHIN_CAP if k <= legal else OVER_CAP,
                zorder=3,
            )
            ax.text(
                k, pct + 3.5, f"{pct:.0f}%", ha="center", va="bottom",
                fontsize=9, color=INK_SOFT, zorder=4,
            )
        # The divider does the explaining, so the colours never carry it alone.
        ax.axvline(legal + 0.5, color=INK_SOFT, lw=1.1, ls=(0, (4, 3)), zorder=2)
        ax.set_title(
            f"{label}  ·  {total} runs", loc="left", fontsize=11.5,
            color=INK, pad=7, fontweight="bold",
        )
        # Right-aligned so the gap between name and gloss reads as a column,
        # not as stray padding.
        ax.text(
            1.0, 1.04, blurb, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=10, color=INK_SOFT,
        )
        ax.set_ylim(0, 118)
        ax.set_yticks([0, 50, 100])
        ax.set_yticklabels(["0", "50", "100%"], fontsize=9, color=INK_SOFT)
        ax.grid(axis="y", color=GRID, lw=0.9, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(length=0)

    axes[0].text(
        legal + 0.62, 92, "everything right of this line breached the cap",
        fontsize=9, color=OVER_CAP, va="center", ha="left",
    )
    ax = axes[-1]
    ax.set_xticks(range(xmin, xmax + 1))
    ax.set_xticklabels([str(k) for k in range(xmin, xmax + 1)], fontsize=9.5, color=INK_SOFT)
    ax.set_xlabel("reservations granted in the run", fontsize=10, color=INK_SOFT, labelpad=8)

    fig.suptitle(
        "Granted per run: the naive paths scatter, atomic does not",
        x=0.062, y=0.985, ha="left", fontsize=14.5, color=INK, fontweight="bold",
    )
    fig.text(
        0.062, 0.935,
        f"cap 10, seeded spent 6, \u03b5 2.0 per job \u2014 exactly {legal} fit. "
        "Share of that strategy's runs ending at each outcome.",
        ha="left", fontsize=10, color=INK_SOFT,
    )
    fig.subplots_adjust(top=0.845, bottom=0.105, left=0.062, right=0.985)

    path = RESULTS / out
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return path


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.9, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0, labelsize=10, colors=INK_SOFT)


def _titles(fig, title, subtitle):
    # Offsets in points, not figure fractions, so the title/subtitle gap is the
    # same on every figure whatever its height.
    h = fig.get_figheight() * 72
    fig.suptitle(title, x=0.07, y=1 - 14 / h, ha="left", va="top", fontsize=15,
                 color=INK, fontweight="bold")
    fig.text(0.07, 1 - 44 / h, subtitle, ha="left", va="top", fontsize=10.5,
             color=INK_SOFT)


def _by_workers(rows):
    g = {}
    for r in rows:
        g.setdefault(int(r["workers"]), []).append(r)
    return dict(sorted(g.items()))


def exp1_breach_rate(out="fig_exp1_breach_rate.png"):
    """Experiment 1: share of naive runs that breached the cap, by N.

    N is plotted as evenly spaced categories, not on a linear axis -- the
    levels are 2, 3, 5, 10, 20, 50 and a linear axis would crush the first four.
    """
    rows = read("exp1_naive_vs_n")
    if not rows:
        print("!! missing results/exp1_naive_vs_n.csv -- skipping exp1")
        return None
    g = _by_workers(rows)
    ns = list(g)
    rate = [100.0 * sum(r["breach"] == "True" for r in g[n]) / len(g[n]) for n in ns]
    xs = range(len(ns))

    fig, ax = plt.subplots(figsize=(10, 5.6), facecolor=SURFACE)
    _style(ax)
    ax.plot(xs, rate, color=OVER_CAP, lw=2.2, zorder=3)
    ax.scatter(xs, rate, s=70, color=OVER_CAP, edgecolor=SURFACE, lw=2, zorder=4)
    for x, v in zip(xs, rate):
        # A 0% label sits beside its point; above it, the rising line runs through it.
        if v == 0:
            ax.text(x + 0.12, v, "0%", ha="left", va="center", fontsize=10, color=INK_SOFT)
        else:
            ax.text(x, v + 4, f"{v:.0f}%", ha="center", fontsize=10, color=INK_SOFT)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([str(n) for n in ns])
    ax.set_ylim(-4, 114)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0", "25", "50", "75", "100%"])
    ax.set_xlabel("concurrent jobs (N)", fontsize=10.5, color=INK_SOFT, labelpad=8)
    ax.set_ylabel("runs that breached the cap", fontsize=10.5, color=INK_SOFT)
    legal = int(rows[0]["expected_max_success"])
    _titles(
        fig,
        "Experiment 1 \u00b7 the naive path breaches the cap",
        f"cap 10, spent 6, \u03b5 2 per job \u2014 {legal} fit. "
        f"{len(g[ns[0]])} runs per level. N = {ns[0]} is the control: demand equals supply.",
    )
    fig.subplots_adjust(top=0.84, bottom=0.12, left=0.09, right=0.98)
    path = RESULTS / out
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return path


def exp2_granted(out="fig_exp2_granted.png"):
    """Experiment 2: mean reservations granted, naive vs atomic, against the
    legal limit. Atomic sits exactly on the limit at every N, so the limit is
    drawn first and atomic over it; naive simply stops where it stopped being run.
    """
    naive, atomic = read("exp1_naive_vs_n"), read("exp2_atomic_vs_n")
    if not naive or not atomic:
        print("!! missing exp1/exp2 CSVs -- skipping exp2")
        return None
    gn, ga = _by_workers(naive), _by_workers(atomic)
    ns = sorted(set(gn) | set(ga))
    xs = list(range(len(ns)))
    mean = lambda rs: sum(int(r["reserved_count"]) for r in rs) / len(rs)
    legal = int(atomic[0]["expected_max_success"])

    fig, ax = plt.subplots(figsize=(10, 5.6), facecolor=SURFACE)
    _style(ax)
    ax.axhline(legal, color=INK_SOFT, lw=1, ls=(0, (4, 3)), zorder=2)
    nx = [x for x, n in zip(xs, ns) if n in gn]
    nv = [mean(gn[n]) for n in ns if n in gn]
    ax_ = [x for x, n in zip(xs, ns) if n in ga]
    av = [mean(ga[n]) for n in ns if n in ga]
    ax.plot(nx, nv, color=OVER_CAP, lw=2.2, zorder=3)
    ax.scatter(nx, nv, s=70, color=OVER_CAP, edgecolor=SURFACE, lw=2, zorder=4)
    ax.plot(ax_, av, color=WITHIN_CAP, lw=2.2, zorder=5)
    ax.scatter(ax_, av, s=70, color=WITHIN_CAP, edgecolor=SURFACE, lw=2, zorder=6)
    ax.text(nx[-1], nv[-1] + 0.45, "naive", ha="center", fontsize=11,
            color=OVER_CAP, fontweight="bold")
    ax.text(ax_[-1], av[-1] + 0.45, "atomic", ha="center", fontsize=11,
            color=WITHIN_CAP, fontweight="bold")
    ax.text(-0.05, legal - 0.55, f"legal limit = {legal}", ha="left", fontsize=9.5,
            color=INK_SOFT)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(n) for n in ns])
    top = max(nv) * 1.25
    ax.set_ylim(0, top)
    ax.set_xlabel("concurrent jobs (N)", fontsize=10.5, color=INK_SOFT, labelpad=8)
    ax.set_ylabel("reservations granted (mean per run)", fontsize=10.5, color=INK_SOFT)
    breaches = sum(r["breach"] == "True" for r in atomic)
    _titles(
        fig,
        "Experiment 2 \u00b7 atomic grants exactly what fits",
        f"{breaches}/{len(atomic)} atomic runs breached, to N = {max(ga)}. "
        f"Naive was run to N = {max(gn)} only \u2014 its line ends there, not at 0.",
    )
    fig.subplots_adjust(top=0.84, bottom=0.12, left=0.09, right=0.98)
    path = RESULTS / out
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return path


def exp4_invariants(out="fig_exp4_invariants.png"):
    """Experiment 4: crash-test checks passed, one bar per invariant.

    There is no sweep axis here, only checks that pass or fail, so each bar is
    filled to its pass share with the count written beside it. The restart
    precondition carries no invariant and is left out of the bars.
    """
    rows = read("exp4_controlled_abort") + read("exp4_process_kill")
    if not rows:
        print("!! missing exp4 CSVs -- skipping exp4")
        return None
    labels = {
        "I1": "I1 \u00b7 cap never exceeded",
        "I2": "I2 \u00b7 ledger matches its records",
        "I3": "I3 \u00b7 settled stays settled",
        "I4": "I4 \u00b7 a retry charges once",
    }
    bars = []
    for inv, lab in labels.items():
        rs = [r for r in rows if r["invariant"] == inv]
        if rs:
            bars.append((lab, sum(r["passed"] == "True" for r in rs), len(rs)))
    bars.reverse()  # barh draws bottom-up; keep I1 on top

    fig, ax = plt.subplots(figsize=(10, 4.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ys = range(len(bars))
    ax.barh(ys, [1] * len(bars), height=0.5, color=GRID, zorder=2)
    ax.barh(ys, [p / t for _, p, t in bars], height=0.5, zorder=3,
            color=[WITHIN_CAP if p == t else OVER_CAP for _, p, t in bars])
    for y, (_, p, t) in zip(ys, bars):
        ax.text(1.02, y, f"{p}/{t}", va="center", fontsize=11, color=INK,
                fontweight="bold")
    ax.set_yticks(list(ys))
    ax.set_yticklabels([b[0] for b in bars], fontsize=11, color=INK)
    ax.set_xlim(0, 1.12)
    ax.set_xticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    passed = sum(r["passed"] == "True" for r in rows)
    modes = sorted({r["mode"] for r in rows})
    _titles(
        fig,
        "Experiment 4 \u00b7 a crash leaves a hold, never a loss",
        f"{passed}/{len(rows)} checks passed across {' and '.join(modes)}, "
        "faults injected between reserve and commit.",
    )
    fig.subplots_adjust(top=0.80, bottom=0.06, left=0.30, right=0.97)
    path = RESULTS / out
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return path


if __name__ == "__main__":
    for p in filter(None, [granted_distribution(), exp1_breach_rate(),
                          exp2_granted(), exp4_invariants()]):
        print(f"wrote {p.relative_to(ROOT)}")
