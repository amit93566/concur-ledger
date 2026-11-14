import { useState } from "react";
import { AccessRow, AuditRow, Dataset, Me, Party, Verify, api, clock, message, usePoll } from "./api";

// Who accessed what, and when (access_log), and the hash-chained record of
// every change to the ledger (audit_log).

const ACTIONS: Record<string, string> = {
  "GET /whoami": "signed in",
  "POST /demo/setup": "set up experiments",
  "POST /demo/runs": "started a run",
  "POST /demo/runs/{run_id}/stop": "stopped a run",
  "POST /datasets/{dataset_id}/reserve": "reserved ε",
  "POST /reservations/{reservation_id}/commit": "committed",
  "POST /reservations/{reservation_id}/release": "released",
  "POST /datasets/{dataset_id}/run": "ran a job",
  "POST /holds/{reservation_id}/resolve": "settled a hold",
  "POST /audit/verify": "verified the audit chain",
  "POST /datasets": "created a dataset",
  "POST /admin/db-constraint": "changed the CHECK",
};

function action(r: AccessRow) {
  return ACTIONS[`${r.method} ${r.route ?? r.path}`] ?? `${r.method} ${r.route ?? r.path}`;
}

function result(status: number) {
  if (status === 401 || status === 403) return <span className="pill bad">blocked {status}</span>;
  if (status === 429) return <span className="pill">denied</span>;
  if (status >= 500) return <span className="pill bad">error {status}</span>;
  if (status >= 400) return <span className="pill warn">{status}</span>;
  return <span className="pill good">ok</span>;
}

export function Access({ own = false }: { own?: boolean }) {
  const [who, setWho] = useState<string | null>(null);
  const { data, error } = usePoll<{ enabled: boolean; parties: Party[]; rows: AccessRow[] }>(
    `/access-log?limit=300${who ? `&actor=${encodeURIComponent(who)}` : ""}`, 2000,
  );
  const ds = usePoll<Dataset[]>("/datasets", 5000);
  const names = new Map((ds.data ?? []).map((d) => [d.id, d.name]));
  const target = (path: string) => {
    const m = path.match(/\/datasets\/([0-9a-f-]{36})/);
    return m ? names.get(m[1]) ?? m[1].slice(0, 8) : "";
  };
  return (
    <>
      {error && <p className="err" role="alert">{error}</p>}
      {data && !data.enabled && <p className="empty">Access logging is on only while API keys are in use.</p>}
      {!own && <div className="parties">
        <button className={who === null ? "on" : ""} onClick={() => setWho(null)}>Everyone</button>
        {(data?.parties ?? []).map((p) => (
          <button key={p.actor ?? "-"} className={who === p.actor ? "on" : ""} onClick={() => setWho(p.actor)}>
            <b>{p.actor ?? "-"}</b> <span className="mono">{p.calls}</span>
            {p.blocked > 0 && <span className="pill bad">{p.blocked} blocked</span>}
            <small>{clock(p.last_at)}</small>
          </button>
        ))}
      </div>}
      <div className="scroll">
        <table className="compact">
          <thead><tr><th>When</th><th>Who</th><th>Role</th><th>Action</th><th>On</th><th>Result</th></tr></thead>
          <tbody>
            {(data?.rows ?? []).map((r) => (
              <tr key={r.seq}>
                <td className="mono">{clock(r.at)}</td><td>{r.actor ?? "-"}</td><td>{r.role ?? "-"}</td>
                <td>{action(r)}</td><td className="mono">{target(r.path)}</td><td>{result(r.status)}</td>
              </tr>
            ))}
            {data?.rows.length === 0 && <tr><td colSpan={6}>Nothing yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function Ledger({ me }: { me: Me }) {
  const { data, error } = usePoll<AuditRow[]>("/audit?limit=100", 3000);
  const [res, setRes] = useState<Verify | null>(null);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const canVerify = !me.rbac_enabled || me.role !== "viewer";
  const verify = async () => {
    setBusy(true); setFail(null);
    try { setRes(await api<Verify>("/audit/verify", "POST")); } catch (e) { setFail(message(e)); } finally { setBusy(false); }
  };
  return (
    <>
      <div className="toolbar">
        <button disabled={!canVerify || busy} onClick={verify}>{busy ? "Verifying" : "Verify chain"}</button>
        {res && (res.valid
          ? <span className="pill good big">Chain valid · {res.checked.toLocaleString()} rows</span>
          : <span className="pill bad big">Broken at row {res.seq}: {res.reason}</span>)}
      </div>
      {(error || fail) && <p className="err" role="alert">{error ?? fail}</p>}
      <div className="scroll">
        <table className="compact">
          <thead><tr><th className="r">Row</th><th>When</th><th>Change</th><th>Status</th><th>Strategy</th><th>Actor</th><th>Settled by</th><th>Hash</th></tr></thead>
          <tbody>
            {(data ?? []).map((r) => (
              <tr key={r.seq}>
                <td className="r mono">{r.seq}</td><td className="mono">{clock(r.at)}</td><td>{r.op}</td><td>{r.status ?? "-"}</td>
                <td>{r.strategy ?? "-"}</td><td>{r.actor ?? "-"}</td><td>{r.resolved_by ?? "-"}</td>
                <td className="mono">{r.hash ? r.hash.slice(0, 10) : "pending"}</td>
              </tr>
            ))}
            {data?.length === 0 && <tr><td colSpan={8}>Nothing yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function AuditPage({ me }: { me: Me }) {
  const [tab, setTab] = useState<"access" | "ledger">("access");
  return (
    <>
      <header className="pagehead"><h1>Audit logs</h1></header>
      <div className="seg" role="tablist">
        <button role="tab" aria-selected={tab === "access"} className={tab === "access" ? "on" : ""} onClick={() => setTab("access")}>Access</button>
        <button role="tab" aria-selected={tab === "ledger"} className={tab === "ledger" ? "on" : ""} onClick={() => setTab("ledger")}>Ledger changes</button>
      </div>
      {tab === "access" ? <Access /> : <Ledger me={me} />}
    </>
  );
}
