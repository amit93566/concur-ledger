import { useEffect, useState } from "react";
import { Catalog, Dataset, Invariants, LedgerRecord, Me, OwnerAnswer, Price, SetupState, Spec, Usage, call, clock, fmt, message, usePoll } from "./api";
import { Budget, Chips, Waiting } from "./common";

// The Shared dataset: team-1 and team-2 ask their own questions of the UCI
// Adult data, against one budget. OpenDP prices each question before it is
// asked. Answers stay with the team that asked; the admin sees who spent what.

export const DEFAULT_SPEC: Spec = { kind: "count", filter: "income_gt_50k", accuracy: "high" };

/** A question's price, from OpenDP, refreshed whenever the question changes. */
export function usePrice(spec: Spec) {
  const [price, setPrice] = useState<Price | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = JSON.stringify(spec);
  useEffect(() => {
    let live = true;
    call<Price & { detail?: string }>("/adult/price", "POST", { spec })
      .then((r) => {
        if (!live) return;
        if (r.status === 200) { setPrice(r.body); setError(null); } else { setPrice(null); setError(r.body.detail ?? `HTTP ${r.status}`); }
      })
      .catch((e) => live && setError(message(e)));
    return () => { live = false; };
  }, [key]);
  return { price, error };
}

export function QuestionBuilder({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const { data: cat } = usePoll<Catalog>("/adult/catalog", 60000);
  if (!cat) return null;
  const set = (patch: Partial<Spec>) => onChange({ ...spec, ...patch });
  return (
    <div className="qb">
      <label>
        <span>Question</span>
        <select value={spec.kind} onChange={(e) => set({ kind: e.target.value as Spec["kind"], column: e.target.value === "sum" ? spec.column ?? cat.sum_columns[0].key : undefined })}>
          {cat.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
      </label>
      {spec.kind === "sum" && (
        <label>
          <span>Column</span>
          <select value={spec.column} onChange={(e) => set({ column: e.target.value })}>
            {cat.sum_columns.map((c) => <option key={c.key} value={c.key}>{c.label} ({c.bounds[0]}-{c.bounds[1]})</option>)}
          </select>
        </label>
      )}
      <label>
        <span>Who</span>
        <select value={spec.filter} onChange={(e) => set({ filter: e.target.value })}>
          {cat.filters.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </label>
      <label>
        <span>Accuracy</span>
        <select value={spec.accuracy} onChange={(e) => set({ accuracy: e.target.value })}>
          {cat.levels.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
        </select>
      </label>
    </div>
  );
}

export function PriceTag({ price, error }: { price: Price | null; error: string | null }) {
  if (error) return <p className="err">{error}</p>;
  if (!price) return null;
  return (
    <p className="price">
      <span>{price.question}</span>
      <b className="mono">ε {fmt(price.epsilon)}</b>
      <span className="mono">answer within ±{fmt(price.accuracy)} ({Math.round(price.confidence * 100)}%)</span>
    </p>
  );
}

type Answer = { id: string; at: string; question: string; answer: number | null; epsilon: number; status: "answered" | "denied"; left?: number };

const store = (team: string) => `concur-ledger-answers-${team}`;
function loadAnswers(team: string): Answer[] {
  try { return JSON.parse(sessionStorage.getItem(store(team)) ?? "[]"); } catch { return []; }
}
function saveAnswers(team: string, list: Answer[]) {
  try { sessionStorage.setItem(store(team), JSON.stringify(list)); } catch { /* kept until reload */ }
}

function Ask({ me, datasetId }: { me: Me; datasetId: string }) {
  const [spec, setSpec] = useState<Spec>(DEFAULT_SPEC);
  const { price, error } = usePrice(spec);
  const [answers, setAnswers] = useState<Answer[]>(() => loadAnswers(me.name ?? "team"));
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);

  const ask = async () => {
    if (!price) return;
    setBusy(true); setFail(null);
    try {
      const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
      const r = await call<{ result?: number; epsilon_committed?: number; epsilon_cost?: number; dataset?: Dataset; detail?: string }>(
        `/datasets/${datasetId}/run`, "POST",
        { job_id: price.question, idempotency_key: id, strategy: "atomic", adapter: "adult", spec },
      );
      let entry: Answer | null = null;
      if (r.status === 200) {
        entry = { id, at: new Date().toISOString(), question: price.question, answer: r.body.result ?? null, epsilon: r.body.epsilon_committed ?? price.epsilon, status: "answered" };
      } else if (r.status === 429) {
        entry = { id, at: new Date().toISOString(), question: price.question, answer: null, epsilon: r.body.epsilon_cost ?? price.epsilon, status: "denied" };
      } else {
        setFail(r.body.detail ?? `HTTP ${r.status}`);
      }
      if (entry) {
        const next = [entry, ...answers];
        setAnswers(next);
        saveAnswers(me.name ?? "team", next);
      }
    } catch (e) { setFail(message(e)); } finally { setBusy(false); }
  };

  const last = answers[0];
  return (
    <>
      <section className="panel ask">
        <QuestionBuilder spec={spec} onChange={setSpec} />
        <div className="askrow">
          <PriceTag price={price} error={error} />
          <button disabled={busy || !price} onClick={ask}>Ask</button>
        </div>
        {fail && <p className="err" role="alert">{fail}</p>}
        {last && (
          last.status === "answered"
            ? <p className="answer"><span>{last.question}</span><b className="mono">{fmt(last.answer ?? 0)}</b><span className="mono">charged ε {fmt(last.epsilon)}</span></p>
            : <p className="answer denied"><span>{last.question}</span><b>Denied: not enough budget left</b><span className="mono">costs ε {fmt(last.epsilon)}</span></p>
        )}
      </section>
      {answers.length > 0 && (
        <div className="scroll">
          <table className="compact">
            <caption>Your answers</caption>
            <thead><tr><th>When</th><th>Question</th><th className="r">Answer</th><th className="r">ε</th></tr></thead>
            <tbody>
              {answers.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{clock(a.at)}</td><td>{a.question}</td>
                  <td className="r mono">{a.status === "answered" ? fmt(a.answer ?? 0) : <span className="pill bad">denied</span>}</td>
                  <td className="r mono">{a.status === "answered" ? fmt(a.epsilon) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** What the dataset is: published schema facts only, never values from the rows. */
function DatasetCard({ cat }: { cat: Catalog }) {
  return (
    <details className="card">
      <summary><b>About the data</b> <span className="meta">{cat.dataset} · {cat.rows.toLocaleString()} people · {cat.schema.length} columns · {cat.license}</span></summary>
      <div className="scroll">
        <table className="compact">
          <thead><tr><th>Column</th><th>Type</th><th>Values</th></tr></thead>
          <tbody>{cat.schema.map((c) => <tr key={c.column}><td className="mono">{c.column}</td><td>{c.type}</td><td>{c.values}</td></tr>)}</tbody>
        </table>
      </div>
      <p className="foot">{cat.source}. One row is one person. Rows are never shown; questions are answered only through OpenDP.</p>
    </details>
  );
}

/** The data owner's view: each answer a team received, beside the true value. */
function OwnerAnswers({ datasetId }: { datasetId: string }) {
  const { data } = usePoll<OwnerAnswer[]>(`/adult/answers?dataset_id=${datasetId}&limit=50`, 1500);
  return (
    <div className="scroll">
      <table className="compact">
        <caption>Questions asked <span className="pill">true value: owner only</span></caption>
        <thead><tr><th>When</th><th>Team</th><th>Question</th><th className="r">Answer given</th><th className="r">True value</th><th className="r">Noise</th><th>Within promise</th></tr></thead>
        <tbody>
          {(data ?? []).map((a) => {
            const err = a.answer !== null && a.truth !== null ? a.answer - a.truth : null;
            return (
              <tr key={a.id}>
                <td className="mono">{clock(a.at)}</td><td>{a.team ?? "-"}</td><td>{a.question}</td>
                <td className="r mono">{a.status === "denied" ? <span className="pill bad">denied</span> : a.answer === null ? "-" : fmt(a.answer)}</td>
                <td className="r mono">{a.truth === null ? "-" : fmt(a.truth)}</td>
                <td className="r mono">{err === null ? "-" : `${err >= 0 ? "+" : ""}${fmt(Math.round(err * 10) / 10)}`}</td>
                <td>{err === null || a.accuracy === null ? "-" : Math.abs(err) <= a.accuracy
                  ? <span className="pill good">yes · ±{fmt(a.accuracy)}</span>
                  : <span className="pill warn">no · ±{fmt(a.accuracy)}</span>}</td>
              </tr>
            );
          })}
          {data?.length === 0 && <tr><td colSpan={7}>No questions yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function Watch({ datasetId, owner }: { datasetId: string; owner: boolean }) {
  const usage = usePoll<Usage[]>("/demo/usage", 1500);
  const rec = usePoll<LedgerRecord[]>(owner ? null : `/datasets/${datasetId}/activity?limit=50`, 1500);
  const teams = (usage.data ?? []).filter((u) => u.dataset_id === datasetId);
  return (
    <>
      {teams.length > 0 && (
        <div className="scroll">
          <table className="compact">
            <caption>Spending by team</caption>
            <thead><tr><th>Team</th><th className="r">Answered</th><th className="r">Denied</th><th className="r">ε spent</th><th className="r">Share of cap</th></tr></thead>
            <tbody>
              {teams.map((u) => (
                <tr key={u.team}>
                  <td>{u.team}</td><td className="r mono">{u.granted}</td><td className="r mono">{u.denied}</td>
                  <td className="r mono">{fmt(u.eps_spent)}</td><td className="r mono">{fmt(Math.round((u.eps_spent / u.epsilon_cap) * 1000) / 10)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {owner ? <OwnerAnswers datasetId={datasetId} /> : <div className="scroll">
        <table className="compact">
          <caption>Questions asked</caption>
          <thead><tr><th>When</th><th>Team</th><th>Question</th><th>Result</th><th className="r">ε</th></tr></thead>
          <tbody>
            {(rec.data ?? []).map((r) => (
              <tr key={r.id}>
                <td className="mono">{clock(r.reserved_at)}</td><td>{r.actor ?? "-"}</td><td>{r.job_id}</td>
                <td>{r.status === "committed" ? <span className="pill good">answered</span> : r.status === "denied" ? <span className="pill bad">denied</span> : <span className="pill">{r.status}</span>}</td>
                <td className="r mono">{r.epsilon_committed === null ? "-" : fmt(r.epsilon_committed)}</td>
              </tr>
            ))}
            {rec.data?.length === 0 && <tr><td colSpan={5}>No questions yet.</td></tr>}
          </tbody>
        </table>
      </div>}
    </>
  );
}

export function SharedPage({ me, setup }: { me: Me; setup: SetupState | null }) {
  const s = setup?.setup ?? null;
  const id = s?.shared_dataset_id ?? null;
  const ds = usePoll<Dataset>(id ? `/datasets/${id}` : null, 1000);
  const inv = usePoll<Invariants>(id ? `/datasets/${id}/invariants` : null, 1500);
  const { data: cat } = usePoll<Catalog>("/adult/catalog", 60000);
  const sharer = !!me.name && !!s?.shared_teams.includes(me.name);

  return (
    <>
      <header className="pagehead">
        <h1>Shared dataset</h1>
        {s && <Chips items={[`${cat?.dataset ?? "UCI Adult"} · ${cat ? cat.rows.toLocaleString() : "-"} people`, `cap ${fmt(s.epsilon_cap)}`, `shared by ${s.shared_teams.join(" and ")}`]} />}
      </header>
      {setup && !s ? <Waiting /> : id && (
        <>
          {cat && <DatasetCard cat={cat} />}
          {ds.data && <Budget d={ds.data} inv={inv.data} />}
          {sharer ? <Ask me={me} datasetId={id} /> : <Watch datasetId={id} owner={!me.rbac_enabled || me.role === "admin"} />}
        </>
      )}
    </>
  );
}
