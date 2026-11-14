import { useEffect, useState } from "react";
import { Round, Run, RunDetail, fmt, usePoll } from "./api";
import { Chips, Legend, LineChart, Series } from "./common";
import { MetricSelect } from "./sweep";

// Results: this session's console runs, then the measured results, which the
// dashboard cannot read from results/ and gets from results.json instead
// (experiments/dashboard_results.py writes it).

type Point = {
  n: number; median: number; min: number; max: number; reps: number; gave_up_per_s: number; denial_rate: number;
  p50_ms?: number; p95_ms?: number; p99_ms: number; retries_per_success?: number;
};
type Panel = { title: string; regime: string; duration_ms: number; source: string; series: Record<string, Point[]> };
type Burst = { strategy: string; cap: number; seed_spent: number; n: number; runs: number; breaches: number; max_overshoot: number; legal: number; granted_max: number };
type Calibration = { epsilon: number; delta: number; accuracy_median: number; epoch_s_median: number };
type Results = { calibration: Calibration | null; panels: Panel[]; exp12?: Burst[]; cap100?: Burst[]; exp4?: { label: string; checks: number; passed: number }[] };

const ORDER = ["naive", "atomic", "for_update", "serializable"];

const MEASURED = {
  throughput: { label: "Throughput", unit: "per s", get: (p: Point) => p.median },
  p50_ms: { label: "p50 latency", unit: "ms", get: (p: Point) => p.p50_ms ?? null },
  p95_ms: { label: "p95 latency", unit: "ms", get: (p: Point) => p.p95_ms ?? null },
  p99_ms: { label: "p99 latency", unit: "ms", get: (p: Point) => p.p99_ms },
  gave_up: { label: "Gave up", unit: "per s", get: (p: Point) => p.gave_up_per_s },
} as const;
type MKey = keyof typeof MEASURED;

// --- this session ------------------------------------------------------------

function SessionRow({ id }: { id: string }) {
  const { data: r } = usePoll<RunDetail>(`/demo/runs/${id}`, 3000);
  if (!r || !r.dataset) return null;
  const rounds = r.progress as Round[];
  const over = r.dataset.total - r.dataset.epsilon_cap;
  return (
    <tr>
      <td><i className={`sw sw-${r.strategy}`} />Exp {r.exp} · {r.strategy}</td>
      <td className="r mono">{(r.params.teams ?? []).length}</td>
      <td className="r mono">{rounds.length}</td>
      <td className="r mono">{rounds.reduce((a, x) => a + x.granted, 0)}</td>
      <td className="r mono">{fmt(r.dataset.total)} / {fmt(r.dataset.epsilon_cap)}</td>
      <td>{over > 0 ? <span className="pill bad">breached by {fmt(over)}</span> : r.status === "running" ? <span className="pill warn">running</span> : <span className="pill good">cap held</span>}</td>
    </tr>
  );
}

function Session() {
  const e1 = usePoll<Run[]>("/demo/runs?exp=1&limit=1", 3000);
  const e2 = usePoll<Run[]>("/demo/runs?exp=2&limit=20", 3000);
  const latest2 = ORDER.map((s) => (e2.data ?? []).find((r) => r.strategy === s)).filter(Boolean) as Run[];
  const ids = [...(e1.data ?? []).slice(0, 1), ...latest2].map((r) => r.id);
  if (!ids.length) return <p className="empty">No Exp 1 or Exp 2 runs yet.</p>;
  return (
    <div className="scroll">
      <table className="compact">
        <thead><tr><th>Run</th><th className="r">Teams</th><th className="r">Rounds</th><th className="r">Granted</th><th className="r">Total / cap</th><th>Result</th></tr></thead>
        <tbody>{ids.map((id) => <SessionRow key={id} id={id} />)}</tbody>
      </table>
    </div>
  );
}

// --- measured ----------------------------------------------------------------

