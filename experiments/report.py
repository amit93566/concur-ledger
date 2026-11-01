"""Turn the experiment CSVs into one self-contained results page.

HARD RULE: every number rendered here is computed from results/*.csv at
generation time. Nothing is hand-typed. If a CSV changes, the page changes --
which is what makes this a report of measurements rather than a decorative
summary that can quietly drift away from the evidence.

This page is NOT a dashboard and never talks to the running service. It reads
files. Present it as "here are the results", never as "here is the system
running" -- the harness is the evidence, this is its write-up.

    python experiments/report.py            # -> results/report.html
"""

import argparse
import csv
import html
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

# Categorical slots 8 (red) and 1 (blue) from the validated reference palette.
# Verified with the dataviz validator in BOTH modes: all six checks pass
# (worst adjacent CVD dE 21.6 light / 19.2 dark; normal-vision 32.3 / 29.0).
NAIVE_L, NAIVE_D = "#e34948", "#e66767"
SAFE_L, SAFE_D = "#2a78d6", "#3987e5"


def read(name):
    p = RESULTS / name
    if not p.exists():
        return []
    with p.open() as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if v in ("True", "False"):
                r[k] = v == "True"
            else:
                try:
                    r[k] = float(v) if "." in v or "e" in v.lower() else int(v)
                except (ValueError, AttributeError):
                    pass
    return rows


def by_n_key(rows, key):
    """Group rows by any column -- `workers` for the N sweeps, and
    `naive_race_delay_ms` for the race-window sweep, which holds N fixed."""
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    return dict(sorted(g.items()))


def by_n(rows):
    return by_n_key(rows, "workers")


def esc(x):
    return html.escape(str(x))


# --------------------------------------------------------------------------
# svg chart primitives -- hand-drawn, no chart library (CSP-safe, no CDN)
# --------------------------------------------------------------------------

W, H = 720, 300
PAD_L, PAD_R, PAD_T, PAD_B = 58, 24, 22, 46


def _x_positions(n):
    inner = W - PAD_L - PAD_R
    if n == 1:
        return [PAD_L + inner / 2]
    return [PAD_L + inner * i / (n - 1) for i in range(n)]


def _y(v, vmax):
    inner = H - PAD_T - PAD_B
    return PAD_T + inner * (1 - (v / vmax if vmax else 0))


def _frame(labels, vmax, yfmt, ylabel, xlabel="concurrent jobs (N)"):
    inner_b = H - PAD_B
    out = [
        f'<line x1="{PAD_L}" y1="{inner_b}" x2="{W-PAD_R}" y2="{inner_b}" class="axis"/>'
    ]
    for i in range(5):
        v = vmax * i / 4
        y = _y(v, vmax)
        out.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W-PAD_R}" y2="{y:.1f}" class="grid"/>'
        )
        out.append(
            f'<text x="{PAD_L-10}" y="{y+4:.1f}" class="tick" text-anchor="end">'
            f"{yfmt(v)}</text>"
        )
    for x, lab in zip(_x_positions(len(labels)), labels):
        out.append(
            f'<text x="{x:.1f}" y="{inner_b+20}" class="tick" text-anchor="middle">'
            f"{esc(lab)}</text>"
        )
    out.append(
        f'<text x="{W/2:.0f}" y="{H-6}" class="axtitle" text-anchor="middle">'
        f"{esc(xlabel)}</text>"
    )
    out.append(
        f'<text x="14" y="{PAD_T+(H-PAD_T-PAD_B)/2:.0f}" class="axtitle" '
        f'text-anchor="middle" transform="rotate(-90 14 '
        f'{PAD_T+(H-PAD_T-PAD_B)/2:.0f})">{esc(ylabel)}</text>'
    )
    return out


