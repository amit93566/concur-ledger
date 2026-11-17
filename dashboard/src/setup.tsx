import { useEffect, useState } from "react";
import { SetupState, Spec, api, clock, message } from "./api";
import { Workspace } from "./common";
import { DEFAULT_SPEC, PriceTag, QuestionBuilder, usePrice } from "./shared";

// The admin's choices: the cap (the shared Adult budget, and each run's), the
// standard question the automatic runs ask (OpenDP prices it), and how many
// teams race in Exp 1 and 2. Runs copy these when they start.

/** Can naive overshoot? Each round asks for teams x eps; the round that
 * reaches the cap finds `rest` left, and if one query still fits in it, every
 * team reads the same headroom and all are granted. */
function naiveCanBreach(cap: number, eps: number, teams: number) {
  if (teams < 2 || !(cap > 0) || !(eps > 0)) return false;
  const round = teams * eps;
  const rest = cap - Math.floor(cap / round + 1e-9) * round;
  return rest + 1e-9 >= eps;
}

export function SetupPage({ setup, onSaved }: { setup: SetupState | null; onSaved: () => void }) {
  const [cap, setCap] = useState("100");
  const [teams, setTeams] = useState(6);
  const [spec, setSpec] = useState<Spec>(DEFAULT_SPEC);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { price, error: priceError } = usePrice(spec);
  const current = setup?.setup ?? null;
  const available = setup?.teams_available ?? 8;

  useEffect(() => {
    if (current) {
      setCap(String(current.epsilon_cap));
      setTeams(current.teams);
      setSpec(current.query);
    }
  }, [current?.updated_at]);

  const capN = Number(cap);
  const eps = price?.epsilon ?? 0;
  const valid = capN > 0 && !!price && eps <= capN;
  const breach = naiveCanBreach(capN, eps, teams);

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await api("/demo/setup", "POST", { epsilon_cap: capN, teams, query: spec });
      onSaved();
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };

  return (
    <>
      <header className="pagehead"><h1>Setup</h1></header>
      <form className="setup" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <Workspace rail={<section className="panel"><h2>Price</h2><PriceTag price={price} error={priceError} /></section>}>
          <section className="panel">
            <div className="fields">
              <label htmlFor="cap">Privacy budget (cap)<input id="cap" type="number" min="0" step="any" value={cap} onChange={(e) => setCap(e.target.value)} /></label>
              <label htmlFor="teams">Teams in Exp 1 and 2
                <select id="teams" value={teams} onChange={(e) => setTeams(Number(e.target.value))}>
                  {Array.from({ length: Math.max(0, available - 1) }, (_, i) => i + 2).map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            </div>
            <h3 className="setlabel">Standard question for the runs</h3>
            <QuestionBuilder spec={spec} onChange={setSpec} />
            <div className="formfoot">
              <button type="submit" disabled={busy || !valid}>{current ? "Save" : "Start"}</button>
              {valid && !breach && <span className="pill warn">Naive cannot overshoot with these values: each round fits the cap exactly</span>}
            </div>
          </section>
          {current && <p className="foot">Set by {current.updated_by ?? "-"} at {clock(current.updated_at)}. The shared dataset is used by {current.shared_teams.join(" and ")}; a new cap starts a new shared budget.</p>}
        </Workspace>
      </form>
      {error && <p className="err" role="alert">{error}</p>}
    </>
  );
}
