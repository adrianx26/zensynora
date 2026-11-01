// Doctor → Report: summary, errors viewer, the full report and downloads.
// Data: /api/doctor/report, /api/doctor/errors, /api/doctor/report/download
// (myclaw/web/doctor_api.py). Secrets are removed on the server.
import React, { useCallback, useEffect, useState } from 'react';
import { api, getAdminKey, getApiBase } from '../api';
import DoctorOverview from './DoctorOverview';

type Level = 'WARNING' | 'ERROR' | 'CRITICAL';

interface ErrorGroup {
  level: Level;
  key: string;
  count: number;
  first: string;
  last: string;
  module: string;
  message: string;
  samples: string[];
  traceback: boolean;
}

interface Restarts {
  starts: number;
  max_in_window: number;
  window_min: number;
  in_last_window: number;
  last: string | null;
  typical_gap_s: number | null;
}

type Kind = '' | 'failures' | 'jobs' | 'restarts';

interface JobRun { job: string; run: string | number; status: string; started: string; error: string }
interface RestartEvent { time: string; command: string; after_s: number | null }

interface ErrorView {
  kind?: Kind;
  kinds?: Record<'failures' | 'jobs' | 'restarts', number>;
  job_runs?: JobRun[];
  restart_events?: RestartEvent[];
  hours: number;
  counts: Record<Level, number>;
  groups: ErrorGroup[];
  total_groups: number;
  restarts: Restarts;
  log_exists: boolean;
  truncated: boolean;
}

interface ReportSummary {
  checks: Record<'ok' | 'warn' | 'fail' | 'skip', number>;
  overall: 'ok' | 'warn' | 'fail';
  errors: Record<Level, number>;
  error_groups: number;
  restarts: number;
  crash_loop: boolean;
  failed_jobs: number;
  open_proposals: number;
}

const PERIODS: { label: string; hours: number }[] = [
  { label: '1 h', hours: 1 }, { label: '2 h', hours: 2 }, { label: '4 h', hours: 4 }, { label: '8 h', hours: 8 },
  { label: '12 h', hours: 12 }, { label: '24 h', hours: 24 }, { label: '7 days', hours: 168 },
];
// 2026-10-31: show only failures, failed job runs or program restarts
const KINDS: { label: string; value: Exclude<Kind, ''>; help: string }[] = [
  { label: 'Failures', value: 'failures', help: 'Errors, plus warnings about work that failed, timed out or crashed' },
  { label: 'Failed jobs', value: 'jobs', help: 'Job runs that ended with an error, a timeout or were interrupted' },
  { label: 'Restarts', value: 'restarts', help: 'Each start of ZenSynora — many in a short time means a crash loop' },
];

function gap(s: number | null): string {
  if (s === null) return '';
  if (s < 120) return `${s} s after the previous start`;
  if (s < 7200) return `${Math.round(s / 60)} min after the previous start`;
  return `${Math.round(s / 3600)} h after the previous start`;
}
const LEVELS: { label: string; value: '' | Level }[] = [
  { label: 'All', value: '' }, { label: 'Warnings', value: 'WARNING' }, { label: 'Errors', value: 'ERROR' },
  { label: 'Critical', value: 'CRITICAL' },
];
const LEVEL_CLASS: Record<Level, string> = { WARNING: 'warn', ERROR: 'fail', CRITICAL: 'fail' };

