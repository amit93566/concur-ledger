import { useEffect, useState } from "react";
import { Me, Round, RunDetail, SetupState, TeamShot, api, clock, fmt, isTeam } from "./api";
import { Budget, RunControls, RunPicker, SetupChips, StatusPill, Waiting, Workspace, useRuns } from "./common";
import { MyShare } from "./team";

// Exp 1 (naive) and Exp 2 (a safe strategy): every team reserves at the same
// moment, then commits, round after round. The run lives on the server, so
// every window shows the same one.

const SAFE = ["atomic", "for_update", "serializable"];

function traceLine(strategy: string, cap: number, eps: number, t: TeamShot) {
  if (t.outcome === "error") return t.detail ?? "error";
  if (t.outcome === "gave_up") return "gave up after retries";
  const s = t.observed_spent, r = t.observed_reserved;
  if (strategy === "atomic") {
    // The post-update row; none when the predicate was false.
    return s === null || r === null ? "predicate false at the database" : `ledger now ${fmt(s)} + ${fmt(r)} = ${fmt(s + r)} ≤ ${fmt(cap)}`;
  }
  if (s === null || r === null) return "read -";
  const projected = s + r + eps;
  return `read ${fmt(s)} + ${fmt(r)} → ${fmt(projected)} ${projected <= cap ? "≤" : ">"} ${fmt(cap)}`;
}

function Trace({ strategy, cap, eps, round }: { strategy: string; cap: number; eps: number; round: Round }) {
  const ordered = [...round.teams].sort((a, b) => (a.received_at ?? 0) - (b.received_at ?? 0));
  const first = ordered.find((t) => t.outcome === "reserved");
  return (
    <ol className="trace mono">
      {ordered.map((t) => {
        const stale = strategy === "naive" && t.outcome === "reserved" && first && t !== first &&
          t.observed_spent === first.observed_spent && t.observed_reserved === first.observed_reserved;
        return (
          <li key={t.team}>
            <span className="team">{t.team}</span> {traceLine(strategy, cap, eps, t)} →{" "}
            <b className={t.outcome === "reserved" ? "" : "badtext"}>{t.outcome === "reserved" ? "GRANTED" : t.outcome.toUpperCase()}</b>
            {t.answer !== undefined && t.answer !== null && <span className="ans"> answer {fmt(t.answer)}</span>}
            {stale && <span className="stale"> same stale read</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** The exact answer to the run's question, for the data owner (admin) only. */
function Truth({ spec }: { spec: unknown }) {
  const [truth, setTruth] = useState<number | null>(null);
  const key = JSON.stringify(spec);
  useEffect(() => {
    if (!spec) return;
    api<{ truth: number }>("/adult/truth", "POST", { spec }).then((r) => setTruth(r.truth)).catch(() => setTruth(null));
  }, [key]);
  return truth === null ? null : <span className="pill">true value {fmt(truth)} · owner only</span>;
}

function RunView({ run, owner }: { run: RunDetail; owner: boolean }) {
  const rounds = run.progress as Round[];
  const cap = Number(run.params.epsilon_cap);
  const eps = Number(run.params.epsilon);
  const breachAt = rounds.find((r) => r.total > cap)?.round;
  const shown = rounds.find((r) => r.round === breachAt) ?? rounds[rounds.length - 1];
  const granted = rounds.reduce((a, r) => a + r.granted, 0);
  return (
    <>
      <div className="runhead">
        <StatusPill run={run} />
        <span className="meta">{run.strategy} · started by {run.started_by ?? "-"} at {clock(run.started_at)} · {granted} granted in {rounds.length} rounds</span>
        {owner && run.params.query !== undefined && <Truth spec={run.params.query} />}
      </div>
      <Workspace rail={run.dataset && <Budget d={run.dataset} inv={run.invariants} />}>
      {rounds.length > 0 && (
        <>
          <section className="panel scroll">
            <table className="compact">
              <thead><tr><th className="r">Round</th><th className="r">Granted</th><th className="r">Denied</th><th className="r">Total after</th><th className="r">Arrived within</th></tr></thead>
              <tbody>
                {rounds.map((r) => (
                  <tr key={r.round} className={r.round === breachAt ? "hot" : undefined}>
                    <td className="r mono">{r.round}</td>
                    <td className="r mono">{r.granted}</td>
                    <td className="r mono">{r.denied + r.gave_up}</td>
                    <td className="r mono">{fmt(r.total)}</td>
                    <td className="r mono">{r.spread_ms === null ? "-" : `${fmt(r.spread_ms)} ms`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          {shown && (
            <section className="panel">
              <h2>Round {shown.round}{shown.round === breachAt ? " · breach" : ""}</h2>
              <Trace strategy={run.strategy} cap={cap} eps={eps} round={shown} />
            </section>
          )}
        </>
      )}
      </Workspace>
    </>
  );
}

export function RoundsPage({ exp, me, setup }: { exp: 1 | 2; me: Me; setup: SetupState | null }) {
  const [strategy, setStrategy] = useState("atomic");
  const { runs, id, setPicked, detail } = useRuns(exp);
  const running = runs.find((r) => r.status === "running") ?? null;
  const title = exp === 1 ? "Exp 1 · Naive" : "Exp 2 · Atomic";

  const start = async () => {
    const r = await api<{ id: string }>("/demo/runs", "POST", exp === 1 ? { exp } : { exp, strategy });
    setPicked(null);
    return r;
  };

  return (
    <>
      <header className="pagehead">
        <h1>{title}</h1>
        <SetupChips setup={setup?.setup ?? null} extra={[setup?.setup ? setup.setup.question : false]} />
      </header>
      {setup && !setup.setup ? <Waiting /> : (
        <>
          <div className="toolbar">
            <RunControls me={me} running={running} ready={!!setup?.setup} onStart={start}>
              {exp === 2 && (
                <select aria-label="Strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                  {SAFE.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              )}
            </RunControls>
            <RunPicker runs={runs} id={id} onPick={setPicked} />
          </div>
          {detail && isTeam(me) && <MyShare me={me} datasetId={detail.dataset_id} />}
          {detail ? <RunView run={detail} owner={me.rbac_enabled && me.role === "admin"} /> : runs.length === 0 && <p className="empty">No runs yet.</p>}
        </>
      )}
    </>
  );
}
