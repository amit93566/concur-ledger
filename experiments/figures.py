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


# --- Experiment 3 and the locking evidence ---------------------------------

# Fixed per strategy, so a strategy keeps its colour on every figure. Hue order
# checked with the palette validator; line style and markers repeat the identity.
STRATEGY_STYLE = {
    "atomic": ("#2a78d6", "o", "-"),
    "for_update": ("#eb6834", "s", "-"),
    "serializable": ("#1baf7a", "^", "-"),
    "locked_1phase": ("#eda100", "D", "-"),
    "naive": ("#e87ba4", "v", ":"),
}
STRATEGY_LABEL = {"naive": "naive (unsafe, for reference)"}


def _exp3_rows(name):
    """The full sweep, else the pilot, so the figure code can be tested early.
    Returns (rows, source name); the source goes in the subtitle."""
    for candidate in (name, "exp3_pilot" if name == "exp3_cost_sweep" else None,
                      "exp3_pilot_noop" if name == "exp3_noop" else None):
        rows = read(candidate) if candidate else []
        if rows:
            return rows, candidate
    return [], name


def _cells(rows, key):
    """{(strategy, duration, n): (median, min, max, any timeouts)} over reps."""
    g = {}
    for r in rows:
        if r.get(key) not in (None, ""):
            g.setdefault((r["strategy"], int(r["duration_ms"]), int(r["n"])), []).append(r)
    out = {}
    for k, rs in g.items():
        vals = sorted(float(r[key]) for r in rs)
        out[k] = (vals[len(vals) // 2], vals[0], vals[-1],
                  any(int(r["timeouts"]) > 0 for r in rs))
    return out


def _exp3_panels(cells, ylabel, ceiling=None, log=True, strategies=None):
    durations = sorted({d for _, d, _ in cells})
    fig, axes = plt.subplots(1, len(durations), figsize=(11, 5.6), sharey=True,
                             facecolor=SURFACE, squeeze=False)
    for ax, d in zip(axes[0], durations):
        _style(ax)
        ns = sorted({n for _, dd, n in cells if dd == d})
        if ceiling:
            xs = sorted(ceiling)
            ax.plot(xs, [ceiling[n] for n in xs], color=INK_SOFT, ls="--", lw=1.5,
                    label="no-op ceiling")
        for strat, (colour, marker, ls) in STRATEGY_STYLE.items():
            if strategies and strat not in strategies:
                continue
            pts = [(n, cells[(strat, d, n)]) for n in ns if (strat, d, n) in cells]
            if not pts:
                continue
            xs = [n for n, _ in pts]
            ax.plot(xs, [c[0] for _, c in pts], color=colour, ls=ls, lw=2, zorder=3,
                    label=STRATEGY_LABEL.get(strat, strat))
            ax.fill_between(xs, [c[1] for _, c in pts], [c[2] for _, c in pts],
                            color=colour, alpha=0.15, lw=0, zorder=2)
            # Hollow marker: timeouts in this cell, so the value is censored.
            for n, c in pts:
                ax.plot(n, c[0], marker=marker, ms=7, mec=SURFACE if not c[3] else colour,
                        mew=1.2 if not c[3] else 1.8,
                        mfc=colour if not c[3] else SURFACE, zorder=4)
        ax.set_xscale("log", base=2)
        if log:
            ax.set_yscale("log")
        ax.set_xticks(ns, [str(n) for n in ns])
        ax.minorticks_off()
        ax.set_title(f"{d / 1000:g} s operation" if d >= 1000 else f"{d} ms operation",
                     loc="left", fontsize=11, color=INK)
        ax.set_xlabel("concurrent workers (N, closed loop)", fontsize=10, color=INK_SOFT)
    axes[0][0].set_ylabel(ylabel, fontsize=10, color=INK_SOFT)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False,
               fontsize=9.5, labelcolor=INK_SOFT)
    fig.subplots_adjust(top=0.78, bottom=0.2, left=0.08, right=0.97, wspace=0.06)
    return fig


def _save(fig, out):
    path = RESULTS / out
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return path


def _note(source):
    return " PILOT DATA (1 rep)." if "pilot" in source else ""