async function fetchReport(format: 'md' | 'json' | 'zip', hours: number): Promise<{ blob: Blob; name: string }> {
  const headers: Record<string, string> = {};
  const key = getAdminKey();
  if (key) headers['X-API-Key'] = key;
  const res = await fetch(`${getApiBase()}/api/doctor/report/download?format=${format}&hours=${hours}`, { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const cd = res.headers.get('Content-Disposition') || '';
  const d = new Date();
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || `zensynora-report-${stamp}.${format}`;
  return { blob: await res.blob(), name };
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const Card: React.FC<{ label: string; value: React.ReactNode; tone?: 'ok' | 'warn' | 'fail' }> = ({ label, value, tone }) => (
  <div className={`report-stat ${tone ?? ''}`}>
    <div className="report-stat-value">{value}</div>
    <div className="report-stat-label">{label}</div>
  </div>
);

const DoctorReport: React.FC = () => {
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [hours, setHours] = useState(24);
  const [level, setLevel] = useState<'' | Level>('');
  const [kind, setKind] = useState<Kind>('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [view, setView] = useState<ErrorView | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      const r = await api<{ summary: ReportSummary }>('/api/doctor/report?hours=24&log_tail=false');
      setSummary(r.summary);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  const loadErrors = useCallback(async () => {
    try {
      const qs = new URLSearchParams({ hours: String(hours) });
      if (level && kind !== 'jobs' && kind !== 'restarts') qs.set('level', level);
      if (search) qs.set('q', search);
      if (kind) qs.set('kind', kind);
      setView(await api<ErrorView>(`/api/doctor/errors?${qs}`));
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, [hours, level, search, kind]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { loadErrors(); }, [loadErrors]);

  const download = async (format: 'md' | 'json' | 'zip') => {
    setBusy(format);
    setNotice(null);
    try {
      const { blob, name } = await fetchReport(format, hours);
      saveBlob(blob, name);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const showReport = async () => {
    if (markdown !== null) { setMarkdown(null); return; }
    setBusy('view');
    try {
      const { blob } = await fetchReport('md', hours);
      setMarkdown(await blob.text());
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice({ kind: 'ok', text: 'Copied.' });
    } catch {
      setNotice({ kind: 'error', text: 'Copy is not allowed here — select the text instead.' });
    }
  };

  const s = summary;
  const errCount = s ? s.errors.ERROR + s.errors.CRITICAL : 0;

  return (
    <>
      <div className="settings-card">
        <div className="card-head">
          <h3>Report</h3>
          <button className="ghost-btn" onClick={() => { loadSummary(); loadErrors(); }}>↻ Refresh</button>
        </div>
        <p className="muted">
          Everything needed to understand a problem: checks, grouped errors with their log lines, restarts, failed
          jobs and a summary of your settings. Keys and tokens are never included. In a terminal:{' '}
          <code>zensynora doctor --report</code>.
        </p>
        {s ? (
          <div className="report-stats">
            <Card label="Checks OK" value={s.checks.ok} tone="ok" />
            <Card label="Warnings" value={s.checks.warn} tone={s.checks.warn ? 'warn' : undefined} />
            <Card label="Failures" value={s.checks.fail} tone={s.checks.fail ? 'fail' : undefined} />
            <Card label="Errors in 24 h" value={errCount} tone={errCount ? 'fail' : undefined} />
            <Card label="Warnings in 24 h" value={s.errors.WARNING} tone={s.errors.WARNING ? 'warn' : undefined} />
            <Card label={s.crash_loop ? 'Restarts — crash loop' : 'Restarts'} value={s.restarts} tone={s.crash_loop ? 'fail' : undefined} />
            <Card label="Failed jobs" value={s.failed_jobs} tone={s.failed_jobs ? 'warn' : undefined} />
          </div>
        ) : <div className="muted">Collecting…</div>}
        <div className="probe-row">
          <button className="ghost-btn" onClick={showReport} disabled={busy !== null}>
            {busy === 'view' ? 'Loading…' : markdown !== null ? 'Hide report' : 'View report'}
          </button>
          <span className="muted">Download:</span>
          <button className="ghost-btn" onClick={() => download('md')} disabled={busy !== null}>{busy === 'md' ? '…' : 'Markdown'}</button>
          <button className="ghost-btn" onClick={() => download('json')} disabled={busy !== null}>{busy === 'json' ? '…' : 'JSON'}</button>
          <button className="ghost-btn" onClick={() => download('zip')} disabled={busy !== null}>{busy === 'zip' ? '…' : 'ZIP (report + log)'}</button>
        </div>
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}
        {markdown !== null && (
          <div className="report-view-wrap">
            <button className="ghost-btn small-btn" onClick={() => copy(markdown)}>Copy</button>
            <pre className="report-view">{markdown}</pre>
          </div>
        )}
      </div>

      {/* 2026-11-01: one period for the health overview and the errors below */}
      <div className="chip-row report-period">
        <span className="muted small">Period:</span>
        {PERIODS.map(p => (
          <button key={p.hours} className={`chip ${hours === p.hours ? 'active' : ''}`} onClick={() => setHours(p.hours)}>{p.label}</button>
        ))}
      </div>

      <DoctorOverview hours={hours} summary={summary}
        onProblem={m => { setKind(''); setLevel(''); setQuery(m); setSearch(m); }} />

      <div className="settings-card">
        <h3>Errors</h3>
        <div className="report-filters">
          <div className="chip-row">
            {LEVELS.map(l => (
              <button key={l.label} className={`chip ${level === l.value && kind !== 'jobs' && kind !== 'restarts' ? 'active' : ''}`}
                onClick={() => { setLevel(l.value); if (kind !== 'failures') setKind(''); }}>{l.label}</button>
            ))}
          </div>
          <div className="chip-row">
            {KINDS.map(k => (
              <button key={k.value} title={k.help} className={`chip ${kind === k.value ? 'active' : ''}`}
                onClick={() => setKind(kind === k.value ? '' : k.value)}>
                {k.label}{view?.kinds ? <span className="chip-count">{view.kinds[k.value]}</span> : null}
              </button>
            ))}
          </div>
          <form className="field-row" onSubmit={e => { e.preventDefault(); setSearch(query.trim()); }}>
            <input className="field-input" placeholder="Search messages…" value={query} onChange={e => setQuery(e.target.value)} />
            <button className="ghost-btn" type="submit">Search</button>
          </form>
        </div>
        {view && (
          <div className="muted small">
            {view.counts.CRITICAL} critical · {view.counts.ERROR} errors · {view.counts.WARNING} warnings
            {kind !== 'jobs' && kind !== 'restarts' && <>{' '}({view.total_groups} kind{view.total_groups === 1 ? '' : 's'})</>}
            {view.restarts.starts > 0 && <> · {view.restarts.starts} restart(s), at most {view.restarts.max_in_window} in {view.restarts.window_min} min</>}
            {view.truncated && <> · only the newest part of the log was read</>}
          </div>
        )}
        {view && !view.log_exists && kind !== 'jobs' && <div className="muted">No log file yet.</div>}
        {view && view.log_exists && kind !== 'jobs' && kind !== 'restarts' && view.groups.length === 0 && <div className="muted">Nothing found for this filter.</div>}
        {view && kind === 'jobs' && (
          (view.job_runs || []).length === 0
            ? <div className="muted">No failed job runs in this period.</div>
            : <div className="doctor-list">
              {(view.job_runs || []).map(r => {
                const id = `job|${r.run}`;
                const expanded = open === id;
                return (
                  <div key={id} className="error-group">
                    <button className="error-group-head" onClick={() => setOpen(expanded ? null : id)} aria-expanded={expanded}>
                      <span className={`badge doctor-badge ${r.status === 'interrupted' ? 'warn' : 'fail'}`}>{r.status}</span>
                      <span className="error-msg">{r.job}</span>
                      <span className="muted small">{r.started}</span>
                    </button>
                    {expanded && (
                      <div className="error-group-body">
                        <div className="probe-row">
                          <span className="muted small">run {r.run} · started {r.started}</span>
                          <button className="ghost-btn small-btn" onClick={() => copy(`${r.job} ${r.status} ${r.started}\n${r.error}`)}>Copy</button>
                        </div>
                        <pre className="report-view">{r.error || 'No error text was stored for this run.'}</pre>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
        )}
        {view && kind === 'restarts' && (
          (view.restart_events || []).length === 0
            ? <div className="muted">No restarts in this period.</div>
            : <div className="doctor-list">
              {view.restarts.max_in_window > 10 && (
                <div className="notice error">Crash loop: {view.restarts.max_in_window} starts within {view.restarts.window_min} minutes.</div>
              )}
              {(view.restart_events || []).map((r, i) => (
                <div key={`${r.time}-${i}`} className="restart-row">
                  <span className={`badge doctor-badge ${r.after_s !== null && r.after_s < 120 ? 'fail' : 'ok'}`}>{r.command}</span>
                  <span>{r.time}</span>
                  <span className="muted small">{gap(r.after_s)}</span>
                </div>
              ))}
            </div>
        )}
        <div className="doctor-list">
          {view?.groups.map(g => {
            const id = `${g.level}|${g.key}`;
            const expanded = open === id;
            return (
              <div key={id} className="error-group">
                <button className="error-group-head" onClick={() => setOpen(expanded ? null : id)} aria-expanded={expanded}>
                  <span className={`badge doctor-badge ${LEVEL_CLASS[g.level]}`}>{g.level.toLowerCase()}</span>
                  <span className="error-count">×{g.count}</span>
                  <span className="error-msg">{g.message}</span>
                  <span className="muted small">{g.module} · last {g.last}{g.traceback ? ' · traceback' : ''}</span>
                </button>
                {expanded && (
                  <div className="error-group-body">
                    <div className="probe-row">
                      <span className="muted small">first {g.first} · last {g.last}</span>
                      <button className="ghost-btn small-btn" onClick={() => copy(g.samples.join('\n\n'))}>Copy</button>
                    </div>
                    <pre className="report-view">{g.samples.join('\n\n')}</pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
};

export default DoctorReport;
