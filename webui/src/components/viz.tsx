// Small chart kit for the infographics (2026-11-01): stat tiles, a part-to-whole
// bar, columns (single or stacked), horizontal bars, and "download as image".
// Plain SVG/HTML, no chart library. Colours are CSS variables on .viz (index.css),
// validated for both themes; text never wears a series colour.
import React, { useEffect, useState } from 'react';
import { fmt, pct, useWidth } from '../vizUtils';

// ── tooltip ──────────────────────────────────────────────────────────────────
interface Tip { x: number; y: number; lines: string[] }

function useTip(): [Tip | null, (t: Tip | null) => void, React.ReactNode] {
  const [tip, setTip] = useState<Tip | null>(null);
  const node = tip ? (
    <div className="viz-tip" style={{ left: tip.x, top: tip.y }}>
      {tip.lines.map((l, i) => <div key={i} className={i === 0 ? 'viz-tip-head' : ''}>{l}</div>)}
    </div>
  ) : null;
  return [tip, setTip, node];
}

// ── stat tile ────────────────────────────────────────────────────────────────
export const StatTile: React.FC<{
  label: string; value: React.ReactNode; delta?: number | null; deltaLabel?: string; upIsGood?: boolean;
  spark?: number[]; status?: 'good' | 'warning' | 'critical'; sub?: React.ReactNode;
}> = ({ label, value, delta, deltaLabel, upIsGood = true, spark, status, sub }) => {
  const dir = delta ? (delta > 0) === upIsGood ? 'good' : 'bad' : '';
  return (
    <div className={`viz-tile ${status ? `viz-status-${status}` : ''}`}>
      <div className="viz-tile-label">
        {status && <span className="viz-status-dot" aria-hidden="true" />}
        {label}
      </div>
      <div className="viz-tile-value">{value}</div>
      {delta !== undefined && delta !== null && (
        <div className={`viz-delta ${dir}`}>{delta > 0 ? '+' : ''}{fmt(delta)} {deltaLabel}</div>
      )}
      {sub && <div className="viz-tile-sub">{sub}</div>}
      {spark && spark.length > 1 && <Spark values={spark} />}
    </div>
  );
};

