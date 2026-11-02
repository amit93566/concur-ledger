import { useMemo, useState } from "react";
import { AuditRow, Dataset, Hold, Invariants, LedgerRecord, Me, Verify, age, api, fmt, usePoll } from "./api";

function Bar({ d }: { d: Dataset }) {
  // Scaled to the cap, or to the total once it passes the cap, so an overspend
  // shows as length past the marker.
  const scale = Math.max(d.epsilon_cap, d.total) || 1;
  const pc = (v: number) => `${(v / scale) * 100}%`;
  const over = d.total > d.epsilon_cap;
  const within = Math.max(0, Math.min(d.epsilon_reserved, d.epsilon_cap - d.epsilon_spent));
  return (
    <div className="bar" role="img" aria-label={`${fmt(d.total)} of ${fmt(d.epsilon_cap)} used`}>
      <i className="spent" style={{ width: pc(Math.min(d.epsilon_spent, d.epsilon_cap)) }} />
      <i className="held" style={{ width: pc(within) }} />
      {over && <i className="over" style={{ width: pc(d.total - d.epsilon_cap) }} />}
      {over && <b className="capmark" style={{ left: pc(d.epsilon_cap) }} />}
    </div>
  );
}

function Detail({ d }: { d: Dataset }) {
  const inv = usePoll<Invariants>(`/datasets/${d.id}/invariants`, 2000);
  const rec = usePoll<LedgerRecord[]>(`/datasets/${d.id}/activity?limit=15`, 2000);
  return (
    <div className="detail">
      {inv.data && (
        <p>
          <span className={`pill ${inv.data.i1_cap_safety ? "good" : "bad"}`}>I1 cap {inv.data.i1_cap_safety ? "holds" : "broken"}</span>{" "}
          <span className={`pill ${inv.data.i2_ledger_agreement ? "good" : "bad"}`}>I2 ledger {inv.data.i2_ledger_agreement ? "agrees" : "disagrees"}</span>{" "}
          {Object.entries(inv.data.status_counts).map(([k, v]) => <span className="pill" key={k}>{k} {v.toLocaleString()}</span>)}
        </p>
      )}
      <div className="scroll">
        <table>
          <thead><tr><th>Job</th><th>Status</th><th>Strategy</th><th>Actor</th><th className="r">Held ε</th><th className="r">Charged ε</th></tr></thead>
          <tbody>
            {(rec.data ?? []).map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.job_id}</td><td>{r.status}</td><td>{r.strategy}</td><td>{r.actor ?? "-"}</td>
                <td className="r mono">{fmt(r.epsilon_reserved)}</td>
                <td className="r mono">{r.epsilon_committed === null ? "-" : fmt(r.epsilon_committed)}</td>
              </tr>
            ))}
            {rec.data?.length === 0 && <tr><td colSpan={6}>No reservations on this dataset yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function BurnDownPage() {
  const { data, error } = usePoll<Dataset[]>("/datasets", 700);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data ?? [])
      .filter((d) => !q || d.name.toLowerCase().includes(q))
      .sort((a, b) => Number(b.total > b.epsilon_cap) - Number(a.total > a.epsilon_cap));
  }, [data, query]);
  const over = (data ?? []).filter((d) => d.total > d.epsilon_cap).length;
  const held = (data ?? []).reduce((sum, d) => sum + d.epsilon_reserved, 0);

  return (
    <>
      <div className="head">
        <div>
          <h1>Budget burn-down</h1>
          <p className="sub">Live from the ledger. Datasets past their cap are listed first.</p>
        </div>
        <dl className="stats">
          <div><dt>Datasets</dt><dd>{(data ?? []).length}</dd></div>
          <div><dt>Past cap</dt><dd className={over ? "badtext" : ""}>{over}</dd></div>
          <div><dt>ε held</dt><dd>{fmt(held)}</dd></div>
        </dl>
      </div>
      {error && <p className="err" role="alert">{error}</p>}
      <div className="tools">
        <label htmlFor="ds-search">Find a dataset</label>
        <input id="ds-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Name" />
        <ul className="key">
          <li><i className="spent" />Spent</li><li><i className="held" />Held</li><li><i className="free" />Free</li><li><i className="over" />Past cap</li>
        </ul>
      </div>
      <div className="list">
        {rows.map((d) => {
          const isOver = d.total > d.epsilon_cap;
          return (
            <section key={d.id} className="ds">
              <button className="dsrow" aria-expanded={open === d.id} onClick={() => setOpen(open === d.id ? null : d.id)}>
                <span className="name">{d.name}{isOver && <span className="pill bad">Past cap by {fmt(d.total - d.epsilon_cap)}</span>}</span>
                <Bar d={d} />
                <span className="nums mono">{fmt(d.epsilon_spent)} + {fmt(d.epsilon_reserved)} / {fmt(d.epsilon_cap)}</span>
              </button>
              {open === d.id && <Detail d={d} />}
            </section>
          );
        })}
        {data && rows.length === 0 && <p>{query ? "No dataset matches that name." : "No datasets yet. Run a demo, such as make demo-breach, to create one."}</p>}
      </div>
    </>
  );
}

const WINDOWS = [[60, "1 minute"], [300, "5 minutes"], [3600, "1 hour"], [86400, "1 day"]] as const;

