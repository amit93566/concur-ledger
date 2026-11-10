import { ReactNode, useEffect, useState } from "react";
import { Dataset, Invariants, Me, api, call, fmt } from "./api";

// The live demo: three acts that drive the same API the harnesses use. Like the
// terminal demo mode (experiments/harness_asyncio.py), this page computes
// nothing of its own. Every number on it is a server response. It illustrates;
// the measured evidence is the harness CSVs and the Results tab.

const CAP = 100;
const COST = 2; // Act 2's passed-in cost, the same as Experiments 1 and 2
const BURST = 5; // the browser opens at most 6 connections per host, so 5 stay simultaneous
const SPEC = { kind: "count", scale: 0.5, n_rows: 1000 };

type Inv = Invariants & { epsilon_cap: number; epsilon_spent: number; epsilon_reserved: number; total: number; overshoot: number };
type Snap = { d: Dataset; inv: Inv };

const stamp = () => new Date().toTimeString().slice(0, 8).replace(/:/g, "");

function detailOf(status: number, body: unknown) {
  const d = (body as { detail?: unknown })?.detail;
  const text = typeof d === "string" ? d : JSON.stringify(d ?? body);
  return status === 403 || status === 401 ? `Blocked by RBAC (HTTP ${status}): ${text}` : `HTTP ${status}: ${text}`;
}

/** A call that must succeed; anything else is shown as the act's error. */
async function must<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await call<T>(path, method, body);
  if (r.status >= 400) throw new Error(detailOf(r.status, r.body));
  return r.body;
}

async function snapshot(id: string): Promise<Snap> {
  const [d, inv] = await Promise.all([api<Dataset>(`/datasets/${id}`), api<Inv>(`/datasets/${id}/invariants`)]);
  return { d, inv };
}

/**
 * Creates a dataset at the cap, then seeds `spent` the way the harnesses do: as
 * one committed record. Setting the running column alone would leave nothing
 * behind it, and I2 would rightly report the ledger disagreeing.
 */
