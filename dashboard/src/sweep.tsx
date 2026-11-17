import { useState } from "react";
import { Me, SweepCell, api, clock, fmt } from "./api";
import { Chips, Legend, LineChart, RunControls, RunPicker, Series, StatusPill, useRuns } from "./common";

// Exp 3: a short cost sweep run in-process on the server, over all four
// strategies. The full sweep is the terminal's (make exp3); its results are on
// the Results page.

export const METRICS = {
  throughput: { label: "Throughput", unit: "per s", get: (c: SweepCell) => c.throughput },
  p50_ms: { label: "p50 latency", unit: "ms", get: (c: SweepCell) => c.p50_ms },
  p95_ms: { label: "p95 latency", unit: "ms", get: (c: SweepCell) => c.p95_ms },
  p99_ms: { label: "p99 latency", unit: "ms", get: (c: SweepCell) => c.p99_ms },
  retries: { label: "Retries", unit: "retries", get: (c: SweepCell) => c.retries },
  gave_up: { label: "Gave up", unit: "gave up", get: (c: SweepCell) => c.gave_up },
} as const;
type Metric = keyof typeof METRICS;

export function MetricSelect({ value, onChange, keys }: { value: string; onChange: (m: string) => void; keys: string[] }) {
  return (
    <div className="seg" role="group" aria-label="Metric">
      {keys.map((k) => (
        <button key={k} className={value === k ? "on" : ""} aria-pressed={value === k} onClick={() => onChange(k)}>
          {(METRICS as Record<string, { label: string }>)[k]?.label ?? k}
        </button>
      ))}
    </div>
  );
}

export function SweepPage({ me }: { me: Me }) {
  const [metric, setMetric] = useState<Metric>("throughput");
  const { runs, id, setPicked, detail } = useRuns(3);
  const running = runs.find((r) => r.status === "running") ?? null;
  const cells = (detail?.progress ?? []) as SweepCell[];
  const params = detail?.params as { strategies?: string[]; ns?: number[]; seconds?: number; duration_ms?: number } | undefined;
  const strategies = params?.strategies ?? ["naive", "atomic", "for_update", "serializable"];
  const ns = params?.ns ?? [1, 8, 32];
  const m = METRICS[metric];

  const series: Series = {};
  for (const s of strategies) series[s] = cells.filter((c) => c.strategy === s).map((c) => ({ n: c.n, v: m.get(c) }));

  return (
    <>
      <header className="pagehead">
        <h1>Exp 3 · Cost</h1>
        <Chips items={["4 strategies", `N ${ns.join(", ")}`, `${params?.duration_ms ?? 10} ms operation`, `${params?.seconds ?? 3} s per point`]} />
      </header>
      <div className="toolbar">
        <RunControls me={me} running={running} ready onStart={async () => { await api("/demo/runs", "POST", { exp: 3 }); setPicked(null); }} startLabel="Start mini sweep" />
        <RunPicker runs={runs} id={id} onPick={setPicked} />
      </div>
      {detail && (
        <>
          <div className="runhead">
            <StatusPill run={detail} />
            <span className="meta">started by {detail.started_by ?? "-"} at {clock(detail.started_at)} · {cells.length} of {strategies.length * ns.length} points</span>
          </div>
          <section className="panel">
          <div className="panelhead">
            <MetricSelect value={metric} onChange={(k) => setMetric(k as Metric)} keys={Object.keys(METRICS)} />
            <Legend strategies={strategies} />
          </div>
          <div className="charts">
            <LineChart series={series} title={m.label} unit={m.unit} />
            <div className="scroll">
              <table className="compact">
                <thead><tr><th>Strategy</th>{ns.map((n) => <th className="r" key={n}>N={n}</th>)}</tr></thead>
                <tbody>
                  {strategies.map((s) => (
                    <tr key={s}>
                      <td><i className={`sw sw-${s}`} />{s}</td>
                      {ns.map((n) => {
                        const c = cells.find((x) => x.strategy === s && x.n === n);
                        const v = c ? m.get(c) : null;
                        return <td className="r mono" key={n}>{v === null || v === undefined ? "-" : fmt(v)}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          </section>
        </>
      )}
      {!detail && runs.length === 0 && <p className="empty">No runs yet.</p>}
      <p className="foot mono">full sweep: make exp3 · measured results: Results</p>
    </>
  );
}
