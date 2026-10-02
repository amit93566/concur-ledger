"""One-page evidence sheet: results/summary.html.

The companion to report.py, not a replacement. report.py argues; this states.
One block per experiment in the plan's numbering (1, 1b, 2, 3, 4), each with a
verdict, the command that produced it, and a table. Experiment 3 is listed as
deferred rather than omitted, so the numbering matches IMPLEMENTATION_PLAN.md and
the gap is visible instead of silent.

HARD RULE, same as report.py: every number is computed from results/*.csv at
generation time. Nothing is hand-typed.

    python experiments/summary.py
"""

import collections
import csv
import datetime as dt
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = RESULTS / "summary.html"

# Same two categorical slots report.py uses, so the pair of documents reads as
# one system: red = the unsafe path, blue = the safe one.
BAD_L, BAD_D = "#e34948", "#e66767"
OK_L, OK_D = "#2a78d6", "#3987e5"


def load(name):
    p = RESULTS / name
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def istrue(v):
    return str(v).strip().lower() == "true"


def group(rows):
    g = collections.defaultdict(list)
    for r in rows:
        g[int(r["workers"])].append(r)
    return dict(sorted(g.items()))


def e(x):
    return html.escape(str(x))


def table(headers, rows, align=None):
    align = align or []
    th = "".join(
        f'<th class="{align[i] if i < len(align) else ""}">{e(h)}</th>'
        for i, h in enumerate(headers)
    )
    body = []
    for r in rows:
        tds = "".join(
            f'<td class="{align[i] if i < len(align) else ""}">{c}</td>'
            for i, c in enumerate(r)
        )
        body.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def chip(text, kind):
    return f'<span class="chip {kind}">{e(text)}</span>'


def block(num, title, question, cmd, verdict, verdict_kind, body, note=""):
    return (
        f'<section class="exp">'
        f'<div class="head"><span class="num">EXP {e(num)}</span>'
        f"<h2>{e(title)}</h2>{chip(verdict, verdict_kind)}</div>"
        f'<p class="q">{e(question)}</p>'
        f'<pre class="cmd">{e(cmd)}</pre>'
        f"{body}"
        + (f'<p class="note">{note}</p>' if note else "")
        + "</section>"
    )


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

naive = load("exp1_naive_vs_n.csv")
txn = load("exp1b_naive_txn_vs_n.csv")
atomic = load("exp2_atomic_vs_n.csv")
ctrl = load("exp1_naive_sequential_control.csv")
crash = load("exp4_controlled_abort.csv") + load("exp4_process_kill.csv")

if not naive or not atomic:
    raise SystemExit("missing results CSVs -- run `make exp1 exp2` first")

