import { ReactNode, useState } from "react";
import { Dataset, Invariants, Me, Run, RunDetail, Setup, api, clock, fmt, message, usePoll } from "./api";

// Shared pieces of the console. Every number drawn here is a server response.

export const STRATEGY_LABEL: Record<string, string> = {
  naive: "naive",
  atomic: "atomic",
  for_update: "for_update",
  serializable: "serializable",
};

/** True when this login may start runs: a team (analyst), or anyone with RBAC off. */
export const canStart = (me: Me) => !me.rbac_enabled || me.role === "analyst";

export function Chips({ items }: { items: (string | null | false)[] }) {
  return <p className="chips">{items.filter(Boolean).map((t) => <span className="chip mono" key={String(t)}>{t}</span>)}</p>;
}

export function SetupChips({ setup, extra = [] }: { setup: Setup | Run["params"] | null; extra?: (string | false)[] }) {
  if (!setup) return null;
  const teams = "team_names" in setup ? (setup as Setup).teams : (setup.teams as string[] | undefined)?.length;
  return <Chips items={[`cap ${fmt(Number(setup.epsilon_cap))}`, `ε ${fmt(Number(setup.epsilon))} per query`, teams !== undefined && `${teams} teams`, ...extra]} />;
}

// --- budget meters ---------------------------------------------------------

function Meter({ d, lo, hi, label }: { d: Dataset; lo: number; hi: number; label: string }) {
  const span = hi - lo;
  const pc = (v: number) => `${(Math.max(0, v) / span) * 100}%`;
  const within = Math.min(d.total, d.epsilon_cap);
  const spentEnd = Math.min(d.epsilon_spent, d.epsilon_cap);
  const over = d.total > d.epsilon_cap;
  const capNearEnd = (hi - d.epsilon_cap) / span < 0.15;
  return (
    <div className="meter">
      <span className="mlabel">{label}</span>
      <div>
        <div className="bar" role="img" aria-label={`${label}: ${fmt(d.total)} of ${fmt(d.epsilon_cap)} used`}>
          <i className="spent" style={{ width: pc(spentEnd - lo) }} />
          <i className="held" style={{ width: pc(within - Math.max(spentEnd, lo)) }} />
          {over && <i className="over" style={{ width: pc(Math.min(d.total, hi) - d.epsilon_cap) }} />}
          <b className="capmark" style={{ left: pc(d.epsilon_cap - lo) }} />
        </div>
        <div className="ticks mono">
          <span>{fmt(lo)}</span>
          <span className={capNearEnd ? "capend" : undefined} style={{ left: pc(d.epsilon_cap - lo) }}>cap {fmt(d.epsilon_cap)}</span>
          {!capNearEnd && <span className="end">{fmt(hi)}</span>}
        </div>
      </div>
    </div>
  );
}

/** Full scale, plus a zoom on the last tenth below the cap once the budget gets there. */
export function Budget({ d, inv }: { d: Dataset; inv: Invariants | null }) {
  const cap = d.epsilon_cap;
  const zoomFrom = cap * 0.9;
  const over = d.total > cap;
  return (
    <div className="budget">
      <p className="mono nums-line">
        <span className="dsname">{d.name}</span> spent {fmt(d.epsilon_spent)} + held {fmt(d.epsilon_reserved)} = <b>{fmt(d.total)}</b> / {fmt(cap)}{" "}
        {over ? <span className="pill bad">Breached by {fmt(d.total - cap)}</span> : <span className="pill good">Within cap</span>}
      </p>
      <Meter d={d} lo={0} hi={Math.max(cap, d.total)} label="Budget" />
      {d.total >= zoomFrom && <Meter d={d} lo={zoomFrom} hi={Math.ceil(Math.max(cap + (cap - zoomFrom), d.total * 1.02))} label="Zoom" />}
      {inv && (
        <p className="pills">
          <span className={`pill ${inv.i1_cap_safety ? "good" : "bad"}`}>I1 cap {inv.i1_cap_safety ? "holds" : "broken"}</span>
          <span className={`pill ${inv.i2_ledger_agreement ? "good" : "bad"}`}>I2 ledger {inv.i2_ledger_agreement ? "agrees" : "disagrees"}</span>
          {Object.entries(inv.status_counts).map(([k, v]) => <span className="pill" key={k}>{k} {v}</span>)}
        </p>
      )}
    </div>
  );
}

// --- runs ------------------------------------------------------------------

export function StatusPill({ run }: { run: Run & { progress?: unknown[] } }) {
  const n = run.progress?.length ?? 0;
  const map: Record<string, [string, string]> = {
    running: ["warn", run.mode === "rounds" ? `Running · round ${n + 1}` : "Running"],
    breached: ["bad", run.detail ? run.detail.replace(/^cap b/, "B") : "Breached"],
    exhausted: ["good", run.mode === "rounds" ? "Budget used up · cap held" : "Budget used up"],
    done: ["good", "Done"],
    stopped: ["", "Stopped"],
    interrupted: ["bad", "Interrupted by a restart"],
    failed: ["bad", `Failed${run.detail ? `: ${run.detail}` : ""}`],
  };
  const [tone, text] = map[run.status] ?? ["", run.status];
  return <span className={`pill big ${tone}`}>{text}</span>;
}