const Spark: React.FC<{ values: number[] }> = ({ values }) => {
  const W = 120, H = 28, max = Math.max(...values, 1), min = Math.min(...values, 0);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * W},${H - 2 - ((v - min) / (max - min || 1)) * (H - 6)}`);
  const last = pts[pts.length - 1].split(',');
  return (
    <svg className="viz-spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true">
      <polyline points={pts.join(' ')} fill="none" stroke="var(--viz-muted)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={3} fill="var(--viz-s1)" stroke="var(--viz-surface)" strokeWidth={2} />
    </svg>
  );
};

// ── part-to-whole bar (hits vs misses, ok / warn / fail) ─────────────────────
export interface Part { label: string; value: number; color: string }

export const SegBar: React.FC<{ parts: Part[]; empty?: string }> = ({ parts, empty = 'No data yet' }) => {
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) return <div className="muted small">{empty}</div>;
  return (
    <div>
      <div className="viz-seg" role="img" aria-label={parts.map(p => `${p.label} ${p.value}`).join(', ')}>
        {parts.filter(p => p.value > 0).map(p => (
          <span key={p.label} style={{ flexGrow: p.value, background: p.color }} title={`${p.label}: ${fmt(p.value)} (${pct(p.value / total)})`} />
        ))}
      </div>
      <div className="viz-legend">
        {parts.map(p => (
          <span key={p.label}><i style={{ background: p.color }} />{p.label} <b>{fmt(p.value)}</b> <span className="muted">{pct(p.value / total)}</span></span>
        ))}
      </div>
    </div>
  );
};

// ── columns (single series or stacked) ───────────────────────────────────────
export interface Series { key: string; label: string; color: string }
export type Row = Record<string, number | string> & { label: string };

const niceMax = (v: number) => {
  if (v <= 4) return Math.max(1, Math.ceil(v));
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return [1, 2, 2.5, 5, 10].map(m => m * p).find(m => m >= v) || v;
};

export const Columns: React.FC<{
  rows: Row[]; series: Series[]; height?: number; ariaLabel: string; tickEvery?: number;
  markers?: { index: number; label: string }[];   // e.g. restarts: drawn as ticks under the axis
}> = ({ rows, series, height = 150, ariaLabel, tickEvery, markers = [] }) => {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [, setTip, tipNode] = useTip();
  const totals = rows.map(r => series.reduce((s, k) => s + (Number(r[k.key]) || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  // room for the widest y label (the exported image may use a wider fallback font)
  const left = Math.max(34, 14 + 8 * fmt(max).length), bottom = 22 + (markers.length ? 12 : 0), top = 8;
  const plotW = width - left - 4, plotH = height - top - bottom;
  const slot = plotW / Math.max(1, rows.length);
  const bw = Math.max(2, Math.min(24, slot - 2));
  const every = tickEvery || Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor(plotW / 70))));
  const y = (v: number) => top + plotH - (v / max) * plotH;
  return (
    <div ref={ref} className="viz-chart" onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} role="img" aria-label={ariaLabel}>
        {[0, 0.5, 1].map(f => (
          <g key={f}>
            <line x1={left} x2={width - 4} y1={y(max * f)} y2={y(max * f)} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={left - 6} y={y(max * f) + 3} textAnchor="end" className="viz-axis">{fmt(max * f)}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const x = left + i * slot + (slot - bw) / 2;
          let acc = 0;
          const segs = series.map(s => {
            const v = Number(r[s.key]) || 0;
            const y0 = y(acc), y1 = y(acc + v);
            acc += v;
            return { s, v, y0, y1 };
          }).filter(g => g.v > 0);
          return (
            <g key={i}>
              {segs.map((g, j) => {
                const h = Math.max(1, g.y0 - g.y1 - (j < segs.length - 1 ? 2 : 0));
                const isTop = j === segs.length - 1;
                return isTop
                  ? <path key={g.s.key} fill={g.s.color} d={roundedTop(x, g.y0 - h, bw, h, Math.min(4, bw / 2, h))} />
                  : <rect key={g.s.key} x={x} y={g.y0 - h} width={bw} height={h} fill={g.s.color} />;
              })}
              <rect x={left + i * slot} y={top} width={slot} height={plotH} fill="transparent"
                onMouseMove={e => {
                  const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                  setTip({ x: Math.min(e.clientX - box.left + 12, width - 170), y: e.clientY - box.top + 12,
                    lines: [String(r.label), ...series.map(s => `${s.label}: ${fmt(Number(r[s.key]) || 0)}`)] });
                }} />
              {i % every === 0 && (
                <text x={left + i * slot + slot / 2} y={height - (markers.length ? 14 : 4)} textAnchor="middle" className="viz-axis">{String(r.label)}</text>
              )}
            </g>
          );
        })}
        <line x1={left} x2={width - 4} y1={top + plotH} y2={top + plotH} stroke="var(--viz-axis)" strokeWidth={1} />
        {markers.map((m, k) => (
          <g key={k}>
            <path d={`M ${left + m.index * slot + slot / 2} ${height - 9} l 4 7 h -8 z`} fill="var(--viz-s7)" />
            <title>{m.label}</title>
          </g>
        ))}
      </svg>
      {tipNode}
    </div>
  );
};

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  return `M ${x} ${y + h} V ${y + r} Q ${x} ${y} ${x + r} ${y} H ${x + w - r} Q ${x + w} ${y} ${x + w} ${y + r} V ${y + h} Z`;
}

// ── horizontal bars (top problems, groups, attention) ────────────────────────
export const HBars: React.FC<{
  rows: { key: string; label: React.ReactNode; value: number; note?: React.ReactNode; color?: string; onClick?: () => void }[];
  max?: number; valueText?: (v: number) => string;
  stacked?: boolean;   // label on its own line (narrow cards, long labels)
}> = ({ rows, max, valueText = fmt, stacked = false }) => {
  const m = max ?? Math.max(1, ...rows.map(r => r.value));
  return (
    <div className={`viz-hbars ${stacked ? 'stacked' : ''}`}>
      {rows.map(r => (
        <div key={r.key} className={`viz-hbar ${r.onClick ? 'clickable' : ''}`} onClick={r.onClick}>
          <div className="viz-hbar-label">{r.label}</div>
          <div className="viz-hbar-track">
            <span style={{ width: `${Math.max(r.value > 0 ? 1.5 : 0, (r.value / m) * 100)}%`, background: r.color || 'var(--viz-s1)' }} />
          </div>
          <div className="viz-hbar-value">{valueText(r.value)}</div>
          {r.note && <div className="viz-hbar-note">{r.note}</div>}
        </div>
      ))}
    </div>
  );
};

/** A "table view" toggle: every chart's numbers stay reachable without hover. */
export const TableToggle: React.FC<{ rows: Row[]; series: Series[]; first: string }> = ({ rows, series, first }) => {
  const [open, setOpen] = useState(false);
  useEffect(() => { setOpen(false); }, [rows.length]); // eslint-disable-line react-hooks/set-state-in-effect -- new data: close
  return (
    <div className="viz-table-toggle" data-no-export="1">
      <button className="ghost-btn small-btn" onClick={() => setOpen(o => !o)}>{open ? 'Hide table' : 'Show as table'}</button>
      {open && (
        <div className="viz-table-wrap">
          <table className="cost-table">
            <thead><tr><th>{first}</th>{series.map(s => <th key={s.key}>{s.label}</th>)}</tr></thead>
            <tbody>{rows.filter(r => series.some(s => Number(r[s.key]))).map((r, i) => (
              <tr key={i}><td>{String(r.label)}</td>{series.map(s => <td key={s.key}>{fmt(Number(r[s.key]) || 0)}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
};