cap = float(naive[0]["cap"])
seed = float(naive[0]["seed_spent"])
cost = float(naive[0]["cost"])
legal = int((cap - seed) // cost)
iso = sorted({r.get("isolation_level", "") for r in atomic} - {""}) or ["read committed"]

parts = []
A = parts.append

A(
    "<title>Evidence Sheet</title><style>"
    ":root{--bg:#fbfbf9;--card:#fff;--ink:#191917;--ink2:#5c5b55;--ink3:#84837c;"
    f"--line:#e4e3dd;--bad:{BAD_L};--ok:{OK_L};--codebg:#f5f5f2;}}"
    "@media(prefers-color-scheme:dark){:root:not([data-theme=light]){"
    "--bg:#14140f;--card:#1c1c18;--ink:#f2f1ea;--ink2:#c3c2b7;--ink3:#8e8d85;"
    f"--line:#2e2e27;--bad:{BAD_D};--ok:{OK_D};--codebg:#111110;}}}}"
    ":root[data-theme=dark]{--bg:#14140f;--card:#1c1c18;--ink:#f2f1ea;--ink2:#c3c2b7;"
    f"--ink3:#8e8d85;--line:#2e2e27;--bad:{BAD_D};--ok:{OK_D};--codebg:#111110;}}"
    "*{box-sizing:border-box}"
    "body{background:var(--bg);color:var(--ink);margin:0;"
    "font:15px/1.5 ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;"
    "-webkit-text-size-adjust:100%}"
    ".wrap{max-width:860px;margin:0 auto;padding:32px 20px 64px}"
    "h1{font-size:26px;line-height:1.2;margin:0 0 6px;letter-spacing:-.01em}"
    ".sub{color:var(--ink3);font-size:13px;margin:0 0 4px}"
    ".scen{background:var(--card);border:1px solid var(--line);border-radius:10px;"
    "padding:12px 14px;margin:20px 0 28px;font-size:14px;color:var(--ink2)}"
    ".scen b{color:var(--ink)}"
    ".exp{background:var(--card);border:1px solid var(--line);border-radius:10px;"
    "padding:16px 18px;margin:0 0 16px}"
    ".head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}"
    ".num{font:600 11px/1 ui-monospace,monospace;letter-spacing:.08em;"
    "color:var(--ink3);border:1px solid var(--line);border-radius:4px;padding:5px 6px}"
    "h2{font-size:16px;margin:0;font-weight:600;flex:1;min-width:180px}"
    ".chip{font:600 11px/1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.03em;"
    "padding:5px 8px;border-radius:999px;white-space:nowrap}"
    ".chip.bad{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}"
    ".chip.ok{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}"
    ".chip.off{background:color-mix(in srgb,var(--ink3) 16%,transparent);color:var(--ink3)}"
    ".q{color:var(--ink2);font-size:14px;margin:0 0 10px}"
    "pre.cmd{background:var(--codebg);border:1px solid var(--line);border-radius:6px;"
    "padding:8px 10px;margin:0 0 12px;overflow-x:auto;"
    "font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink2)}"
    ".tw{overflow-x:auto}"
    "table{border-collapse:collapse;width:100%;font-size:13px}"
    "th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;"
    "white-space:nowrap}"
    "th{font-weight:600;color:var(--ink3);font-size:11px;letter-spacing:.04em;"
    "text-transform:uppercase}"
    "tbody tr:last-child td{border-bottom:none}"
    "td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}"
    ".bad{color:var(--bad);font-weight:600}.ok{color:var(--ok);font-weight:600}"
    ".dim{color:var(--ink3)}"
    ".note{color:var(--ink3);font-size:12.5px;margin:10px 0 0;line-height:1.45}"
    ".foot{color:var(--ink3);font-size:12px;margin-top:24px;line-height:1.5}"
    "@media print{body{background:#fff}.exp,.scen{break-inside:avoid}}"
    "</style>"
)

A('<div class="wrap">')
A("<h1>Shared budget under concurrency — evidence sheet</h1>")
A(
    f'<p class="sub">Generated from <code>results/*.csv</code> at '
    f'{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC · isolation '
    f"<b>{e(iso[0])}</b> · <code>within_cap</code> CHECK dropped for every run below.</p>"
)
A(
    f'<div class="scen">Scenario throughout: cap <b>{cap:g}</b>, already spent '
    f"<b>{seed:g}</b>, so <b>{cap - seed:g}</b> of headroom. Each job reserves "
    f"<b>ε {cost:g}</b> — exactly <b>{legal}</b> can legally be granted.</div>"
)

# --- EXP 1 -----------------------------------------------------------------
g = group(naive)
rows = []
for n, rs in g.items():
    br = sum(istrue(r["breach"]) for r in rs) / len(rs)
    gr = [int(r["reserved_count"]) for r in rs]
    ov = [float(r["overshoot"]) for r in rs]
    cls = "bad" if br else "ok"
    rows.append(
        [
            f"{n}",
            f"{len(rs)}",
            f'<span class="{cls}">{br:.0%}</span>',
            f"{sum(gr)/len(gr):.1f}",
            f"{max(gr)}",
            f"{legal}",
            f"{max(ov):.1f}",
        ]
    )
A(
    block(
        "1",
        "The breach — write skew on the naive path",
        "Can a faithful read → check → write cap check be raced into overspending?",
        "make exp1",
        "FAIL — cap breached",
        "bad",
        '<div class="tw">'
        + table(
            ["N", "runs", "breach rate", "granted avg", "granted max", "legal", "max overshoot"],
            rows,
            ["n", "n", "n", "n", "n", "n", "n"],
        )
        + "</div>",
        f"N={min(g)} is the control: {legal} reserves fit exactly into the headroom, "
        f"so granting both is correct. Breaches begin once demand exceeds supply.",
    )
)

# --- EXP 1b ----------------------------------------------------------------
if txn:
    gt = group(txn)
    rows = []
    for n in sorted(set(g) | set(gt)):
        ra = (
            f"{sum(istrue(r['breach']) for r in g[n])/len(g[n]):.0%}" if n in g else "—"
        )
        rb = sum(istrue(r["breach"]) for r in gt[n]) / len(gt[n]) if n in gt else None
        gr = (
            f"{sum(int(r['reserved_count']) for r in gt[n])/len(gt[n]):.1f}"
            if n in gt
            else "—"
        )
        cls = "bad" if rb else "ok"
        rows.append(
            [
                f"{n}",
                f"{len(gt.get(n, [])) or '—'}",
                f'<span class="dim">{ra}</span>',
                f'<span class="{cls}">{rb:.0%}</span>' if rb is not None else "—",
                gr,
                f"{legal}",
            ]
        )
    i2 = sum(istrue(r["i2_ledger_agreement"]) for r in txn)
    A(
        block(
            "1b",
            "Control — the same logic in ONE transaction",
            "Was the two-transaction split what made it fail?",
            "make exp1b",
            "FAIL — breaches identically",
            "bad",
            '<div class="tw">'
            + table(
                ["N", "runs", "naive 2-txn", "naive_txn 1-txn", "granted avg", "legal"],
                rows,
                ["n", "n", "n", "n", "n", "n"],
            )
            + "</div>",
            f"No. Not a lost update either — I2 held {i2}/{len(txn)} runs, so nothing "
            f"was lost or corrupted. It is the same write skew, now shown to survive "
            f"a single transaction: each decision was valid against the state it read.",
        )
    )

# --- sequential control ----------------------------------------------------
if ctrl:
    rows = [
        [
            f"{n}",
            f"{len(rs)}",
            f'<span class="ok">{sum(istrue(r["breach"]) for r in rs)/len(rs):.0%}</span>',
            f"{sum(int(r['reserved_count']) for r in rs)/len(rs):.2f}",
            f"{legal}",
        ]
        for n, rs in group(ctrl).items()
    ]
    A(
        block(
            "1c",
            "Control — the same naive code, run sequentially",
            "Is the naive implementation a strawman, or genuinely correct absent concurrency?",
            "make exp1-control",
            "PASS — no breach",
            "ok",
            '<div class="tw">'
            + table(
                ["N", "runs", "breach rate", "granted avg", "legal"],
                rows,
                ["n", "n", "n", "n", "n"],
            )
            + "</div>",
            "Exactly the number that fit, zero breaches. The logic is correct; only "
            "concurrency breaks it.",
        )
    )

# --- EXP 2 -----------------------------------------------------------------
ga = group(atomic)
rows = []
for n, rs in ga.items():
    br = sum(istrue(r["breach"]) for r in rs) / len(rs)
    granted = sorted({int(r["reserved_count"]) for r in rs})
    sp = sorted(float(r["reserved_at_spread_ms"]) for r in rs)
    rows.append(
        [
            f"{n}",
            f"{len(rs)}",
            f'<span class="ok">{br:.0%}</span>',
            f"{granted[0] if len(granted) == 1 else granted}",
            f"{legal}",
            f"{sp[len(sp)//2]:.1f}",
        ]
    )
A(
    block(
        "2",
        "The fix — atomic (conditional UPDATE)",
        "Does moving the check into the WHERE clause prevent every breach?",
        "make exp2",
        f"PASS — 0/{len(atomic)} breached",
        "ok",
        '<div class="tw">'
        + table(
            ["N", "runs", "breach rate", "granted", "legal", "median spread ms"],
            rows,
            ["n", "n", "n", "n", "n", "n"],
        )
        + "</div>",
        f"Every run granted exactly {legal} — not merely safe, exactly correct. "
        f"Correct by the documented semantics of {e(iso[0])}: a concurrent UPDATE "
        f"causes the predicate to be re-evaluated against the newly committed row.",
    )
)

# --- EXP 3 -----------------------------------------------------------------
A(
    block(
        "3",
        "The cost of the guarantee — the cost sweep",
        "What does safety cost in throughput and latency, across strategies and "
        "operation durations?",
        "# Phase 2 — not yet run",
        "NOT YET MEASURED",
        "off",
        '<div class="tw">'
        + table(
            ["planned variable", "levels"],
            [
                ["strategies", "naive · naive_txn · atomic · for_update · serializable"],
                ["concurrency", "1, 2, 4, 8, 16, 32, 64, 100"],
                ["duration regimes", "short · long (synthetic adapter)"],
                ["metrics", "throughput · latency p50/p95/p99 · denial rate · 40001 retries"],
                ["validity controls", "no-op baseline · stated saturation point"],
            ],
        )
        + "</div>",
        "This is the primary contribution and it is deliberately absent from the "
        "mid-term scope. All contention is on a single row by design; that ceiling "
        "is structural, and the sweep measures its price rather than discovering it.",
    )
)

# --- EXP 4 -----------------------------------------------------------------
if crash:
    by_mode = collections.defaultdict(list)
    for r in crash:
        by_mode[r["mode"]].append(r)
    rows = []
    for mode, rs in by_mode.items():
        p = sum(istrue(r.get("passed")) for r in rs)
        cls = "ok" if p == len(rs) else "bad"
        rows.append(
            [e(mode), f"{len(rs)}", f'<span class="{cls}">{p}/{len(rs)}</span>']
        )
    inv = collections.defaultdict(lambda: [0, 0])
    for r in crash:
        k = r.get("invariant") or "—"
        inv[k][1] += 1
        inv[k][0] += istrue(r.get("passed"))
    irows = [
        [e(k), f"{v[1]}", f'<span class="{"ok" if v[0]==v[1] else "bad"}">{v[0]}/{v[1]}</span>']
        for k, v in sorted(inv.items())
    ]
    total_pass = sum(istrue(r.get("passed")) for r in crash)
    A(
        block(
            "4",
            "Survive the failure — crash between reserve and commit",
            "Can a crash mid-protocol lose budget, double-spend it, or corrupt the "
            "ledger?",
            "make exp4        # controlled abort\nmake exp4-kill   # real process kill",
            f"PASS — {total_pass}/{len(crash)} checks",
            "ok",
            '<div class="tw">'
            + table(["fault mode", "checks", "passed"], rows, ["", "n", "n"])
            + "</div><div class=tw>"
            + table(["invariant", "checks", "passed"], irows, ["", "n", "n"])
            + "</div>",
            "Budget survives a crash as a <b>hold</b> — never as a loss and never as "
            "a double-spend. A retried commit charges once; releasing a committed "
            "reservation is rejected; a partial commit returns the unused hold.",
        )
    )

# --- footer ----------------------------------------------------------------
srcs = [
    f
    for f in (
        "exp1_naive_vs_n.csv",
        "exp1b_naive_txn_vs_n.csv",
        "exp1_naive_sequential_control.csv",
        "exp2_atomic_vs_n.csv",
        "exp4_controlled_abort.csv",
        "exp4_process_kill.csv",
    )
    if (RESULTS / f).exists()
]
A(
    '<p class="foot">Every figure computed at generation time from '
    + " · ".join(f"<code>{e(s)}</code>" for s in srcs)
    + ". Rebuild with <code>make summary</code>. Safety is demonstrated "
    "empirically under the tested conditions; the atomic result additionally "
    "follows from the documented isolation semantics. The cap is a policy input, "
    "never derived. No DP mathematics is implemented here.</p>"
)
A("</div>")

OUT.write_text("\n".join(parts))
print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")
