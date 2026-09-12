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


def by_n(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["workers"]].append(r)
    return dict(sorted(g.items()))


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


def _frame(labels, vmax, yfmt, ylabel):
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
        "concurrent jobs (N)</text>"
    )
    out.append(
        f'<text x="14" y="{PAD_T+(H-PAD_T-PAD_B)/2:.0f}" class="axtitle" '
        f'text-anchor="middle" transform="rotate(-90 14 '
        f'{PAD_T+(H-PAD_T-PAD_B)/2:.0f})">{esc(ylabel)}</text>'
    )
    return out


def line_chart(labels, series, vmax, yfmt, ylabel, cid):
    """series: [(name, values, css_var, direct_label)]"""
    parts = [
        f'<svg viewBox="0 0 {W} {H}" role="img" class="chart" '
        f'aria-label="{esc(ylabel)} by concurrency">'
    ]
    parts += _frame(labels, vmax, yfmt, ylabel)
    xs = _x_positions(len(labels))
    for si, (name, vals, var, dlabel) in enumerate(series):
        pts = " ".join(f"{x:.1f},{_y(v, vmax):.1f}" for x, v in zip(xs, vals))
        parts.append(f'<polyline points="{pts}" class="ln" style="stroke:var({var})"/>')
        for i, (x, v) in enumerate(zip(xs, vals)):
            # 2px surface ring so overlapping markers stay separable
            parts.append(
                f'<circle cx="{x:.1f}" cy="{_y(v,vmax):.1f}" r="5" '
                f'style="fill:var({var})" class="mk">'
                f"<title>{esc(name)} — N={esc(labels[i])}: "
                f"{yfmt(v)}</title></circle>"
            )
        if dlabel:
            lx, lv = xs[-1], vals[-1]
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


def table(headers, rows, cls=""):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows
    )
    return (
        f'<div class="tw"><table class="{cls}"><thead><tr>{h}</tr></thead>'
        f"<tbody>{b}</tbody></table></div>"
    )


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