export function HoldsPage({ me }: { me: Me }) {
  const [older, setOlder] = useState(300);
  const { data, error, refresh } = usePoll<{ total: number; holds: Hold[] }>(`/holds?older_than_s=${older}`, 4000);
  const [confirming, setConfirming] = useState<{ hold: Hold; action: "release" | "charge" } | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [fail, setFail] = useState<string | null>(null);
  const canAct = me.role === "admin";

  const resolve = async () => {
    if (!confirming) return;
    const { hold, action } = confirming;
    setFail(null);
    try {
      await api(`/holds/${hold.id}/resolve`, "POST", { action });
      setDone(`${action === "release" ? "Released" : "Charged"} ε ${fmt(hold.epsilon)} on ${hold.dataset_name}.`);
      setConfirming(null);
      refresh();
    } catch (e) {
      setFail(e instanceof Error ? e.message : String(e));
      setConfirming(null);
    }
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Stale holds</h1>
          <p className="sub">Reservations that were never settled, usually because a job crashed. Their budget stays held until someone acts.</p>
        </div>
        <dl className="stats"><div><dt>Stale holds</dt><dd>{data?.total.toLocaleString() ?? "-"}</dd></div></dl>
      </div>
      <div className="tools">
        <label htmlFor="older">Older than</label>
        <select id="older" value={older} onChange={(e) => setOlder(Number(e.target.value))}>
          {WINDOWS.map(([s, label]) => <option key={s} value={s}>{label}</option>)}
        </select>
      </div>
      <p className="guide"><b>Release</b> returns the budget. Use it only if you know the job published nothing. <b>Charge</b> spends the full hold. Use it when the outcome is unknown, because privacy fails closed.</p>
      {!canAct && <p className="guide">You can view holds. Settling one needs the admin role.</p>}
      {(error || fail) && <p className="err" role="alert">{error ?? fail}</p>}
      {done && <p className="okmsg" role="status">{done}</p>}
      {confirming && (
        <div className="confirm" role="alertdialog" aria-label="Confirm">
          <span>{confirming.action === "release" ? "Release" : "Charge"} ε {fmt(confirming.hold.epsilon)} on <b>{confirming.hold.dataset_name}</b>? This cannot be undone.</span>
          <button onClick={resolve}>{confirming.action === "release" ? "Release hold" : "Charge hold"}</button>
          <button className="link" onClick={() => setConfirming(null)}>Keep hold</button>
        </div>
      )}
      <div className="scroll">
        <table>
          <thead><tr><th>Dataset</th><th>Job</th><th>Strategy</th><th className="r">Held ε</th><th className="r">Age</th>{canAct && <th>Action</th>}</tr></thead>
          <tbody>
            {(data?.holds ?? []).map((h) => (
              <tr key={h.id}>
                <td>{h.dataset_name}</td><td className="mono">{h.job_id}</td><td>{h.strategy}</td>
                <td className="r mono">{fmt(h.epsilon)}</td><td className="r mono">{age(h.age_s)}</td>
                {canAct && (
                  <td className="acts">
                    <button onClick={() => { setDone(null); setConfirming({ hold: h, action: "release" }); }}>Release</button>
                    <button onClick={() => { setDone(null); setConfirming({ hold: h, action: "charge" }); }}>Charge</button>
                  </td>
                )}
              </tr>
            ))}
            {data?.holds.length === 0 && <tr><td colSpan={canAct ? 6 : 5}>No holds older than this. Nothing needs attention.</td></tr>}
          </tbody>
        </table>
      </div>
      {data && data.total > data.holds.length && <p className="sub">Showing the oldest {data.holds.length} of {data.total.toLocaleString()}.</p>}
    </>
  );
}

export function AuditPage({ me }: { me: Me }) {
  const { data, error } = usePoll<AuditRow[]>("/audit?limit=100", 3000);
  const [result, setResult] = useState<Verify | null>(null);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const canVerify = me.role !== "viewer";

  const verify = async () => {
    setBusy(true);
    setFail(null);
    try {
      setResult(await api<Verify>("/audit/verify", "POST"));
    } catch (e) {
      setFail(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="head">
        <div>
          <h1>Audit log</h1>
          <p className="sub">Every change to a spend record, newest first. Rows join the hash chain about a second after their transaction ends.</p>
        </div>
        <button disabled={!canVerify || busy} title={canVerify ? undefined : "Needs the analyst role"} onClick={verify}>
          {busy ? "Verifying" : "Verify chain"}
        </button>
      </div>
      {(error || fail) && <p className="err" role="alert">{error ?? fail}</p>}
      {result && (
        <div className={`verdict ${result.valid ? "pass" : "failv"}`} role="status">
          {result.valid ? (
            <p><b>Chain valid.</b> {result.checked.toLocaleString()} rows checked, {result.unsealed} not yet sealed.</p>
          ) : (
            <p><b>Chain broken at position {result.broken_at?.toLocaleString()}</b> (row {result.seq}): {result.reason}. {result.checked.toLocaleString()} rows before it verified.</p>
          )}
          <p className="mono head-hash">Head {result.head || "(empty)"}</p>
          <p className="sub">Keep a copy of the head elsewhere. Only that catches someone rewriting the whole chain.</p>
        </div>
      )}
      <div className="scroll">
        <table>
          <thead><tr><th className="r">Row</th><th>When</th><th>Change</th><th>Status</th><th>Strategy</th><th>Actor</th><th>Settled by</th><th>Chain</th></tr></thead>
          <tbody>
            {(data ?? []).map((r) => (
              <tr key={r.seq}>
                <td className="r mono">{r.seq}</td><td className="mono">{new Date(r.at).toLocaleTimeString()}</td>
                <td>{r.op}</td><td>{r.status ?? "-"}</td><td>{r.strategy ?? "-"}</td><td>{r.actor ?? "-"}</td><td>{r.resolved_by ?? "-"}</td>
                <td className="mono">{r.hash ? r.hash.slice(0, 10) : "pending"}</td>
              </tr>
            ))}
            {data?.length === 0 && <tr><td colSpan={8}>No audit rows yet. They appear when a reservation is made.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
