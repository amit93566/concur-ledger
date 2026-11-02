import { useEffect, useState } from "react";

type Point = { n: number; median: number; min: number; max: number; reps: number; gave_up_per_s: number; denial_rate: number; p99_ms: number };
type Panel = { title: string; regime: string; duration_ms: number; source: string; series: Record<string, Point[]> };
type Calibration = { epochs: number; reps: number; train_s_median: number; train_s_min: number; train_s_max: number; epoch_s_median: number; epsilon: number; delta: number; accuracy_median: number; opacus: string };
type Results = { calibration: Calibration | null; panels: Panel[] };

// Same strategy colours and order as the report's figures.
const STYLE: Record<string, { label: string; dash?: string }> = {
  atomic: { label: "atomic" },
  for_update: { label: "for_update" },
  serializable: { label: "serializable" },
  naive: { label: "naive (unsafe)", dash: "2 4" },
};

function Chart({ panel }: { panel: Panel }) {
  const [hover, setHover] = useState<{ s: string; p: Point; x: number; y: number } | null>(null);
  const W = 460, H = 250, L = 46, R = 12, T = 12, B = 34;
  const all = Object.values(panel.series).flat();
  const ns = [...new Set(all.map((p) => p.n))].sort((a, b) => a - b);
  const top = Math.max(...all.map((p) => p.max)) * 1.08;
  // N doubles at each step, so the axis is logarithmic.
  const x = (n: number) => ns.length < 2 ? L : L + (Math.log(n / ns[0]) / Math.log(ns[ns.length - 1] / ns[0])) * (W - L - R);
  const y = (v: number) => H - B - (v / top) * (H - T - B);
  const step = Math.pow(10, Math.floor(Math.log10(top)));
  const unit = top / step > 5 ? step * 2 : top / step > 2 ? step : step / 2;
  const ticks = Array.from({ length: Math.floor(top / unit) + 1 }, (_, i) => i * unit);

  return (
    <figure className="chart">
      <figcaption>{panel.title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${panel.title}: completed cycles per second against N`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 7} y={y(t) + 4} textAnchor="end" className="tick">{t}</text>
          </g>
        ))}
        {ns.map((n) => <text key={n} x={x(n)} y={H - B + 17} textAnchor="middle" className="tick">{n}</text>)}
        <text x={(L + W - R) / 2} y={H - 3} textAnchor="middle" className="tick">concurrent workers N</text>
        {Object.entries(panel.series).map(([s, pts]) => (
          <g key={s}>
            <polyline points={pts.map((p) => `${x(p.n)},${y(p.median)}`).join(" ")} fill="none" strokeWidth={2} strokeDasharray={STYLE[s].dash} className={`line-${s}`} />
            {pts.map((p) => (
              <g key={p.n}>
                <circle cx={x(p.n)} cy={y(p.median)} r={4} className={`dot-${s}`} />
                <circle cx={x(p.n)} cy={y(p.median)} r={11} fill="transparent" onMouseEnter={() => setHover({ s, p, x: x(p.n), y: y(p.median) })} />
              </g>
            ))}
          </g>
        ))}
      </svg>
      {hover && (
        <div className="tip" style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }}>
          <b>{STYLE[hover.s].label}, N={hover.p.n}</b><br />
          {hover.p.median} cycles/s ({hover.p.min}-{hover.p.max})<br />
          {hover.p.reps} reps, {hover.p.gave_up_per_s} gave up/s, {Math.round(hover.p.denial_rate * 100)}% denied
        </div>
      )}
      <p className="src">Median cycles/s, from the {panel.source}.</p>
    </figure>
  );
}

function Table({ panel }: { panel: Panel }) {
  const ns = [...new Set(Object.values(panel.series).flat().map((p) => p.n))].sort((a, b) => a - b);
  return (
    <div className="scroll">
      <table>
        <caption>{panel.title}</caption>
        <thead><tr><th>Strategy</th>{ns.map((n) => <th className="r" key={n}>N={n}</th>)}</tr></thead>
        <tbody>
          {Object.entries(panel.series).map(([s, pts]) => (
            <tr key={s}>
              <td><i className={`sw sw-${s}`} />{STYLE[s].label}</td>
              {ns.map((n) => {
                const p = pts.find((q) => q.n === n);
                return <td className="r mono" key={n}>{p ? p.median : "-"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResultsPage() {
  const [data, setData] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}results.json`)
      .then((r) => { if (!r.ok) throw new Error("not found"); return r.json(); })
      .then(setData)
      .catch(() => setError("No results in this build. Run python experiments/dashboard_results.py, then rebuild."));
  }, []);
  if (error) return <p className="err" role="alert">{error}</p>;
  if (!data) return null;
  const c = data.calibration;
  return (
    <>
      <div className="head">
        <div>
          <h1>Experiment 3 results</h1>
          <p className="sub">What safety costs, including at a duration calibrated from real DP-SGD training. These are stored results, not live data.</p>
        </div>
      </div>
      {c && (
        <>
          <h2>DP-SGD calibration</h2>
          <dl className="stats wide">
            <div><dt>Training time</dt><dd>{Math.round(c.train_s_median)} s</dd><dd className="sm">{c.train_s_min}-{c.train_s_max} s, {c.reps} runs</dd></div>
            <div><dt>One epoch</dt><dd>{c.epoch_s_median.toFixed(1)} s</dd><dd className="sm">{c.epochs} epochs per run</dd></div>
            <div><dt>ε</dt><dd>{c.epsilon}</dd><dd className="sm">at δ = {c.delta}, Opacus {c.opacus}</dd></div>
            <div><dt>Test accuracy</dt><dd>{(c.accuracy_median * 100).toFixed(1)}%</dd><dd className="sm">MNIST, CPU only</dd></div>
          </dl>
        </>
      )}
      <h2>Throughput</h2>
      <ul className="key">
        {Object.entries(STYLE).map(([s, v]) => <li key={s}><i className={`sw sw-${s}`} />{v.label}</li>)}
      </ul>
      <p className="sub">Lines lie on top of each other where strategies tie. At 14 s all four tie unless the cap is limiting.</p>
      <div className="charts">{data.panels.map((p) => <Chart key={p.title} panel={p} />)}</div>
      <h2>The same numbers</h2>
      {data.panels.map((p) => <Table key={p.title} panel={p} />)}
    </>
  );
}
