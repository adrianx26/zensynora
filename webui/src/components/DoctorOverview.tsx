// Doctor → Report → Health overview (2026-11-01): the app's health on one
// screen for the chosen period — checks, warnings / errors / critical over time
// with restarts, job runs, the biggest problems and how the models behave.
// Data: /api/doctor/timeline, /api/models/health (+ the report summary).
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { Columns, HBars, SegBar, StatTile, TableToggle, type Row } from './viz';
import { downloadPanel, fmt, pct } from '../vizUtils';

interface Bucket { t: number; label: string; WARNING: number; ERROR: number; CRITICAL: number; failures: number; restarts: number; jobs_ok: number; jobs_failed: number }
interface Timeline {
  hours: number; bucket_seconds: number; buckets: Bucket[];
  jobs: { ok: number; failed: number; other: number };
  totals: { WARNING: number; ERROR: number; CRITICAL: number; failures: number; restarts: number };
  top: { level: string; count: number; message: string; module: string; failure: boolean }[];
}
interface ModelRow { provider: string; model: string; calls: number; failures: number; fail_rate: number; latency: number | null }
export interface HealthSummary { checks: Record<'ok' | 'warn' | 'fail' | 'skip', number>; overall: 'ok' | 'warn' | 'fail'; crash_loop: boolean }

const LEVELS = [
  { key: 'WARNING', label: 'Warnings', color: 'var(--viz-warning)' },
  { key: 'ERROR', label: 'Errors', color: 'var(--viz-serious)' },
  { key: 'CRITICAL', label: 'Critical', color: 'var(--viz-critical)' },
];
const JOBS = [
  { key: 'jobs_ok', label: 'Job runs OK', color: 'var(--viz-good)' },
  { key: 'jobs_failed', label: 'Job runs failed', color: 'var(--viz-critical)' },
];

const periodText = (h: number) => (h >= 48 ? `${Math.round(h / 24)} days` : `${h} h`);
const bucketText = (s: number) => (s >= 86_400 ? 'day' : s >= 3600 ? `${s / 3600} h` : `${s / 60} min`);

