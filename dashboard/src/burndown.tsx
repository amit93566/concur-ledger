import { useMemo, useState } from "react";
import { Dataset, Invariants, LedgerRecord, fmt, usePoll } from "./api";

// Every dataset's budget, live from the ledger. Admin only.

function Bar({ d }: { d: Dataset }) {
  // Scaled to the cap, or to the total once it passes it, so an overspend shows
  // as length past the marker.
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
        <p className="pills">
          <span className={`pill ${inv.data.i1_cap_safety ? "good" : "bad"}`}>I1 cap {inv.data.i1_cap_safety ? "holds" : "broken"}</span>
          <span className={`pill ${inv.data.i2_ledger_agreement ? "good" : "bad"}`}>I2 ledger {inv.data.i2_ledger_agreement ? "agrees" : "disagrees"}</span>
          {Object.entries(inv.data.status_counts).map(([k, v]) => <span className="pill" key={k}>{k} {v.toLocaleString()}</span>)}
        </p>
      )}
      <div className="scroll">
        <table className="compact">
          <thead><tr><th>Job</th><th>Status</th><th>Strategy</th><th>Team</th><th className="r">Held ε</th><th className="r">Charged ε</th></tr></thead>
          <tbody>
            {(rec.data ?? []).map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.job_id}</td><td>{r.status}</td><td>{r.strategy}</td><td>{r.actor ?? "-"}</td>
                <td className="r mono">{fmt(r.epsilon_reserved)}</td>
                <td className="r mono">{r.epsilon_committed === null ? "-" : fmt(r.epsilon_committed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function BurnDownPage() {
  const { data, error } = usePoll<Dataset[]>("/datasets", 1000);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data ?? []).filter((d) => !q || d.name.toLowerCase().includes(q));
  }, [data, query]);
  const over = (data ?? []).filter((d) => d.total > d.epsilon_cap).length;
  const held = (data ?? []).reduce((sum, d) => sum + d.epsilon_reserved, 0);

  return (
    <>
      <header className="pagehead">
        <h1>Burn-down</h1>
        <dl className="stats">
          <div><dt>Datasets</dt><dd>{(data ?? []).length}</dd></div>
          <div><dt>Past cap</dt><dd className={over ? "badtext" : ""}>{over}</dd></div>
          <div><dt>ε held</dt><dd>{fmt(held)}</dd></div>
        </dl>
      </header>
      {error && <p className="err" role="alert">{error}</p>}
      <div className="toolbar">
        <input aria-label="Find a dataset" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a dataset" />
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
                <span className="name">{d.name}{isOver && <span className="pill bad">+{fmt(d.total - d.epsilon_cap)}</span>}</span>
                <Bar d={d} />
                <span className="nums mono">{fmt(d.epsilon_spent)} + {fmt(d.epsilon_reserved)} / {fmt(d.epsilon_cap)}</span>
              </button>
              {open === d.id && <Detail d={d} />}
            </section>
          );
        })}
        {data && rows.length === 0 && <p className="empty">No datasets.</p>}
      </div>
    </>
  );
}