async function createDataset(name: string, spent: number): Promise<Dataset> {
  const d = await must<Dataset>("/datasets", "POST", { name: `${name}-${stamp()}`, epsilon_cap: CAP });
  if (spent <= 0) return d;
  return must<Dataset>(`/datasets/${d.id}/seed`, "POST", { epsilon_spent: spent, epsilon_reserved: 0, delete_records: true });
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// ---------------------------------------------------------------------------
// budget meters
// ---------------------------------------------------------------------------

/** Spent, held and past-cap segments drawn over the range [lo, hi]. */
function Meter({ d, lo, hi, label }: { d: Dataset; lo: number; hi: number; label: string }) {
  const span = hi - lo;
  const pc = (v: number) => `${(Math.max(0, v) / span) * 100}%`;
  const within = Math.min(d.total, d.epsilon_cap);
  const spentEnd = Math.min(d.epsilon_spent, d.epsilon_cap);
  const over = d.total > d.epsilon_cap;
  // When the cap sits near the right end, its label would overprint the end
  // label: drop the end label and end the cap label at the marker instead.
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

/** The full scale, plus a zoom on the last 10 below the cap once the budget gets there. */
function Budget({ s }: { s: Snap }) {
  const { d, inv } = s;
  const cap = d.epsilon_cap;
  const near = d.total >= cap - 10;
  const over = d.total > cap;
  return (
    <div className="budget">
      <p className="dsname">dataset <b className="mono">{d.name}</b></p>
      <p className="mono nums-line">
        spent {fmt(d.epsilon_spent)} + held {fmt(d.epsilon_reserved)} = <b>{fmt(d.total)}</b> / cap {fmt(cap)}{" "}
        {over ? <span className="pill bad">Cap breached by {fmt(d.total - cap)}</span> : <span className="pill good">Within cap</span>}
      </p>
      <Meter d={d} lo={0} hi={Math.max(cap, d.total)} label="Full scale" />
      {near && <Meter d={d} lo={cap - 10} hi={Math.max(cap + 10, Math.ceil(d.total) + 2)} label={`Zoomed: ${fmt(cap - 10)} up`} />}
      <p>
        <span className={`pill ${inv.i1_cap_safety ? "good" : "bad"}`}>I1 cap {inv.i1_cap_safety ? "holds" : "broken"}</span>{" "}
        <span className={`pill ${inv.i2_ledger_agreement ? "good" : "bad"}`}>I2 ledger {inv.i2_ledger_agreement ? "agrees" : "disagrees"}</span>{" "}
        {Object.entries(inv.status_counts).map(([k, v]) => <span className="pill" key={k}>{k} {v}</span>)}
      </p>
    </div>
  );
}

function Fallback({ children }: { children: ReactNode }) {
  return <p className="fallback">If this fails, run it from the terminal: <code className="mono">{children}</code></p>;
}

function Err({ text }: { text: string | null }) {
  return text ? <p className="err" role="alert">{text}</p> : null;
}

// ---------------------------------------------------------------------------
// Act 1: a real OpenDP query
// ---------------------------------------------------------------------------

type RunRow = { n: number; outcome: string; result?: unknown; charged?: number; source?: string; spentAfter?: number; detail?: string };

function ActOpenDP() {
  const [ds, setDs] = useState<Dataset | null>(null);
  const [price, setPrice] = useState<number | null>(null);
  const [snap, setSnap] = useState<Snap | null>(null);
  const [rows, setRows] = useState<RunRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const prepare = async () => {
    setBusy(true); setError(null); setRows([]); setSnap(null);
    try {
      const priced = await must<{ epsilon: number }>("/adapters/epsilon", "POST", { adapter: "opendp", spec: SPEC });
      const d = await createDataset("demo-opendp", 96);
      setPrice(priced.epsilon); setDs(d); setSnap(await snapshot(d.id));
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  const run = async () => {
    if (!ds) return;
    const n = rows.length + 1;
    setBusy(true); setError(null);
    try {
      const r = await call<Record<string, unknown>>(`/datasets/${ds.id}/run`, "POST", {
        job_id: `dp-${n}`, idempotency_key: `${ds.id}-dp-${n}`, strategy: "atomic", adapter: "opendp", spec: SPEC,
      });
      const b = r.body;
      const row: RunRow =
        r.status === 200
          ? { n, outcome: "committed", result: b.result, charged: b.epsilon_committed as number, source: b.epsilon_source as string, spentAfter: (b.dataset as Dataset).epsilon_spent }
          : r.status === 429
            ? { n, outcome: "denied", charged: 0, detail: `ε ${fmt(b.epsilon_cost as number)} would exceed the cap` }
            : { n, outcome: "error", detail: detailOf(r.status, b) };
      if (row.outcome === "error") setError(row.detail ?? null);
      else setRows((prev) => [...prev, row]);
      setSnap(await snapshot(ds.id));
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  return (
    <section className="act">
      <h2><span className="step">Act 1</span> Where ε comes from: a real OpenDP query</h2>
      <p className="guide">OpenDP prices the query. The ledger reserves exactly that price, runs the query, and commits it. It never computes privacy itself.</p>
      <div className="tools">
        <button disabled={busy} onClick={prepare}>{ds ? "Start again" : "Prepare: cap 100, spent 96"}</button>
        <button disabled={busy || !ds} onClick={run}>Run query {rows.length + 1}</button>
      </div>
      <Err text={error} />
      {price !== null && (
        <p className="mono callout">
          spec: noisy count, Laplace scale {SPEC.scale}, {SPEC.n_rows} rows → OpenDP price <b>ε = {fmt(price)}</b> (the same every time for this spec)
        </p>
      )}
      {snap && <Budget s={snap} />}
      {rows.length > 0 && (
        <div className="scroll">
          <table>
            <thead><tr><th>Query</th><th>Outcome</th><th className="r">Noisy count</th><th className="r">ε charged</th><th>ε source</th><th className="r">Spent after</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.n}>
                  <td className="mono">{r.n}</td>
                  <td>{r.outcome === "committed" ? <span className="pill good">COMMITTED</span> : <span className="pill bad">DENIED</span>} {r.detail}</td>
                  <td className="r mono">{r.result === undefined ? "-" : typeof r.result === "number" ? fmt(r.result) : String(r.result)}</td>
                  <td className="r mono">{r.charged === undefined ? "-" : fmt(r.charged)}</td>
                  <td className="mono">{r.source ?? "-"}</td>
                  <td className="r mono">{r.spentAfter === undefined ? "-" : fmt(r.spentAfter)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Fallback>make opendp</Fallback>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Act 2: breach vs safe
// ---------------------------------------------------------------------------

type Shot = {
  worker: number; outcome: string; receivedAt: number | null;
  observedSpent: number | null; observedReserved: number | null; detail?: string;
};
type Side = { ds: Dataset; shots: Shot[] | null; snap: Snap | null; spreadMs: number | null };

const PRESETS = {
  near: { spent: 96, label: "Near the cap: spent 96 of 100" },
  far: { spent: 0, label: "Far from the cap: spent 0 of 100" },
} as const;
type Preset = keyof typeof PRESETS;
const STRATEGIES = ["naive", "atomic"] as const;
type Strategy = (typeof STRATEGIES)[number];

function traceLine(strategy: Strategy, s: Shot) {
  if (s.outcome === "error") return s.detail ?? "error";
  const os = s.observedSpent, or = s.observedReserved;
  if (strategy === "naive") {
    // The pre-write read, so the projection is the (stale) decision it made.
    if (os === null || or === null) return "read(-)";
    const projected = os + or + COST;
    return `read(spent ${fmt(os)}, reserved ${fmt(or)}) → ${fmt(projected)} ≤ ${CAP} ${projected <= CAP ? "✓" : "✗"}`;
  }
  // atomic returns the post-update row; when the predicate is false there is none.
  if (os === null || or === null) return "predicate false at the database";
  return `ledger now spent ${fmt(os)} + reserved ${fmt(or)} = ${fmt(os + or)} ≤ ${CAP}`;
}

function SideView({ strategy, side, legal, onFire, busy }: { strategy: Strategy; side: Side; legal: number; onFire: () => void; busy: boolean }) {
  const ordered = [...(side.shots ?? [])].sort((a, b) => (a.receivedAt ?? 0) - (b.receivedAt ?? 0));
  const granted = ordered.filter((s) => s.outcome === "reserved").length;
  const denied = ordered.filter((s) => s.outcome === "denied").length;
  const firstRead = ordered.find((s) => s.outcome === "reserved");
  return (
    <div className="side">
      <h3>{strategy === "naive" ? "naive (read, check, write)" : "atomic (one conditional UPDATE)"}</h3>
      <button disabled={busy || side.shots !== null} onClick={onFire}>Fire {BURST} at once: {strategy}</button>
      {side.snap && <Budget s={side.snap} />}
      {side.shots && (
        <>
          <p className="mono">
            granted <b>{granted}</b> of {BURST} (only {legal} fit) · denied {denied}
            {side.spreadMs !== null && <> · arrived within <b>{fmt(Math.round(side.spreadMs * 10) / 10)} ms</b></>}
          </p>
          <ol className="trace mono">
            {ordered.map((s) => {
              const stale = strategy === "naive" && s.outcome === "reserved" && firstRead && s !== firstRead &&
                s.observedSpent === firstRead.observedSpent && s.observedReserved === firstRead.observedReserved;
              return (
                <li key={s.worker}>
                  w{s.worker} {traceLine(strategy, s)} → <b className={s.outcome === "reserved" ? "" : "badtext"}>{s.outcome === "reserved" ? "RESERVED" : s.outcome.toUpperCase()}</b>
                  {stale && <span className="stale"> ← same stale read</span>}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </div>
  );
}

function ActBreach() {
  const [preset, setPreset] = useState<Preset>("near");
  const [check, setCheck] = useState<boolean | null>(null);
  const [sides, setSides] = useState<Record<Strategy, Side> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spent = PRESETS[preset].spent;
  const legal = Math.floor((CAP - spent) / COST);

  useEffect(() => {
    api<{ within_cap_constraint_present: boolean }>("/config").then((c) => setCheck(c.within_cap_constraint_present)).catch(() => setCheck(null));
  }, []);

  const prepare = async () => {
    setBusy(true); setError(null); setSides(null);
    try {
      // Off, so any safety shown comes from the strategy, not the database.
      // It is not switched back on here: re-adding it fails while any dataset
      // is past its cap, which is exactly what this act creates.
      const c = await must<{ within_cap_constraint_present: boolean }>("/admin/db-constraint", "POST", { enabled: false });
      setCheck(c.within_cap_constraint_present);
      const tag = preset === "near" ? "near" : "far";
      const [n, a] = [await createDataset(`demo-naive-${tag}`, spent), await createDataset(`demo-atomic-${tag}`, spent)];
      setSides({
        naive: { ds: n, shots: null, snap: await snapshot(n.id), spreadMs: null },
        atomic: { ds: a, shots: null, snap: await snapshot(a.id), spreadMs: null },
      });
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  const fire = async (strategy: Strategy) => {
    if (!sides) return;
    const id = sides[strategy].ds.id;
    setBusy(true); setError(null);
    try {
      // All requests start before any is awaited, so they are in flight together.
      const pending = Array.from({ length: BURST }, (_, i) =>
        call<Record<string, unknown>>(`/datasets/${id}/reserve`, "POST", {
          job_id: `w${i + 1}`, idempotency_key: `${id}-w${i + 1}`, epsilon_cost: COST, strategy,
        }),
      );
      const results = await Promise.all(pending);
      const shots: Shot[] = results.map((r, i) => {
        const b = r.body;
        const num = (v: unknown) => (typeof v === "number" ? v : null);
        const ok = r.status === 200 || r.status === 429;
        return {
          worker: i + 1,
          outcome: ok ? String(b.status) : "error",
          receivedAt: num(b.received_at),
          observedSpent: num(b.observed_spent),
          observedReserved: num(b.observed_reserved),
          detail: ok ? undefined : detailOf(r.status, b),
        };
      });
      const errors = shots.filter((s) => s.outcome === "error");
      if (errors.length) setError(errors[0].detail ?? "a request failed");
      const stamps = shots.map((s) => s.receivedAt).filter((v): v is number => v !== null);
      const spreadMs = stamps.length > 1 ? (Math.max(...stamps) - Math.min(...stamps)) * 1000 : null;
      const snap = await snapshot(id);
      setSides((prev) => prev && { ...prev, [strategy]: { ...prev[strategy], shots, snap, spreadMs } });
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  return (
    <section className="act">
      <h2><span className="step">Act 2</span> Breach vs safe: the same load on two strategies</h2>
      <p className="guide">
        Two identical datasets, {BURST} jobs at ε {COST} each, all sent at once. What decides a breach is not the size of the cap
        but how many requests arrive together compared with how many still fit.
      </p>
      <div className="tools">
        <label htmlFor="preset">Starting point</label>
        <select id="preset" value={preset} disabled={busy} onChange={(e) => { setPreset(e.target.value as Preset); setSides(null); }}>
          {(Object.keys(PRESETS) as Preset[]).map((p) => <option key={p} value={p}>{PRESETS[p].label}</option>)}
        </select>
        <button disabled={busy} onClick={prepare}>{sides ? "Start again" : "Prepare both datasets"}</button>
        <span className={`pill ${check === false ? "warn" : ""}`}>within_cap CHECK: {check === null ? "unknown" : check ? "ON" : "OFF"}</span>
      </div>
      <p className="mono callout">cap {CAP} │ spent {spent} │ headroom {CAP - spent} │ only <b>{legal}</b> can legally be granted</p>
      {spent > 0 && <p className="fallback">The {spent} already spent is seeded as one committed record, as the harness does, so it shows as "committed 1".</p>}
      <Err text={error} />
      {sides && (
        <div className="sides">
          {STRATEGIES.map((s) => <SideView key={s} strategy={s} side={sides[s]} legal={legal} busy={busy} onFire={() => fire(s)} />)}
        </div>
      )}
      <Fallback>.venv/bin/python experiments/harness_asyncio.py --strategy naive --demo --cap 100 --seed-spent {spent} --cost 2 --workers 5 --runs 1</Fallback>
      <p className="fallback">Then the same with <code className="mono">--strategy atomic</code>. At cap 10: <code className="mono">make demo-breach</code>, <code className="mono">make demo-safe</code>.</p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Act 3: a crash between reserve and commit
// ---------------------------------------------------------------------------

function ActCrash({ onOpenHolds }: { onOpenHolds: (dataset: string) => void }) {
  const [ds, setDs] = useState<Dataset | null>(null);
  const [snap, setSnap] = useState<Snap | null>(null);
  const [aborts, setAborts] = useState<{ job: string; status: string; id: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Kept live, so a hold settled on the Stale holds page shows up here.
  useEffect(() => {
    if (!ds) return;
    const id = setInterval(() => { snapshot(ds.id).then(setSnap).catch(() => {}); }, 2000);
    return () => clearInterval(id);
  }, [ds]);

  const prepare = async () => {
    setBusy(true); setError(null); setAborts([]); setSnap(null);
    try {
      const d = await createDataset("demo-crash", 0);
      setDs(d); setSnap(await snapshot(d.id));
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  const abort = async () => {
    if (!ds) return;
    const n = aborts.length + 1;
    setBusy(true); setError(null);
    try {
      const b = await must<{ reservation_id: string; status: string }>(`/debug/reserve-and-abort/${ds.id}`, "POST", {
        job_id: `abort-${n}`, idempotency_key: `${ds.id}-abort-${n}`, epsilon_cost: COST, strategy: "atomic",
      });
      setAborts((prev) => [...prev, { job: `abort-${n}`, status: b.status, id: b.reservation_id }]);
      setSnap(await snapshot(ds.id));
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  return (
    <section className="act">
      <h2><span className="step">Act 3</span> A crash between reserve and commit</h2>
      <p className="guide">
        The service reserves ε {COST}, then fails before it commits, exactly as a crash in that window would leave things. The hold
        still counts against the cap, so the budget is neither lost nor spent twice.
      </p>
      <div className="tools">
        <button disabled={busy} onClick={prepare}>{ds ? "Start again" : "Prepare: cap 100, spent 0"}</button>
        <button disabled={busy || !ds} onClick={abort}>Reserve, then crash before commit</button>
        <button className="secondary" disabled={aborts.length === 0 || !ds} onClick={() => ds && onOpenHolds(ds.name)}>Open stale holds</button>
      </div>
      <Err text={error} />
      {snap && <Budget s={snap} />}
      {aborts.length > 0 && (
        <ol className="trace mono">
          {aborts.map((a) => <li key={a.id}>{a.job} → status <b>{a.status}</b>, reservation {a.id.slice(0, 8)}, never committed</li>)}
        </ol>
      )}
      <p className="fallback">
        A real process kill cannot run from this page: it stops the server that serves it. Run <code className="mono">make demo-crash</code> in
        the terminal for the abort and one real kill. That is also the fallback for this act.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------

export function DemoPage({ me, onOpenHolds }: { me: Me; onOpenHolds: (dataset: string) => void }) {
  return (
    <>
      <div className="head">
        <div>
          <h1>Live demo</h1>
          <p className="sub">
            Each button calls the same API the harnesses use, and every number shown is a server response. This page illustrates;
            the measured results are on the Results tab and in results/*.csv.
          </p>
        </div>
      </div>
      {me.rbac_enabled && me.role !== "admin" && (
        <p className="guide">
          Signed in as <b>{me.role}</b>. Preparing a dataset needs the admin role, so the server will refuse these buttons. That refusal is the
          access-control demo.
        </p>
      )}
      <ActOpenDP />
      <ActBreach />
      <ActCrash onOpenHolds={onOpenHolds} />
    </>
  );
}
