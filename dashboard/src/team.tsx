import { useState } from "react";
import { Me, Usage, clock, fmt, usePoll } from "./api";
import { Access, Ledger } from "./audit";

// A team's own view: what it has received in each run, and its own log. The
// server returns this team's rows only.

export function runLabel(u: Usage) {
  if (u.dataset.startsWith("adult-shared")) return "Shared dataset";
  if (u.exp === 1 || u.exp === 2) return `Exp ${u.exp} · ${u.strategy}`;
  if (u.exp === 4) return `Exp 4 · ${u.mode === "abort" ? "controlled abort" : "work"}`;
  if (u.dataset.startsWith("exp3-")) return `Exp 3 · ${u.dataset.split("-")[1]}`;
  return u.dataset;
}

/** This team's share of one run, for the experiment pages. */
export function MyShare({ me, datasetId }: { me: Me; datasetId: string | null }) {
  const { data } = usePoll<Usage[]>(datasetId ? "/demo/usage" : null, 1500);
  const u = (data ?? []).find((x) => x.dataset_id === datasetId);
  if (!datasetId) return null;
  return (
    <p className="myshare">
      <b>{me.name}</b>
      <span className="mono">{u ? `${u.granted} granted · ε ${fmt(u.eps_spent)}${u.eps_held ? ` + ${fmt(u.eps_held)} held` : ""}${u.denied ? ` · ${u.denied} denied` : ""}` : "nothing yet"}</span>
    </p>
  );
}

export function MyTeamPage({ me }: { me: Me }) {
  const [tab, setTab] = useState<"access" | "ledger">("access");
  const { data, error } = usePoll<Usage[]>("/demo/usage", 2000);
  const rows = data ?? [];
  return (
    <>
      <header className="pagehead"><h1>{me.name}</h1></header>
      <h2>Budget received</h2>
      {error && <p className="err" role="alert">{error}</p>}
      {data && rows.length === 0 ? <p className="empty">Nothing yet.</p> : (
        <div className="scroll">
          <table className="compact">
            <thead>
              <tr><th>Run</th><th>Dataset</th><th className="r">Granted</th><th className="r">Denied</th><th className="r">ε spent</th><th className="r">ε held</th><th className="r">Share of cap</th><th>Last</th></tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.dataset_id}>
                  <td>{runLabel(u)}</td>
                  <td className="mono">{u.dataset}</td>
                  <td className="r mono">{u.granted}</td>
                  <td className="r mono">{u.denied}</td>
                  <td className="r mono">{fmt(u.eps_spent)}</td>
                  <td className="r mono">{u.eps_held ? fmt(u.eps_held) : "-"}</td>
                  <td className="r mono">{fmt(Math.round(((u.eps_spent + u.eps_held) / u.epsilon_cap) * 1000) / 10)}%</td>
                  <td className="mono">{clock(u.last_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2>Log</h2>
      <div className="seg" role="tablist">
        <button role="tab" aria-selected={tab === "access"} className={tab === "access" ? "on" : ""} onClick={() => setTab("access")}>Access</button>
        <button role="tab" aria-selected={tab === "ledger"} className={tab === "ledger" ? "on" : ""} onClick={() => setTab("ledger")}>Ledger changes</button>
      </div>
      {tab === "access" ? <Access own /> : <Ledger me={me} />}
    </>
  );
}