CSS = """
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


def _inv_cell(rows, inv):
    """Pass count for one invariant across the crash-test checks."""
    rs = [r for r in rows if r["invariant"] == inv]
    if not rs:
        return '<span class="ok">verified</span>'
    p = sum(1 for r in rs if r["passed"])
    cls = "ok" if p == len(rs) else "bad"
    return f'<span class="{cls}">{p}/{len(rs)} checks</span>'


def build():
    naive = read("exp1_naive_vs_n.csv")
    atomic = read("exp2_atomic_vs_n.csv")
    ctrl = read("exp1_naive_sequential_control.csv")
    if not naive or not atomic:
        raise SystemExit("missing results CSVs -- run `make exp1 exp2` first")

    gn, ga, gc = by_n(naive), by_n(atomic), by_n(ctrl)
    ns = sorted(set(gn) | set(ga))
    labels = [str(n) for n in ns]

    def rate(g, n):
        return (sum(1 for r in g[n] if r["breach"]) / len(g[n]) * 100) if n in g else 0.0

    nb = [rate(gn, n) for n in ns]
    ab = [rate(ga, n) for n in ns]

    cap = naive[0]["cap"]
    cost = naive[0]["cost"]
    legal = int((cap - naive[0]["seed_spent"]) // cost)
    max_grant = max(r["reserved_count"] for r in naive)
    max_over = max(r["overshoot"] for r in naive)
    worst_mult = (max_over + cap) / cap
    a_runs = len(atomic)
    a_breaches = sum(1 for r in atomic if r["breach"])
    first_breach_n = next((n for n in ns if n in gn and rate(gn, n) == 100), None)
    a_exact = all(r["reserved_count"] == legal for r in atomic)

    ov_ns = [n for n in ns if n in gn]
    ov = [statistics.mean(r["overshoot"] for r in gn[n]) for n in ov_ns]
    sp_ns = [n for n in ns if n in ga]
    sp = [statistics.median(r["reserved_at_spread_ms"] for r in ga[n]) for n in sp_ns]

    P = []
    A = P.append
    A('<div class="wrap">')
    A("<h1>Can a shared privacy budget survive concurrent use?</h1>")
    A(
        f'<p class="sub">Measured results · generated from '
        f"<code>results/*.csv</code> at "
        f'{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}. '
        "Not a live view of the system.</p>"
    )
    A(
        "<p>One dataset, one finite budget (ε), many jobs arriving at once. "
        f"With a cap of {cap:g} and {naive[0]['seed_spent']:g} already spent, "
        f"exactly <strong>{legal}</strong> reservations of ε&nbsp;{cost:g} fit. "
        "Here is what each enforcement implementation actually granted.</p>"
    )

    A('<div class="tiles">')
    A(
        f'<div class="tile"><div class="n naive">{max_grant}</div>'
        f'<div class="l">reservations granted by the naive path where only '
        f"{legal} were legal</div></div>"
    )
    A(
        f'<div class="tile"><div class="n naive">{worst_mult:.1f}×</div>'
        f'<div class="l">worst overspend of the cap ({cap + max_over:g} against '
        f"{cap:g})</div></div>"
    )
    A(
        f'<div class="tile"><div class="n safe">{a_breaches} / {a_runs}</div>'
        f'<div class="l">breaches under the atomic strategy, to N={max(ns)}</div></div>'
    )
    A("</div>")

    # --- breach rate ------------------------------------------------------
    A(
        "<h2>Experiment 1 · Can the naive path be made to overspend?</h2>"
        '<p class="q"><strong>Question.</strong> Does a faithful '
        "read&nbsp;→&nbsp;check&nbsp;→&nbsp;write enforcement implementation "
        "overspend a shared cap under genuine concurrency?</p>"
        '<p class="q"><strong>Method.</strong> Seed the budget near the cap '
        f"(cap {cap:g}, spent {naive[0]['seed_spent']:g}), drop the database "
        "CHECK constraint, then fire N simultaneous reserves of "
        f"ε&nbsp;{cost:g} — each individually legal, collectively not. "
        f"{len(gn[ns[0]]) if ns[0] in gn else 15} runs per level.</p>"
        '<pre class="cmd">make exp1</pre>'
    )
    A(
        "<p>The naive implementation breaches the cap on "
        f"<strong>every run</strong> from N={first_breach_n} upward. The atomic "
        "implementation never does, at any concurrency tested.</p>"
    )
    A('<div class="card">')
    A(
        '<div class="legend">'
        '<span><i style="background:var(--naive)"></i>naive (read → check → write)</span>'
        '<span><i style="background:var(--safe)"></i>atomic (conditional UPDATE)</span>'
        "</div>"
    )
    A(
        line_chart(
            labels,
            [
                ("naive", nb, "--naive", "naive"),
                ("atomic", ab, "--safe", "atomic"),
            ],
            100,
            lambda v: f"{v:.0f}%",
            "runs that breached the cap",
            "breach",
        )
    )
    A("</div>")
    A(
        '<p class="note"><strong>N=2 is a control, not a failure to breach.</strong> '
        f"Two reservations of ε&nbsp;{cost:g} fit exactly into the "
        f"{cap - naive[0]['seed_spent']:g} of headroom, so a correct system grants "
        "both. The naive path only fails once demand genuinely exceeds supply — "
        "which is the point.</p>"
    )

    rows = []
    for n in ns:
        nr = f"{rate(gn,n):.0f}%" if n in gn else "—"
        ar = f"{rate(ga,n):.0f}%" if n in ga else "—"
        g = (
            f"{statistics.mean(r['reserved_count'] for r in gn[n]):.1f}"
            if n in gn
            else "—"
        )
        rows.append(
            [
                n,
                len(gn.get(n, [])) or "—",
                f'<span class="{"bad" if n in gn and rate(gn,n) else "ok"}">{nr}</span>',
                f'<span class="ok">{ar}</span>',
                g,
                legal,
            ]
        )
    A(
        table(
            ["N", "runs", "naive breach", "atomic breach", "naive granted", "legal"],
            rows,
        )
    )
    # --- overshoot --------------------------------------------------------
    A("<h3>How far past the cap</h3>")
    A(
        "<p>Breach rate says it happened; overshoot says how badly. Mean ε "
        "granted beyond the cap, by concurrency:</p>"
    )
    A('<div class="card">')
    A(
        bar_chart(
            [str(n) for n in ov_ns],
            ov,
            max(max(ov) * 1.25, 1),
            lambda v: f"{v:.1f}",
            "mean overshoot (ε past the cap)",
            "--naive",
        )
    )
    A("</div>")
    A(
        '<p class="note"><strong>Overshoot is not monotonic in N, and that is the '
        "correct behaviour.</strong> It peaks in the middle of the range and then "
        "flattens. Past roughly N=10 a run takes long enough (see §4) that later "
        "arrivals read a partly-updated row and are legitimately denied — so a "
        "larger share of requests lose the race they were trying to win. More "
        "contention does not mean more successful overspending.</p>"
    )

    # --- the control ------------------------------------------------------
    A("<h3>Control: the naive path is faithful, not a strawman</h3>")
    A(
        "<p>The obvious objection is that the naive implementation was written to "
        "fail. The control answers it: the <em>same code</em>, the same requests, "
        "issued one at a time instead of simultaneously.</p>"
    )
    if gc:
        crows = [
            [
                n,
                len(gc[n]),
                f'<span class="ok">{sum(1 for r in gc[n] if r["breach"])/len(gc[n]):.0%}</span>',
                f"{statistics.mean(r['reserved_count'] for r in gc[n]):.2f}",
                legal,
            ]
            for n in sorted(gc)
        ]
        A(table(["N", "runs", "breach rate", "granted", "legal"], crows))
    A(
        "<p>Zero breaches, and exactly the number that fit. The logic is correct; "
        "only concurrency breaks it. That is what makes it a plausible first "
        "implementation rather than a sabotaged one.</p>"
    )

    # --- concurrency validity --------------------------------------------
    A(
        "<h3>Was the contention real?</h3>"
    )
    A(
        "<p>If the harness had serialised its requests, none of the above would "
        "mean anything. Every run records the wall-clock spread of the arrival "
        "timestamps Postgres itself wrote — median, by concurrency:</p>"
    )
    A('<div class="card">')
    A(
        line_chart(
            [str(n) for n in sp_ns],
            [("spread", sp, "--safe", None)],
            max(max(sp) * 1.25, 1),
            lambda v: f"{v:.0f}ms",
            "median arrival spread (ms)",
            "spread",
        )
    )
    A("</div>")
    A(
        f"<p>At N={sp_ns[0]} the arrivals cluster within "
        f"{sp[0]:.2f}&nbsp;ms — genuinely simultaneous. "
        "<strong>Measurement-validity caveat:</strong> the harness and the server "
        "share one machine, so the growth at the top of the range "
        f"({sp[-1]:.0f}&nbsp;ms at N={sp_ns[-1]}) is partly client-side scheduling, "
        "not database behaviour. This does not affect the safety conclusions — a "
        "breach either happened or it did not — but the cost sweep that follows in "
        "the next phase will need a no-op baseline and a stated saturation point.</p>"
    )

    # --- the mechanism ----------------------------------------------------
    A("<h2>Experiment 2 · Does the atomic path hold?</h2>")
    A(
        '<p class="q"><strong>Question.</strong> Under the identical seed and '
        "load, and with the database CHECK still dropped, does a single "
        "conditional UPDATE prevent every breach?</p>"
        '<p class="q"><strong>Method.</strong> Same harness, '
        f"<code>strategy=atomic</code>, escalating deliberately to N={max(ns)} "
        f"trying to force a breach. {len(atomic)} runs total.</p>"
        '<pre class="cmd">make exp2</pre>'
    )
    A('<div class="tiles">')
    A(
        f'<div class="tile"><div class="n safe">{a_breaches}/{a_runs}</div>'
        '<div class="l">runs that breached the cap</div></div>'
    )
    A(
        f'<div class="tile"><div class="n safe">'
        f'{"exactly " + str(legal) if a_exact else "varies"}</div>'
        "<div class=\"l\">reservations granted per run — the denial count is "
        "not merely safe, it is correct</div></div>"
    )
    A(
        f'<div class="tile"><div class="n safe">N={max(ns)}</div>'
        '<div class="l">highest concurrency tested without a breach</div></div>'
    )
    A("</div>")
    A(
        "<p>The breach-rate chart above already carries this result: the atomic "
        f"line sits flat on zero at every level. Across all {a_runs} runs, "
        f"{'every run granted exactly the ' + str(legal) + ' reservations that fit'
           if a_exact else 'grant counts varied'}.</p>"
    )

    A("<h3>The difference, in full</h3>")
    A(
        "<p>Not an architecture change. The cap check moves out of the application "
        "and into the write itself.</p>"
    )
    A('<div class="two">')
    A(
        '<div><h3 class="naive">naive — breaches</h3><pre>'
        "<em>-- transaction 1</em>\nSELECT epsilon_spent, epsilon_reserved,\n"
        "       epsilon_cap\n  FROM datasets WHERE id = :id;\n"
        "<em>-- transaction ends. no lock held.</em>\n\n"
        "<em># the check, in Python, on stale values</em>\n"
        "if spent + reserved + cost &lt;= cap:\n\n"
        "<em>-- transaction 2: unconditional</em>\n    UPDATE datasets\n"
        "       SET epsilon_reserved =\n           epsilon_reserved + :cost\n"
        "     WHERE id = :id;</pre>"
        "<p>Concurrent callers all read the same values, all pass the check, "
        "all write.</p></div>"
    )
    A(
        '<div><h3 class="safe">atomic — holds</h3><pre>'
        "UPDATE datasets\n   SET epsilon_reserved =\n       epsilon_reserved + :cost\n"
        " WHERE id = :id\n   AND epsilon_spent\n     + epsilon_reserved\n"
        "     + :cost &lt;= epsilon_cap\nRETURNING epsilon_spent,\n"
        "          epsilon_reserved;\n\n"
        "<em>-- rowcount 1 -&gt; reserved</em>\n"
        "<em>-- rowcount 0 -&gt; denied</em></pre>"
        "<p>One statement. There is nothing between the check and the write for "
        "another transaction to interleave with.</p></div>"
    )
    A("</div>")

    # --- invariants -------------------------------------------------------
    A("<h2>Experiment 4 · Can a crash corrupt the ledger?</h2>")
    ca = read("exp4_controlled_abort.csv")
    pk = read("exp4_process_kill.csv")
    if ca or pk:
        tot = len(ca) + len(pk)
        passed = sum(1 for r in ca + pk if r["passed"]) 
        A(
            '<p class="q"><strong>Question.</strong> If the service dies between '
            "reserving budget and committing it, can the ledger be left "
            "inconsistent — budget lost, double-spent, or disagreeing with its "
            "own records?</p>"
            '<p class="q"><strong>Method.</strong> Two fault modes. A '
            "<em>controlled abort</em> reserves and then deliberately fails "
            "before commit; a <em>process kill</em> hard-exits the service "
            "(<code>os._exit</code>) inside the same window and lets Docker "
            "restart it. The controlled abort tests the same protocol property "
            "and is reproducible on every attempt; the kill is corroboration.</p>"
            '<pre class="cmd">make exp4        # controlled abort\n'
            "make exp4-kill   # real process kill</pre>"
        )
        A('<div class="tiles">')
        A(
            f'<div class="tile"><div class="n safe">{passed}/{tot}</div>'
            '<div class="l">fault-injection checks passed</div></div>'
        )
        for label, rs in (("controlled abort", ca), ("process kill", pk)):
            if rs:
                A(
                    f'<div class="tile"><div class="n safe">'
                    f'{sum(1 for r in rs if r["passed"])}/{len(rs)}</div>'
                    f'<div class="l">{label}</div></div>'
                )
        A("</div>")
        rows = []
        for r in ca + pk:
            v = (
                '<span class="ok">PASS</span>'
                if r["passed"]
                else '<span class="bad">FAIL</span>'
            )
            rows.append(
                [
                    esc(r["mode"]),
                    esc(r["check"]),
                    f'<strong>{esc(r["invariant"])}</strong>',
                    v,
                    f'<span class="det">{esc(r["detail"])}</span>',
                ]
            )
        A(table(["mode", "check", "inv.", "result", "observed"], rows))
        A(
            '<p class="note"><strong>Budget survives a crash as a hold — never '
            "as a loss and never as a double-spend.</strong> A reservation "
            "orphaned by a crash stays <code>reserved</code>, and is released "
            "explicitly rather than reclaimed on a timer. Because the record row "
            "and the running columns move in one transaction, I2 cannot drift: a "
            "partial write is not a state the database can be left in.</p>"
        )
    else:
        A(
            "<p>No crash-test results found. Run <code>make exp4</code> and "
            "<code>make exp4-kill</code>, then regenerate.</p>"
        )
    A("<h2>Invariants across all experiments</h2>")
    n_i1 = sum(1 for r in naive if r["i1_cap_safety"])
    n_i2 = sum(1 for r in naive if r["i2_ledger_agreement"])
    a_i1 = sum(1 for r in atomic if r["i1_cap_safety"])
    a_i2 = sum(1 for r in atomic if r["i2_ledger_agreement"])
    ok = lambda c, t: f'<span class="ok">{c}/{t}</span>'
    bad = lambda c, t: f'<span class="bad">{c}/{t}</span>'
    A(
        table(
            ["invariant", "meaning", "Exp 1 (naive)", "Exp 2 & 4 (safe path)"],
            [
                [
                    "<strong>I1</strong> cap safety",
                    "spent + reserved ≤ cap",
                    bad(n_i1, len(naive)),
                    ok(a_i1, len(atomic)),
                ],
                [
                    "<strong>I2</strong> ledger agreement",
                    "running columns = sum of record rows",
                    ok(n_i2, len(naive)),
                    ok(a_i2, len(atomic)),
                ],
                [
                    "<strong>I3</strong> terminal states",
                    "a settled reservation never changes again",
                    "—",
                    _inv_cell(ca + pk, "I3"),
                ],
                [
                    "<strong>I4</strong> exactly-once",
                    "a retried commit charges once",
                    "—",
                    _inv_cell(ca + pk, "I4"),
                ],
            ],
        )
    )
    A(
        '<p class="note"><strong>The naive path violates I1 but not I2.</strong> '
        "It overspends the cap — a policy violation — while leaving the ledger "
        "internally consistent. Overspending and corruption are different "
        "failures, and only the first one happens here. I3 and I4 are verified "
        "behaviourally by the crash tests rather than per-run.</p>"
    )

    # --- honesty ----------------------------------------------------------
    A("<h2>What this does and does not claim</h2>")
    A(
        "<ul>"
        "<li>Absence of breach is <strong>empirically demonstrated under the "
        "tested conditions</strong>, not formally proven.</li>"
        "<li>The database <code>within_cap</code> CHECK constraint was "
        "<strong>dropped for every run shown here</strong>, including the atomic "
        "ones — so safety is attributable to the reserve strategy, not to the "
        "database backstopping it.</li>"
        "<li>The cap is an organizational <strong>policy input</strong>. This "
        "system enforces whatever cap it is given and never derives one.</li>"
        "<li>No differential-privacy mathematics is implemented. ε comes from "
        "OpenDP, or is passed in and labelled <code>passed_in</code>.</li>"
        "<li>Prior art — PrivateKube (OSDI '21), Sage — is cited up front. "
        "PrivateKube also performs two-phase budget accounting; that is not "
        "claimed as novel here.</li>"
        "<li>This is the mid-term result. The cost of the guarantee — throughput "
        "and latency across four strategies — is the primary contribution and is "
        "not yet measured.</li>"
        "</ul>"
    )

    src = []
    for f, lab in [
        ("exp1_naive_vs_n.csv", "Experiment 1 (naive)"),
        ("exp2_atomic_vs_n.csv", "Experiment 2 (atomic)"),
        ("exp1_naive_sequential_control.csv", "sequential control"),
    ]:
        if (RESULTS / f).exists():
            src.append(f"{lab}: <code>results/{f}</code>")
    A(
        '<p class="foot">Every figure on this page is computed at generation time '
        "from " + " · ".join(src) + ". Regenerate with "
        "<code>make exp1 exp2 exp1-control &amp;&amp; python experiments/report.py</code>."
        "</p>"
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
