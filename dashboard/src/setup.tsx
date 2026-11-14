import { useEffect, useState } from "react";
import { SetupState, api, clock, message } from "./api";

// The admin's choice of cap, epsilon per query and number of teams. Runs take
// a copy when they start, so a change applies to the next run.

/** Can naive overshoot with these values? Each round asks for teams x eps; the
 * round that reaches the cap finds `rest` left. If one query still fits in it,
 * every team reads the same headroom and all are granted. */
function naiveCanBreach(cap: number, eps: number, teams: number) {
  if (teams < 2 || !(cap > 0) || !(eps > 0)) return false;
  const round = teams * eps;
  const rest = cap - Math.floor(cap / round + 1e-9) * round;
  return rest + 1e-9 >= eps;
}

export function SetupPage({ setup, onSaved }: { setup: SetupState | null; onSaved: () => void }) {
  const [cap, setCap] = useState("100");
  const [eps, setEps] = useState("2");
  const [teams, setTeams] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = setup?.setup ?? null;
  const available = setup?.teams_available ?? 8;

  useEffect(() => {
    if (current) {
      setCap(String(current.epsilon_cap));
      setEps(String(current.epsilon));
      setTeams(current.teams);
    }
  }, [current?.updated_at]);

  const capN = Number(cap), epsN = Number(eps);
  const valid = capN > 0 && epsN > 0 && epsN <= capN;
  const breach = naiveCanBreach(capN, epsN, teams);

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await api("/demo/setup", "POST", { epsilon_cap: capN, epsilon: epsN, teams });
      onSaved();
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  return (
    <>
      <header className="pagehead"><h1>Setup</h1></header>
      <form className="setup" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <label htmlFor="cap">Privacy budget (cap)</label>
        <input id="cap" type="number" min="0" step="any" value={cap} onChange={(e) => setCap(e.target.value)} />
        <label htmlFor="eps">ε per query</label>
        <input id="eps" type="number" min="0" step="any" value={eps} onChange={(e) => setEps(e.target.value)} />
        <label htmlFor="teams">Teams</label>
        <select id="teams" value={teams} onChange={(e) => setTeams(Number(e.target.value))}>
          {Array.from({ length: available }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <div className="formfoot">
          <button type="submit" disabled={busy || !valid}>{current ? "Save" : "Start"}</button>
          {valid && !breach && (
            <span className="pill warn">
              {teams < 2 ? "Naive cannot overshoot with one team: nothing races" : "Naive cannot overshoot with these values: each round fits the cap exactly"}
            </span>
          )}
        </div>
      </form>
      {error && <p className="err" role="alert">{error}</p>}
      {current && <p className="foot">Set by {current.updated_by ?? "-"} at {clock(current.updated_at)}.</p>}
    </>
  );
}
