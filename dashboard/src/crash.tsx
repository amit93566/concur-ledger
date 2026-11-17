import { useEffect, useRef, useState } from "react";
import { Dataset, Hold, Invariants, Me, RunDetail, SetupState, api, clock, fmt, isTeam, message, usePoll } from "./api";
import { Budget, RunControls, RunPicker, SetupChips, StatusPill, Waiting, Workspace, useRuns } from "./common";
import { MyShare } from "./team";

// Exp 4: teams run 3 s jobs back to back; someone kills and restarts the app
// from a terminal; the page compares the ledger it last saw with the ledger
// after the restart. Plus a controlled abort: reserve, then never commit.

export type Outage = { down: boolean; since: number | null; last: { from: number; to: number } | null };
type Snap = { at: number; d: Dataset; inv: Invariants };

const RESTART = "docker compose kill app && docker compose start app";
const KEY = (id: string) => `concur-ledger-exp4-before-${id}`;

function load(id: string): Snap | null {
  try {
    const raw = sessionStorage.getItem(KEY(id));
    return raw ? (JSON.parse(raw) as Snap) : null;
  } catch {
    return null;
  }
}

function save(id: string, snap: Snap | null) {
  try {
    if (snap) sessionStorage.setItem(KEY(id), JSON.stringify(snap));
    else sessionStorage.removeItem(KEY(id));
  } catch {
    // The comparison then lasts until the page is reloaded.
  }
}

const count = (inv: Invariants, k: string) => inv.status_counts[k] ?? 0;

function Compare({ before, run, onClear }: { before: Snap; run: RunDetail; onClear: () => void }) {
  const d = run.dataset!, inv = run.invariants!;
  const rows: [string, string, string][] = [
    ["Spent", fmt(before.d.epsilon_spent), fmt(d.epsilon_spent)],
    ["Held", fmt(before.d.epsilon_reserved), fmt(d.epsilon_reserved)],
    ["Jobs committed", String(count(before.inv, "committed")), String(count(inv, "committed"))],
    ["Jobs in flight", String(count(before.inv, "reserved")), String(count(inv, "reserved"))],
  ];
  const checks: [boolean, string][] = [
    [d.epsilon_spent >= before.d.epsilon_spent, "Committed work kept"],
    [inv.i1_cap_safety, "Cap holds (I1)"],
    [inv.i2_ledger_agreement, "Ledger agrees with its records (I2)"],
  ];
  return (
    <section className="panel">
      <h2>Before and after the restart</h2>
      <div className="split">
        <table className="compact">
          <thead><tr><th /><th className="r">Last seen · {clock(new Date(before.at).toISOString())}</th><th className="r">Now</th></tr></thead>
          <tbody>{rows.map(([k, a, b]) => <tr key={k}><td>{k}</td><td className="r mono">{a}</td><td className="r mono">{b}</td></tr>)}</tbody>
        </table>
        <ul className="checks">
          {checks.map(([ok, text]) => <li key={text} className={ok ? "ok" : "no"}>{ok ? "✓" : "✗"} {text}</li>)}
          <li className="info">{count(inv, "reserved")} in-flight jobs left as holds</li>
          <li><button className="link" onClick={onClear}>Clear comparison</button></li>
        </ul>
      </div>
    </section>
  );
}

function Holds({ me, datasetId }: { me: Me; datasetId: string }) {
  const { data, refresh } = usePoll<{ holds: (Hold & { dataset_id: string })[] }>("/holds?older_than_s=0&limit=500", 2000);
  const [error, setError] = useState<string | null>(null);
  const holds = (data?.holds ?? []).filter((h) => h.dataset_id === datasetId);
  const isAdmin = !me.rbac_enabled || me.role === "admin";
  if (!holds.length) return null;
  const settle = async (id: string, action: "charge" | "release") => {
    setError(null);
    try { await api(`/holds/${id}/resolve`, "POST", { action }); refresh(); } catch (e) { setError(message(e)); }
  };
  return (
    <section className="panel">
      <h2>Holds left behind · {holds.length}</h2>
      {error && <p className="err" role="alert">{error}</p>}
      <div className="scroll">
        <table className="compact">
          <thead><tr><th>Job</th><th>Team</th><th className="r">Held ε</th><th className="r">Age</th>{isAdmin && <th />}</tr></thead>
          <tbody>
            {holds.map((h) => (
              <tr key={h.id}>
                <td className="mono">{h.job_id}</td><td>{h.actor ?? "-"}</td>
                <td className="r mono">{fmt(h.epsilon)}</td><td className="r mono">{Math.round(h.age_s)} s</td>
                {isAdmin && (
                  <td className="acts">
                    <button onClick={() => settle(h.id, "charge")}>Charge</button>
                    <button className="secondary" onClick={() => settle(h.id, "release")}>Release</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function CrashPage({ me, setup, outage }: { me: Me; setup: SetupState | null; outage: Outage }) {
  const { runs, id, setPicked, detail } = useRuns(4);
  const running = runs.find((r) => r.status === "running") ?? null;
  const lastSeen = useRef<Snap | null>(null);
  const [before, setBefore] = useState<Snap | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => { setBefore(id ? load(id) : null); }, [id]);

  // Remember what the ledger looked like while the service was up...
  useEffect(() => {
    if (!outage.down && detail?.dataset && detail.invariants && detail.id === id) {
      lastSeen.current = { at: Date.now(), d: detail.dataset, inv: detail.invariants };
    }
  }, [detail, outage.down, id]);

  // ...and freeze it when the service goes down.
  useEffect(() => {
    if (outage.down && id && lastSeen.current) {
      save(id, lastSeen.current);
      setBefore(lastSeen.current);
    }
  }, [outage.down, id]);

  const startMode = (mode: "work" | "abort") => async () => {
    await api("/demo/runs", "POST", { exp: 4, mode });
    setPicked(null);
  };

  return (
    <>
      <header className="pagehead">
        <h1>Exp 4 · Crash</h1>
        <SetupChips setup={setup?.setup ?? null} extra={["3 s jobs"]} />
      </header>
      {setup && !setup.setup ? <Waiting /> : (
        <>
          <div className="toolbar">
            <RunControls me={me} running={running} ready={!!setup?.setup} onStart={startMode("work")} startLabel="Start work"
              alt={{ label: "Controlled abort", run: startMode("abort") }} />
            <RunPicker runs={runs} id={id} onPick={setPicked} />
          </div>
          <div className="cmd">
            <code className="mono">{RESTART}</code>
            <button className="secondary" onClick={() => { navigator.clipboard?.writeText(RESTART).then(() => setCopied(true)).catch(() => {}); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          {detail && (
            <>
              <div className="runhead">
                <StatusPill run={detail} />
                <span className="meta">{detail.mode === "abort" ? "controlled abort" : "work"} · started by {detail.started_by ?? "-"} at {clock(detail.started_at)}{detail.finished_at && detail.status === "interrupted" ? ` · service back at ${clock(detail.finished_at)}` : ""}</span>
              </div>
              {isTeam(me) && <MyShare me={me} datasetId={detail.dataset_id} />}
              <Workspace rail={detail.dataset && <Budget d={detail.dataset} inv={detail.invariants} />}>
                {before && detail.dataset && detail.invariants && !outage.down && <Compare before={before} run={detail} onClear={() => { if (id) save(id, null); setBefore(null); }} />}
                {detail.dataset_id && <Holds me={me} datasetId={detail.dataset_id} />}
              </Workspace>
            </>
          )}
          {!detail && runs.length === 0 && <p className="empty">No runs yet.</p>}
        </>
      )}
    </>
  );
}