def exp3_throughput(out="fig_exp3_throughput.png"):
    rows, source = _exp3_rows("exp3_cost_sweep")
    if not rows:
        return None
    noop, _ = _exp3_rows("exp3_noop")
    ceiling = {n: v[0] for (_, _, n), v in _cells(noop, "completed_per_s").items()}
    cells = _cells(rows, "completed_per_s")
    fig = _exp3_panels(cells, "completed cycles per second (log)", ceiling)
    longest = max(d for _, d, _ in cells)
    lk = [v[0] for (s, d, _), v in cells.items() if s == "locked_1phase" and d == longest]
    _titles(fig, "Experiment 3 \u00b7 holding the lock across the operation caps throughput",
            f"locked_1phase peaks at {max(lk):.1f} cycles/s at {longest / 1000:g} s, "
            f"whatever N is. Median of reps, band = min\u2013max.{_note(source)}")
    return _save(fig, out)


def exp3_latency(out="fig_exp3_latency.png"):
    rows, source = _exp3_rows("exp3_cost_sweep")
    if not rows:
        return None
    fig = _exp3_panels(_cells(rows, "lat_p99_ms"), "p99 full-cycle latency, ms (log)")
    _titles(fig, "Experiment 3 \u00b7 tail latency of the full cycle",
            "Hollow marker: client timeouts in that cell, so latency is censored "
            f"(a lower bound).{_note(source)}")
    return _save(fig, out)


def exp3_retries(out="fig_exp3_retries.png"):
    rows, source = _exp3_rows("exp3_cost_sweep")
    if not rows:
        return None
    fig = _exp3_panels(_cells(rows, "retries_per_success"), "40001 retries per success",
                       log=False, strategies={"serializable"})
    _titles(fig, "Experiment 3 \u00b7 what serializable pays under contention",
            f"Retries are spent on conflicts that a row lock would queue.{_note(source)}")
    return _save(fig, out)


def exp3_denials(out="fig_exp3_denials.png"):
    rows, source = _exp3_rows("exp3_nearcap")
    if not rows:
        return None
    fig = _exp3_panels(_cells(rows, "denial_rate"), "share of decisions denied",
                       log=False)
    _titles(fig, "Experiment 3 \u00b7 near the cap, held budget becomes denials",
            "Each operation holds 1 of a cap of 10 and gives it back: at most 10 holds "
            f"fit.{_note(source)}")
    return _save(fig, out)


def lock_waits(out="fig_lock_waits.png"):
    rows = read("exp_lock_waits")
    if not rows:
        return None
    strategies = sorted({r["strategy"] for r in rows})
    fig, axes = plt.subplots(len(strategies), 1, figsize=(11, 5.2), sharex=True,
                             sharey=True, facecolor=SURFACE, squeeze=False)
    for ax, strat in zip(axes[:, 0], strategies):
        _style(ax)
        rs = [r for r in rows if r["strategy"] == strat]
        t0 = float(rs[0]["t_s"])
        ax.step([float(r["t_s"]) - t0 for r in rs], [int(r["waiting_on_lock"]) for r in rs],
                where="post", color=STRATEGY_STYLE[strat][0], lw=1.5)
        ax.set_title(strat, loc="left", fontsize=11, color=INK)
        ax.set_ylabel("backends waiting", fontsize=10, color=INK_SOFT)
    axes[-1][0].set_xlabel("seconds since the first of 5 bursts of 50 requests",
                           fontsize=10, color=INK_SOFT)
    _titles(fig, "Backends waiting on the dataset row lock",
            "Sampled from pg_stat_activity by a separate connection, about every 1.6 ms.")
    fig.subplots_adjust(top=0.80, bottom=0.11, left=0.08, right=0.97, hspace=0.35)
    return _save(fig, out)


if __name__ == "__main__":
    for p in filter(None, [granted_distribution(), exp1_breach_rate(),
                          exp2_granted(), exp4_invariants(), exp3_throughput(),
                          exp3_latency(), exp3_retries(), exp3_denials(), lock_waits()]):
        print(f"wrote {p.relative_to(ROOT)}")