const DoctorOverview: React.FC<{
  hours: number; summary: HealthSummary | null; onProblem: (message: string) => void;
}> = ({ hours, summary, onProblem }) => {
  const [tl, setTl] = useState<Timeline | null>(null);
  const [models, setModels] = useState<ModelRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const [t, m] = await Promise.all([
        api<Timeline>(`/api/doctor/timeline?hours=${hours}`),
        api<{ models: ModelRow[] }>('/api/models/health').catch(() => ({ models: [] as ModelRow[] })),
      ]);
      setTl(t);
      setModels(m.models);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [hours]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- data load (and reload when the period changes)
  useEffect(() => { load(); }, [load]);

  if (error) return <div className="notice error">{error}</div>;
  if (!tl) return <div className="settings-card muted">Collecting…</div>;
  const rows = tl.buckets as unknown as Row[];
  const markers = tl.buckets.flatMap((b, i) => (b.restarts ? [{ index: i, label: `${b.label}: ${b.restarts} restart(s)` }] : []));
  const errors = tl.totals.ERROR + tl.totals.CRITICAL;
  const jobsTotal = tl.jobs.ok + tl.jobs.failed;
  const failingModels = models.filter(m => m.calls > 0 && m.fail_rate > 0.5);
  const overall = summary?.overall === 'fail' || tl.totals.CRITICAL || summary?.crash_loop ? 'critical'
    : summary?.overall === 'warn' || errors || tl.jobs.failed ? 'warning' : 'good';
  const overallText = overall === 'good' ? 'Healthy' : overall === 'warning' ? 'Needs a look' : 'Problems';

  return (
    <div className="viz settings-card" ref={panel}>
      <div className="viz-head">
        <h3>Health overview <span className="muted small">last {periodText(hours)}</span></h3>
        <span className="row-actions" data-no-export="1">
          <button className="ghost-btn small-btn" onClick={load}>↻ Refresh</button>
          <button className="ghost-btn small-btn" onClick={() => downloadPanel(panel.current, 'zensynora-health')}>⬇ Download as image</button>
        </span>
      </div>

      <div className="viz-tiles">
        <StatTile label="Overall" status={overall} value={overallText}
          sub={summary ? <>{summary.checks.fail} failed · {summary.checks.warn} warning checks</> : undefined} />
        <StatTile label="Errors + critical" status={errors ? 'critical' : 'good'} value={fmt(errors)}
          sub={<>{fmt(tl.totals.CRITICAL)} critical · {fmt(tl.totals.WARNING)} warnings</>} />
        <StatTile label="Failures" status={tl.totals.failures ? 'warning' : 'good'} value={fmt(tl.totals.failures)} sub="errors + failed / timed-out work" />
        <StatTile label="Job runs" status={tl.jobs.failed ? 'warning' : 'good'} value={jobsTotal ? `${fmt(tl.jobs.ok)} / ${fmt(jobsTotal)} ok` : '—'}
          sub={jobsTotal ? `${fmt(tl.jobs.failed)} failed · ${pct(tl.jobs.failed / jobsTotal)}` : 'no runs in this period'} />
        <StatTile label={summary?.crash_loop ? 'Restarts — crash loop' : 'Restarts'} status={summary?.crash_loop ? 'critical' : tl.totals.restarts > 3 ? 'warning' : 'good'}
          value={fmt(tl.totals.restarts)} />
        <StatTile label="Models failing" status={failingModels.length ? 'warning' : 'good'} value={`${failingModels.length} / ${models.filter(m => m.calls).length}`}
          sub="since ZenSynora started" />
      </div>

      <div className="viz-block" style={{ marginTop: 14 }}>
        <h4>Warnings, errors and critical per {bucketText(tl.bucket_seconds)} <span className="muted small">▲ = restart</span></h4>
        <div className="viz-legend" style={{ marginTop: 0, marginBottom: 4 }}>
          {LEVELS.map(l => <span key={l.key}><i style={{ background: l.color }} />{l.label} <b>{fmt(tl.totals[l.key as 'WARNING'])}</b></span>)}
          <span><i style={{ background: 'var(--viz-s7)' }} />Restarts <b>{fmt(tl.totals.restarts)}</b></span>
        </div>
        <Columns rows={rows} series={LEVELS} markers={markers} ariaLabel="Warnings, errors and critical over time" height={160} />
        <TableToggle rows={rows} series={[...LEVELS, { key: 'failures', label: 'Failures', color: '' }, { key: 'restarts', label: 'Restarts', color: '' }]} first="From" />
      </div>

      <div className="viz-grid2">
        <div className="viz-block">
          <h4>Job runs per {bucketText(tl.bucket_seconds)}</h4>
          {jobsTotal === 0 ? <div className="muted small">No job runs in this period.</div> : (
            <>
              <div className="viz-legend" style={{ marginTop: 0, marginBottom: 4 }}>
                {JOBS.map(j => <span key={j.key}><i style={{ background: j.color }} />{j.label} <b>{fmt(j.key === 'jobs_ok' ? tl.jobs.ok : tl.jobs.failed)}</b></span>)}
              </div>
              <Columns rows={rows} series={JOBS} ariaLabel="Job runs over time" height={120} />
            </>
          )}
        </div>
        <div className="viz-block">
          <h4>Checks <span className="muted small">(the Checks tab)</span></h4>
          {summary ? (
            <SegBar parts={[
              { label: 'OK', value: summary.checks.ok, color: 'var(--viz-good)' },
              { label: 'Warning', value: summary.checks.warn, color: 'var(--viz-warning)' },
              { label: 'Failed', value: summary.checks.fail, color: 'var(--viz-critical)' },
              { label: 'Skipped', value: summary.checks.skip || 0, color: 'var(--viz-rest)' },
            ]} />
          ) : <div className="muted small">Collecting…</div>}
        </div>
      </div>

      <div className="viz-grid2">
        <div className="viz-block">
          <h4>Biggest problems <span className="muted small">click to show them below</span></h4>
          {tl.top.length === 0 ? <div className="muted small">Nothing logged in this period.</div> : (
            <HBars stacked rows={tl.top.map((p, i) => ({
              key: `${i}`,
              label: <>{p.level === 'CRITICAL' ? '⛔ ' : p.level === 'ERROR' ? '✖ ' : '⚠ '}{p.message}</>,
              value: p.count,
              color: p.level === 'CRITICAL' ? 'var(--viz-critical)' : p.level === 'ERROR' ? 'var(--viz-serious)' : 'var(--viz-warning)',
              note: `${p.level.toLowerCase()} · ${p.module}`,
              onClick: () => onProblem(p.message.slice(0, 60)),
            }))} />
          )}
        </div>
        <div className="viz-block">
          <h4>Models <span className="muted small">share of calls that failed · since start</span></h4>
          {models.filter(m => m.calls).length === 0 ? <div className="muted small">No model calls yet.</div> : (
            <HBars stacked max={100} valueText={v => `${Math.round(v)}%`}
              rows={models.filter(m => m.calls).slice(0, 8).map(m => ({
                key: `${m.provider}/${m.model}`,
                label: <>{m.fail_rate > 0.5 ? '⚠ failing now · ' : ''}{m.model}</>,
                value: Math.round((m.failures / m.calls) * 100),
                color: m.fail_rate > 0.5 ? 'var(--viz-critical)' : m.failures ? 'var(--viz-warning)' : 'var(--viz-good)',
                note: `${m.provider} · ${m.calls} calls · ${m.failures} failed · ${m.latency === null ? 'no answer yet' : `${m.latency.toFixed(1)} s avg.`}`,
              }))} />
          )}
        </div>
      </div>
    </div>
  );
};

export default DoctorOverview;