/** The latest runs of one experiment, and which one is shown. */
export function useRuns(exp: number) {
  const runs = usePoll<Run[]>(`/demo/runs?exp=${exp}&limit=8`, 1000);
  const [picked, setPicked] = useState<string | null>(null);
  const id = picked ?? runs.data?.[0]?.id ?? null;
  const detail = usePoll<RunDetail>(id ? `/demo/runs/${id}` : null, 700);
  return { runs: runs.data ?? [], id, setPicked, detail: detail.data, refresh: runs.refresh };
}

export function RunPicker({ runs, id, onPick }: { runs: Run[]; id: string | null; onPick: (id: string | null) => void }) {
  if (runs.length < 2) return null;
  return (
    <select aria-label="Run" value={id ?? ""} onChange={(e) => onPick(e.target.value === runs[0].id ? null : e.target.value)}>
      {runs.map((r, i) => (
        <option key={r.id} value={r.id}>
          {i === 0 ? "Latest: " : ""}{clock(r.started_at)} · {r.strategy !== "all" ? `${r.strategy} · ` : ""}{r.mode !== "rounds" && r.mode !== "sweep" ? `${r.mode} · ` : ""}{r.started_by ?? "-"} · {r.status}
        </option>
      ))}
    </select>
  );
}

/** Start/stop for teams; a "Watching" badge for everyone else. */
export function RunControls({ me, running, ready, onStart, children, startLabel = "Start", alt }: {
  me: Me; running: Run | null; ready: boolean; onStart: () => Promise<unknown>; children?: ReactNode; startLabel?: string;
  alt?: { label: string; run: () => Promise<unknown> };
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  if (!canStart(me)) {
    return <div className="controls"><span className="pill watch">{me.role === "admin" ? "Admin · watching" : `${me.role} · read only`}</span></div>;
  }
  return (
    <>
      <div className="controls">
        {children}
        <button disabled={busy || !ready || !!running} onClick={() => go(onStart)}>{startLabel}</button>
        {alt && <button className="secondary" disabled={busy || !ready || !!running} onClick={() => go(alt.run)}>{alt.label}</button>}
        {running && <button className="secondary" disabled={busy} onClick={() => go(() => api(`/demo/runs/${running.id}/stop`, "POST"))}>Stop</button>}
      </div>
      {error && <p className="err" role="alert">{error}</p>}
    </>
  );
}

export function Waiting() {
  return <p className="empty">Waiting for the admin to set up.</p>;
}

// --- chart -------------------------------------------------------------------

export type Series = Record<string, { n: number; v: number | null }[]>;

/** Lines per strategy against N (log scale), drawn from given values only. */
export function LineChart({ series, title, unit }: { series: Series; title: string; unit: string }) {
  const [hover, setHover] = useState<{ s: string; n: number; v: number; x: number; y: number } | null>(null);
  const W = 460, H = 240, L = 50, R = 12, T = 12, B = 34;
  const pts = Object.values(series).flat().filter((p) => p.v !== null) as { n: number; v: number }[];
  if (!pts.length) return null;
  const ns = [...new Set(pts.map((p) => p.n))].sort((a, b) => a - b);
  const top = Math.max(...pts.map((p) => p.v)) * 1.1 || 1;
  const x = (n: number) => (ns.length < 2 ? (L + W - R) / 2 : L + (Math.log(n / ns[0]) / Math.log(ns[ns.length - 1] / ns[0])) * (W - L - R));
  const y = (v: number) => H - B - (v / top) * (H - T - B);
  const step = Math.pow(10, Math.floor(Math.log10(top)));
  const unitStep = top / step > 5 ? step * 2 : top / step > 2 ? step : step / 2;
  const ticks = Array.from({ length: Math.floor(top / unitStep) + 1 }, (_, i) => i * unitStep);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} against N`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 7} y={y(t) + 4} textAnchor="end" className="tick">{fmt(t)}</text>
          </g>
        ))}
        {ns.map((n) => <text key={n} x={x(n)} y={H - B + 17} textAnchor="middle" className="tick">{n}</text>)}
        <text x={(L + W - R) / 2} y={H - 3} textAnchor="middle" className="tick">concurrent workers N</text>
        {Object.entries(series).map(([s, raw]) => {
          const line = raw.filter((p) => p.v !== null) as { n: number; v: number }[];
          return (
            <g key={s}>
              <polyline points={line.map((p) => `${x(p.n)},${y(p.v)}`).join(" ")} fill="none" strokeWidth={2} strokeDasharray={s === "naive" ? "2 4" : undefined} className={`line-${s}`} />
              {line.map((p) => (
                <g key={p.n}>
                  <circle cx={x(p.n)} cy={y(p.v)} r={4} className={`dot-${s}`} />
                  <circle cx={x(p.n)} cy={y(p.v)} r={11} fill="transparent" onMouseEnter={() => setHover({ s, n: p.n, v: p.v, x: x(p.n), y: y(p.v) })} />
                </g>
              ))}
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="tip" style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }}>
          <b>{STRATEGY_LABEL[hover.s] ?? hover.s}, N={hover.n}</b><br />{fmt(hover.v)} {unit}
        </div>
      )}
    </figure>
  );
}

export function Legend({ strategies }: { strategies: string[] }) {
  return (
    <ul className="key">
      {strategies.map((s) => <li key={s}><i className={`sw sw-${s}`} />{STRATEGY_LABEL[s] ?? s}</li>)}
    </ul>
  );
}