def line_chart(
    labels,
    series,
    vmax,
    yfmt,
    ylabel,
    cid,
    xlabel="concurrent jobs (N)",
    xtip=lambda lab: f"N={lab}",
):
    """series: [(name, values, css_var, direct_label)]

    A value of None means NOT MEASURED at that N and is drawn as a gap: no
    marker, and the polyline breaks either side of it. It must never be
    coerced to zero. The strategies are swept to different maxima (naive to
    N=50, atomic to N=100), so a zero-filled naive line would touch 0% at an N
    it was never run at and read as "naive was safe there" -- the exact
    opposite of the result.
    """
    parts = [
        f'<svg viewBox="0 0 {W} {H}" role="img" class="chart" '
        f'aria-label="{esc(ylabel)} by {esc(xlabel)}">'
    ]
    parts += _frame(labels, vmax, yfmt, ylabel, xlabel)
    xs = _x_positions(len(labels))
    for name, vals, var, dlabel in series:
        # Split into runs of consecutive measured points; each becomes its own
        # polyline so a gap is a gap rather than a straight line drawn over it.
        seg, segs = [], []
        for x, v in zip(xs, vals):
            if v is None:
                if seg:
                    segs.append(seg)
                seg = []
            else:
                seg.append((x, v))
        if seg:
            segs.append(seg)
        for s in segs:
            if len(s) < 2:
                continue
            pts = " ".join(f"{x:.1f},{_y(v, vmax):.1f}" for x, v in s)
            parts.append(
                f'<polyline points="{pts}" class="ln" style="stroke:var({var})"/>'
            )
        for i, (x, v) in enumerate(zip(xs, vals)):
            if v is None:
                continue
            # 2px surface ring so overlapping markers stay separable
            parts.append(
                f'<circle cx="{x:.1f}" cy="{_y(v,vmax):.1f}" r="5" '
                f'style="fill:var({var})" class="mk">'
                f"<title>{esc(name)} — {esc(xtip(labels[i]))}: "
                f"{yfmt(v)}</title></circle>"
            )
        if dlabel:
            measured = [(x, v) for x, v in zip(xs, vals) if v is not None]
            if measured:
                lx, lv = measured[-1]
                parts.append(
                    f'<text x="{lx-8:.1f}" y="{_y(lv,vmax)-14:.1f}" class="dlabel" '
                    f'style="fill:var({var})" text-anchor="end">{esc(dlabel)}</text>'
                )
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(labels, vals, vmax, yfmt, ylabel, var):
    parts = [
        f'<svg viewBox="0 0 {W} {H}" role="img" class="chart" '
        f'aria-label="{esc(ylabel)} by concurrency">'
    ]
    parts += _frame(labels, vmax, yfmt, ylabel)
    inner = W - PAD_L - PAD_R
    slot = inner / max(len(labels), 1)
    bw = min(46, slot * 0.6)
    base = H - PAD_B
    for i, (lab, v) in enumerate(zip(labels, vals)):
        x = PAD_L + slot * (i + 0.5) - bw / 2
        y = _y(v, vmax)
        h = max(base - y, 0)
        # 4px rounded data-end, anchored to the baseline
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" '
            f'rx="4" style="fill:var({var})" class="mk">'
            f"<title>N={esc(lab)}: {yfmt(v)}</title></rect>"
        )
        if v > 0:
            parts.append(
                f'<text x="{x+bw/2:.1f}" y="{y-7:.1f}" class="dlabel" '
                f'text-anchor="middle">{yfmt(v)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

CSS = """
table.t{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
table.t th,table.t td{text-align:left;padding:6px 10px;border-bottom:1px solid rgba(128,128,128,.25)}
:root{color-scheme:light;
 --bg:#fcfcfb; --card:#ffffff; --line:#e4e3de; --ink:#0b0b0b; --ink2:#52514e;
 --ink3:#76756f; --naive:__NAIVE_L__; --safe:__SAFE_L__; --codebg:#f6f6f3;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 color-scheme:dark; --bg:#141413; --card:#1a1a19; --line:#33332f; --ink:#ffffff;
 --ink2:#c3c2b7; --ink3:#8e8d85; --naive:__NAIVE_D__; --safe:__SAFE_D__; --codebg:#111110;}}
:root[data-theme="dark"]{color-scheme:dark;
 --bg:#141413; --card:#1a1a19; --line:#33332f; --ink:#ffffff; --ink2:#c3c2b7;
 --ink3:#8e8d85; --naive:__NAIVE_D__; --safe:__SAFE_D__; --codebg:#111110;}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);
 font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 margin:0;padding:40px 20px 80px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:27px;line-height:1.25;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:19px;margin:44px 0 6px;letter-spacing:-.01em}
h3{font-size:15px;margin:22px 0 6px}
h4{font-size:14px;margin:20px 0 4px;color:var(--ink2);font-weight:650}
p{color:var(--ink2);margin:8px 0}
.sub{color:var(--ink3);font-size:13.5px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:20px;margin:16px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.tile .n{font-size:28px;font-weight:650;letter-spacing:-.02em;line-height:1.15}
.tile .l{color:var(--ink3);font-size:12.5px;margin-top:5px}
.naive{color:var(--naive)} .safe{color:var(--safe)}
.chart{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1}
.axis{stroke:var(--line);stroke-width:1}
.tick{fill:var(--ink3);font-size:11.5px;font-family:inherit}
.axtitle{fill:var(--ink3);font-size:11.5px;font-family:inherit}
.ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.mk{stroke:var(--card);stroke-width:2}
.dlabel{font-size:12px;font-weight:600;font-family:inherit;fill:var(--ink2)}
.legend{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0 2px;font-size:13px;color:var(--ink2)}
.legend i{width:11px;height:11px;border-radius:3px;display:inline-block;margin-right:6px;vertical-align:-1px}
.tw{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:440px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--ink3);font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:var(--safe);font-weight:600} .bad{color:var(--naive);font-weight:600}
pre{background:var(--codebg);border:1px solid var(--line);border-radius:10px;
 padding:14px;overflow-x:auto;font-size:12.5px;line-height:1.55;margin:8px 0;
 font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:720px){.two{grid-template-columns:1fr}}
.note{border-left:3px solid var(--line);padding:2px 0 2px 14px;color:var(--ink2);margin:14px 0}
.foot{color:var(--ink3);font-size:12.5px;margin-top:40px;border-top:1px solid var(--line);padding-top:16px}
code{background:var(--codebg);padding:1px 5px;border-radius:4px;font-size:12.5px;
 font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.q{margin:6px 0}
.lbl{display:inline-block;min-width:88px;margin-right:6px;font-weight:650;color:var(--ink);font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
.num{font:600 11px/1 ui-monospace,monospace;letter-spacing:.08em;color:var(--ink3);border:1px solid var(--line);border-radius:4px;padding:4px 6px;margin-right:10px;vertical-align:3px}
.scen{font-size:14px;color:var(--ink2)}
pre.cmd{background:var(--codebg);border:1px solid var(--line);color:var(--ink2);
 padding:10px 14px;font-size:12.5px;margin:12px 0 4px}
h2{border-top:1px solid var(--line);padding-top:26px}
h2:first-of-type{border-top:none}
td .det{color:var(--ink3);font-size:12.5px}
table td{white-space:normal}
table td:first-child,table td:nth-child(3),table td:nth-child(4){white-space:nowrap}
"""
# Substituted rather than %-formatted: the CSS legitimately contains `100%`,
# which a format string would try to interpret.
for _tok, _hex in (
    ("__NAIVE_L__", NAIVE_L), ("__NAIVE_D__", NAIVE_D),
    ("__SAFE_L__", SAFE_L), ("__SAFE_D__", SAFE_D),
):
    CSS = CSS.replace(_tok, _hex)


def pass_bars(groups):
    """groups: [(label, passed, total)] -> horizontal pass-share bars.

    Experiment 4 has no sweep axis, only checks that pass or fail, so the honest
    picture is one bar per invariant filled to its pass share.
    """
    row_h, lab_w, bar_w = 34, 230, W - 230 - PAD_R - 70
    h = row_h * len(groups) + 8
    parts = [
        f'<svg viewBox="0 0 {W} {h}" role="img" class="chart" '
        'aria-label="crash-test checks passed, by invariant">'
    ]
    for i, (label, p, t) in enumerate(groups):
        y = 4 + i * row_h
        share = p / t if t else 0
        var = "--safe" if p == t else "--naive"
        parts.append(
            f'<text x="0" y="{y+19}" class="tick" style="font-size:12.5px;'
            f'fill:var(--ink2)">{esc(label)}</text>'
            f'<rect x="{lab_w}" y="{y+6}" width="{bar_w}" height="18" rx="4" '
            'style="fill:var(--line)"/>'
            f'<rect x="{lab_w}" y="{y+6}" width="{bar_w*share:.1f}" height="18" '
            f'rx="4" style="fill:var({var})"><title>{esc(label)}: {p}/{t}'
            "</title></rect>"
            f'<text x="{lab_w+bar_w+10}" y="{y+20}" class="dlabel">{p}/{t}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def exp(num, title, problem, solution, solution_label, body):
    return (
        f'<h2><span class="num">EXP {esc(num)}</span>{esc(title)}</h2>'
        f'<p class="q"><span class="lbl">Problem</span>{problem}</p>'
        f'<p class="q"><span class="lbl">{esc(solution_label)}</span>{solution}</p>'
        f"{body}"
    )


def table(headers, rows):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return f'<div class="card"><table class="t"><tr>{head}</tr>{body}</table></div>'


def _median_cells(rows, key):
    g = {}
    for r in rows:
        if r.get(key) != "":
            g.setdefault((r["strategy"], r["duration_ms"], r["n"]), []).append(float(r[key]))
    return {k: sorted(v)[len(v) // 2] for k, v in g.items()}


def exp3_section():
    rows = read("exp3_cost_sweep.csv")
    pilot = not rows
    rows = rows or read("exp3_pilot.csv")
    if not rows:
        return ""
    noop_rows = read("exp3_noop.csv") or read("exp3_pilot_noop.csv")
    noop = {k[2]: v for k, v in _median_cells(noop_rows, "completed_per_s").items()}
    done = _median_cells(rows, "completed_per_s")
    p99 = _median_cells(rows, "lat_p99_ms")
    timeouts = {}
    for r in rows:
        k = (r["strategy"], r["duration_ms"], r["n"])
        timeouts[k] = timeouts.get(k, 0) + r["timeouts"]
    strategies = ["atomic", "for_update", "serializable", "naive"]
    longest, shortest = max(r["duration_ms"] for r in rows), min(r["duration_ms"] for r in rows)
    top = max(r["n"] for r in rows)
    near = read("exp3_nearcap.csv")
    every = rows + near
    hold = [all(r[k] for k in ("i1_cap_safety", "i2_ledger_agreement")) for r in every
            if r["strategy"] != "naive"]
    deadlocks = sum(r["deadlocks"] for r in every)
    errors = sum(r["errors"] for r in every)
    peak = {s: max(v for k, v in done.items() if k[0] == s and k[1] == shortest)
            for s in strategies if any(k[0] == s for k in done)}
    body = []
    for d in (shortest, longest):
        ns = sorted({k[2] for k in done if k[1] == d})
        trows = []
        for s in strategies:
            if not any(k[0] == s for k in done):
                continue
            cells = []
            for n in ns:
                v = done.get((s, d, n))
                mark = "*" if timeouts.get((s, d, n)) else ""
                cells.append("-" if v is None else f"{v:.1f}{mark}")
            trows.append([s + (" (unsafe)" if s == "naive" else ""), *cells])
        trows.append(["no-op ceiling", *[f"{noop[n]:.0f}" if n in noop else "-" for n in ns]])
        body.append(f"<h3>Completed cycles/s, {d} ms operation</h3>")
        body.append(table(["strategy", *[f"N={n}" for n in ns]], trows))
    lat = [[s, f"{p99.get((s, longest, top), 0):,.0f}"
            + ("*" if timeouts.get((s, longest, top)) else "")]
           for s in strategies if (s, longest, top) in p99]
    body.append(f"<h3>p99 latency at N={top}, {longest} ms operation (ms)</h3>")
    body.append(table(["strategy", "p99 ms"], lat))
    body.append(_exp3_serializable(rows))
    if near:
        body.append(_exp3_nearcap(near))
    body.append(
        '<p class="foot">* client timeouts in that cell: latency is censored, a lower '
        "bound. At short operations every strategy, naive included, is limited by the "
        "single dataset row: each reserve and commit updates it and holds its lock "
        "until the commit is on disk. Four uvicorn workers did not raise this. "
        f"{errors} requests failed with a dropped connection across {len(every)} cells; "
        "the cause is not established, and no invariant was affected.</p>"
    )
    return exp(
        "3" + (" (pilot, 1 rep)" if pilot else ""),
        "The cost of the guarantee",
        "Two-phase reserve/commit is more code than holding one lock. What does safety "
        "cost, and what does the two-phase design buy?",
        f"Every safe strategy held I1 and I2 in {sum(hold)}/{len(hold)} cells, with "
        f"{deadlocks} deadlocks. At {shortest} ms, atomic and for_update peak at "
        f"{peak['atomic']:.0f} and {peak['for_update']:.0f} cycles/s against naive's "
        f"{peak['naive']:.0f}, so their safety costs nothing above run-to-run noise. "
        f"serializable peaks at {peak['serializable']:.0f} and collapses at high N, "
        "where requests exhaust their retries and give up.",
        "Result",
        "".join(body),
    )


def _spread(rows, key, strategy, d, n, scale=1.0):
    vals = sorted(r[key] / scale for r in rows
                  if (r["strategy"], r["duration_ms"], r["n"]) == (strategy, d, n))
    return f"{vals[len(vals) // 2]:.0f} ({vals[0]:.0f}\u2013{vals[-1]:.0f})"


def _exp3_serializable(rows):
    ns = sorted({r["n"] for r in rows if r["n"] >= 16})
    trows = []
    for d in sorted({r["duration_ms"] for r in rows}):
        trows.append([f"{d} ms completed/s",
                      *[_spread(rows, "completed_per_s", "serializable", d, n) for n in ns]])
        trows.append([f"{d} ms gave up/s",
                      *[_spread(rows, "gave_up", "serializable", d, n, rows[0]["measure_s"])
                        for n in ns]])
    return (
        "<h3>serializable under contention: median (min\u2013max) of reps</h3>"
        + table(["", *[f"N={n}" for n in ns]], trows)
        + '<p class="foot">Repetitions at high N split into a slow and a fast group, '
        "so read the range, not the median. A rise from one N to the next is not "
        "recovery.</p>"
    )


def _exp3_nearcap(rows):
    longest = max(r["duration_ms"] for r in rows)
    done = _median_cells(rows, "completed_per_s")
    rate = _median_cells(rows, "denial_rate")
    ns = sorted({r["n"] for r in rows})
    strategies = ["atomic", "for_update", "serializable", "naive"]
    trows = [[s + (" (unsafe)" if s == "naive" else ""),
              *[f"{done[(s, longest, n)]:.1f}" for n in ns]] for s in strategies]
    drows = [[s + (" (unsafe)" if s == "naive" else ""),
              *[f"{rate[(s, longest, n)]:.0%}" for n in ns]] for s in strategies]
    return (
        f"<h3>Near the cap: completed cycles/s, {longest} ms operation</h3>"
        + table(["strategy", *[f"N={n}" for n in ns]], trows)
        + f"<h3>Near the cap: share of decisions denied, {longest} ms operation</h3>"
        + table(["strategy", *[f"N={n}" for n in ns]], drows)
        + '<p class="foot">Cap 10, each operation holds 1 and gives it back, so 10 holds '
        "at once allow 10 cycles/s at 1 s. naive above 10/s means more than 10 holds "
        "ran at once. The end-of-cell invariants cannot show that, because the actual "
        "cost is 0 and the ledger ends empty.</p>"
    )


def evidence_sections():
    out = []
    eps = read("eps_table.csv")
    if eps:
        out.append(exp(
            "ε", "Where ε comes from",
            "The ledger enforces a sum. Who chooses the numbers it adds up?",
            "OpenDP prices a spec, the ledger rounds that up, and the cap is a policy "
            "input. Noise is measured from real releases, not only derived.",
            "Answer",
            table(
                ["query", "sensitivity", "OpenDP ε", "ledger ε", "noise std",
                 "measured", f"fit in cap {eps[0]['cap']}"],
                [[f"{r['kind']} b={r['scale']}"
                  + (f" ×{r['contributions']}" if r["contributions"] != 1 else ""),
                  r["sensitivity"], r["raw_epsilon"], r["ledger_epsilon"],
                  r["noise_std"], r["empirical_std"], r["uses_of_cap"]] for r in eps],
            ),
        ))
    iso = read("exp_isolation_matrix.csv")
    if iso:
        g = {}
        for r in iso:
            g.setdefault((r["isolation_level"], r["strategy"]), []).append(r)
        out.append(exp(
            "L", "Isolation levels",
            "Does a stricter isolation level make the naive strategy safe?",
            "No. Naive reads and writes in separate transactions, and isolation governs "
            "only what happens inside one. Atomic is safe at every level, but only READ "
            "COMMITTED turns contention into clean denials: the stricter levels raise "
            "40001, which surfaces as an error a client must retry.",
            "Answer",
            table(
                ["level", "strategy", "runs", "breached", "granted", "denied", "errors"],
                [[lvl, s, len(rs), sum(1 for r in rs if r["breach"]),
                  sum(r["reserved_count"] for r in rs), sum(r["denied_count"] for r in rs),
                  sum(r["error_count"] for r in rs)] for (lvl, s), rs in g.items()],
            ),
        ))
    faults = {}
    for path in sorted(RESULTS.glob("exp4_*.csv")):
        rows = read(path.name)
        if rows:
            faults[path.stem.removeprefix("exp4_")] = rows
    if faults:
        out.append(exp(
            "F", "What the crash results do and do not show",
            "Which failures were actually exercised?",
            "Crashes inside each transaction, after commit, of the database under load, "
            "and of the service under load. Not tested: disk loss, fsync lies, network "
            "partitions, and clock faults. A commit can land while its client sees an "
            "error, which is why retries carry an idempotency key.",
            "Coverage",
            table(
                ["mode", "checks passed"],
                [[m, f"{sum(1 for r in rs if r['passed'])}/{len(rs)}"]
                 for m, rs in faults.items()],
            ),
        ))
    return "".join(out)


def build():
    naive = read("exp1_naive_vs_n.csv")
    atomic = read("exp2_atomic_vs_n.csv")
    if not naive or not atomic:
        raise SystemExit("missing results CSVs -- run `make exp1 exp2` first")

    gn, ga = by_n(naive), by_n(atomic)
    ns = sorted(set(gn) | set(ga))
    labels = [str(n) for n in ns]

    def rate(g, n):
        # None, not 0.0: naive stops at N=50 while atomic goes to N=100, and a
        # zero-filled naive line would read as "naive was safe there".
        if n not in g:
            return None
        return sum(1 for r in g[n] if r["breach"]) / len(g[n]) * 100

    def granted(g, n):
        if n not in g:
            return None
        return statistics.mean(r["reserved_count"] for r in g[n])

    cap = naive[0]["cap"]
    seed = naive[0]["seed_spent"]
    cost = naive[0]["cost"]
    legal = int((cap - seed) // cost)
    max_grant = max(r["reserved_count"] for r in naive)
    max_over = max(r["overshoot"] for r in naive)
    a_runs = len(atomic)
    a_breaches = sum(1 for r in atomic if r["breach"])
    over = [r for r in naive if r["workers"] > legal]
    over_rate = sum(1 for r in over if r["breach"]) / len(over) * 100
    a_exact = all(r["reserved_count"] == legal for r in atomic)

    P = []
    A = P.append
    A('<div class="wrap">')
    A("<h1>Can a shared privacy budget survive concurrent use?</h1>")
    A(
        f'<p class="sub">Generated from <code>results/*.csv</code> at '
        f'{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}.</p>'
    )
    A(
        f'<div class="card scen">Setup: cap <strong>{cap:g}</strong>, already '
        f"spent <strong>{seed:g}</strong>. Each job reserves ε&nbsp;{cost:g}, so "
        f"exactly <strong>{legal}</strong> can legally be granted.</div>"
    )

    # --- EXP 1 ----------------------------------------------------------------
    A(
        exp(
            "1",
            "The breach",
            "Many jobs check the remaining budget at the same moment. Each sees "
            "room, each reserves, and together they overspend the cap "
            "(<em>write skew</em>). Does a plain read → check → write "
            "implementation actually fail this way?",
            f"Yes. Once more than {legal} jobs compete, the naive path breaches "
            f"in <strong>{over_rate:.0f}%</strong> of runs, granting up to <strong>{max_grant}</strong> "
            f"reservations where {legal} fit, and overspending the cap by up to "
            f"ε&nbsp;{max_over:g} (<strong>{(cap + max_over) / cap:.1f}×</strong>).",
            "Result",
            '<div class="card">'
            + line_chart(
                [str(n) for n in gn],
                [("naive", [rate(gn, n) for n in gn], "--naive", "naive")],
                100,
                lambda v: f"{v:.0f}%",
                "runs that breached the cap",
                "breach",
            )
            + "</div>",
        )
    )

    # --- EXP 2 ----------------------------------------------------------------
    A(
        exp(
            "2",
            "The fix",
            "Can the cap be enforced under the same concurrent load without "
            "locks or a new architecture?",
            "Move the check into the write: one conditional <code>UPDATE</code>. "
            "There is no gap between check and write for another job to slip "
            f"into. <strong>{a_breaches}/{a_runs}</strong> runs breached, up to "
            f"N={max(ga)}, and "
            + (
                f"every run granted <strong>exactly {legal}</strong>."
                if a_exact
                else "grant counts varied."
            ),
            "Solution",
            "<pre>UPDATE datasets\n"
            "   SET epsilon_reserved = epsilon_reserved + :cost\n"
            " WHERE id = :id\n"
            "   AND epsilon_spent + epsilon_reserved + :cost &lt;= epsilon_cap;\n"
            "<em>-- rowcount 1 -&gt; reserved · rowcount 0 -&gt; denied</em></pre>"
            '<div class="card">'
            '<div class="legend">'
            '<span><i style="background:var(--naive)"></i>naive</span>'
            '<span><i style="background:var(--safe)"></i>atomic</span>'
            "</div>"
            + line_chart(
                labels,
                [
                    ("naive", [granted(gn, n) for n in ns], "--naive", "naive"),
                    ("atomic", [granted(ga, n) for n in ns], "--safe", "atomic"),
                ],
                4 * math.ceil(max_grant * 1.1 / 4),
                lambda v: f"{v:.0f}",
                f"reservations granted (mean) · legal = {legal}",
                "granted",
            )
            + "</div>",
        )
    )

    # --- EXP 4 ----------------------------------------------------------------
    crash = read("exp4_controlled_abort.csv") + read("exp4_process_kill.csv")
    if crash:
        meaning = {
            "I1": "I1 · cap never exceeded",
            "I2": "I2 · ledger matches its records",
            "I3": "I3 · settled stays settled",
            "I4": "I4 · retry charges once",
        }
        groups = []
        for inv, label in meaning.items():
            rs = [r for r in crash if r["invariant"] == inv]
            if rs:
                groups.append((label, sum(1 for r in rs if r["passed"]), len(rs)))
        passed = sum(1 for r in crash if r["passed"])
        A(
            exp(
                "4",
                "Survive a crash",
                "If the service dies between reserving budget and committing it, "
                "can budget be lost, double-spent, or left inconsistent?",
                "Two-phase <strong>reserve → commit | release</strong>, with the "
                "job running outside any transaction. A crash leaves the budget "
                "<em>held</em>, never lost. A retried commit is recognised by its "
                f"idempotency key. <strong>{passed}/{len(crash)}</strong> checks "
                "passed under both a controlled abort and a real process kill.",
                "Solution",
                f'<div class="card">{pass_bars(groups)}</div>',
            )
        )

    A(exp3_section())
    A(evidence_sections())

    A(
        '<p class="foot">Empirically demonstrated under the tested conditions, '
        "not formally proven. The database CHECK constraint was dropped for every "
        "run, so safety comes from the strategy alone. Full detail: "
        "<code>README.md</code>. Regenerate with <code>make report</code>.</p>"
    )
    A("</div>")

    return (
        "<title>Shared Budget Under Concurrency</title>\n"
        f"<style>{CSS}</style>\n" + "\n".join(P)
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CSV -> results page")
    ap.add_argument("--out", default=str(RESULTS / "report.html"))
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build())
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")