function BurstTable({ rows, title }: { rows: Burst[]; title: string }) {
  const strategies = ORDER.filter((s) => rows.some((r) => r.strategy === s));
  const keys = [...new Set(rows.map((r) => `${r.seed_spent}|${r.n}`))];
  return (
    <div className="scroll">
      <table className="compact">
        <caption>{title}</caption>
        <thead><tr><th>Start</th><th className="r">N at once</th><th className="r">Room for</th>{strategies.map((s) => <th key={s}><i className={`sw sw-${s}`} />{s}</th>)}</tr></thead>
        <tbody>
          {keys.map((k) => {
            const [seed, n] = k.split("|").map(Number);
            const cell = (s: string) => rows.find((r) => r.strategy === s && r.seed_spent === seed && r.n === n);
            const any = rows.find((r) => r.seed_spent === seed && r.n === n)!;
            return (
              <tr key={k}>
                <td className="mono">{fmt(any.cap)} / spent {fmt(seed)}</td>
                <td className="r mono">{n}</td>
                <td className="r mono">{any.legal}</td>
                {strategies.map((s) => {
                  const c = cell(s);
                  return (
                    <td key={s} className="mono">
                      {c ? <>{c.breaches > 0 ? <b className="badtext">{c.breaches}/{c.runs} breached</b> : `0/${c.runs}`}{c.breaches > 0 && <small> up to +{fmt(c.max_overshoot)}</small>}</> : "-"}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Exp3({ panels, cal }: { panels: Panel[]; cal: Calibration | null }) {
  const [pi, setPi] = useState(0);
  const [metric, setMetric] = useState<MKey>("throughput");
  const panel = panels[pi];
  if (!panel) return null;
  const m = MEASURED[metric];
  const strategies = ORDER.filter((s) => panel.series[s]);
  const ns = [...new Set(Object.values(panel.series).flat().map((p) => p.n))].sort((a, b) => a - b);
  const series: Series = {};
  for (const s of strategies) series[s] = panel.series[s].map((p) => ({ n: p.n, v: m.get(p) }));
  return (
    <>
      <div className="toolbar">
        <select aria-label="Operation" value={pi} onChange={(e) => setPi(Number(e.target.value))}>
          {panels.map((p, i) => <option key={p.title} value={i}>{p.title}</option>)}
        </select>
        <MetricSelect value={metric} onChange={(k) => setMetric(k as MKey)} keys={Object.keys(MEASURED)} />
      </div>
      {panel.duration_ms === 14000 && cal && (
        <Chips items={[`14 s = one DP-SGD epoch (median ${cal.epoch_s_median.toFixed(1)} s)`, `ε ${cal.epsilon} at δ ${cal.delta}`, `accuracy ${(cal.accuracy_median * 100).toFixed(1)}%`]} />
      )}
      <div className="charts one">
        <div>
          <Legend strategies={strategies} />
          <LineChart series={series} title={`${m.label} · ${panel.title}`} unit={m.unit} />
        </div>
        <div className="scroll">
          <table className="compact">
            <thead><tr><th>Strategy</th>{ns.map((n) => <th className="r" key={n}>N={n}</th>)}</tr></thead>
            <tbody>
              {strategies.map((s) => (
                <tr key={s}>
                  <td><i className={`sw sw-${s}`} />{s}</td>
                  {ns.map((n) => {
                    const p = panel.series[s].find((x) => x.n === n);
                    const v = p ? m.get(p) : null;
                    return <td className="r mono" key={n}>{v === null || v === undefined ? "-" : fmt(v)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="foot">Median over repetitions, {panel.source}.</p>
        </div>
      </div>
    </>
  );
}

export function ResultsPage() {
  const [data, setData] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}results.json`)
      .then((r) => { if (!r.ok) throw new Error("not found"); return r.json(); })
      .then(setData)
      .catch(() => setError("No measured results in this build."));
  }, []);
  return (
    <>
      <header className="pagehead"><h1>Results</h1></header>
      <h2>Exp 1 vs Exp 2 · this session</h2>
      <Session />
      {error && <p className="err" role="alert">{error}</p>}
      {data && (
        <>
          {data.exp12 && data.exp12.length > 0 && (
            <>
              <h2>Exp 1 vs Exp 2 · measured</h2>
              <BurstTable rows={data.exp12} title="All requests at once, near the cap" />
              {data.cap100 && data.cap100.length > 0 && <BurstTable rows={data.cap100} title="The same at cap 100" />}
            </>
          )}
          <h2>Exp 3 · throughput and latency · measured</h2>
          <Exp3 panels={data.panels} cal={data.calibration} />
          {data.exp4 && data.exp4.length > 0 && (
            <>
              <h2>Exp 4 · crash checks · measured</h2>
              <div className="scroll">
                <table className="compact">
                  <thead><tr><th>Fault</th><th className="r">Checks passed</th></tr></thead>
                  <tbody>
                    {data.exp4.map((r) => (
                      <tr key={r.label}><td>{r.label}</td><td className={`r mono ${r.passed < r.checks ? "badtext" : ""}`}>{r.passed} / {r.checks}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
