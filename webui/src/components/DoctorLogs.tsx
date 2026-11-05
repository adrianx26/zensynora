// Doctor → Logs (2026-11-05): what the app did (one line per agent turn, tool or
// script, job, model call, setting saved, update, backup, knowledge write) and the
// app log; period 1/2/4/8/12/24 hours or days; export; AI analysis with options
// that only start when you click.
import React, { useCallback, useEffect, useState } from 'react';
import { api, downloadFile } from '../api';

interface ActRow { time: string; kind: string; name: string; status: string; ms: number | null; agent: string; detail: string }
interface AppRow { time: string; level: string; module: string; message: string }
interface LogsResult { total: number; rows: (ActRow & AppRow)[]; counts: { kind: Record<string, number>; status: Record<string, number>; level: Record<string, number> }; oldest: string | null }
interface Option { title: string; why: string; impact: string; steps: string[]; action: { type: string; target_kind?: string; target?: string; goal?: string; where?: string } }
interface Analysis { summary: string; options: Option[]; at: string; hours: number }

const AMOUNTS = [1, 2, 4, 8, 12, 24];
const KINDS: Record<string, string> = {
  agent: 'Agent answers', tool: 'Tools', script: 'Scripts', job: 'Jobs', model: 'Model calls',
  knowledge: 'Knowledge', setting: 'Settings', update: 'Updates', backup: 'Backups',
};
const STATUS_ICON: Record<string, string> = { ok: '✓', error: '✗', timeout: '⏱', repeat: '↺', cancelled: '–' };
const PAGE = 300;

const DoctorLogs: React.FC = () => {
  const [source, setSource] = useState<'activity' | 'app'>('activity');
  const [amount, setAmount] = useState(24);
  const [unit, setUnit] = useState<'h' | 'd'>('h');
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [level, setLevel] = useState('');
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [res, setRes] = useState<LogsResult | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState('');
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const hours = amount * (unit === 'd' ? 24 : 1);
  const filters = `source=${source}&hours=${hours}&kind=${encodeURIComponent(kind)}&status=${encodeURIComponent(status)}&level=${encodeURIComponent(level)}&q=${encodeURIComponent(search)}`;

  const load = useCallback(async () => {
    setErr(null);
    try {
      setRes(await api<LogsResult>(`/api/doctor/logs?${filters}&limit=${limit}`));
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [filters, limit]);
  useEffect(() => { load(); }, [load]);

  const exportAs = async (fmt: 'txt' | 'jsonl') => {
    setBusy(`export-${fmt}`); setErr(null);
    try {
      await downloadFile(`/api/doctor/logs/export?${filters}&fmt=${fmt}`, `zensynora-${source}.${fmt === 'txt' ? 'log' : 'jsonl'}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };
  const analyze = async () => {
    setBusy('analyze'); setErr(null); setNote(null);
    try {
      setAnalysis(await api<Analysis>('/api/doctor/logs/analyze', { method: 'POST', body: JSON.stringify({ hours }) }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };
  const act = async (o: Option, i: number) => {
    setBusy(`act-${i}`); setNote(null);
    try {
      const r = await api<{ name: string }>('/api/doctor/logs/act', { method: 'POST', body: JSON.stringify({ option: o }) });
      setNote({ ok: true, text: `Started “${r.name}”. Its report is on the Jobs page; a code change, if any, appears under Change proposals (Evolve).` });
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy('');
    }
  };
  const resetPage = () => setLimit(PAGE);

  return (
    <div className="doctor-logs">
      <div className="settings-card">
        <div className="probe-row">
          <span className="viz-toggle">
            <button className={`chip ${source === 'activity' ? 'active' : ''}`} onClick={() => { setSource('activity'); setLevel(''); resetPage(); }}
              title="One line per thing the app did">What the app did</button>
            <button className={`chip ${source === 'app' ? 'active' : ''}`} onClick={() => { setSource('app'); setKind(''); setStatus(''); resetPage(); }}
              title="The full program log (zensynora.log)">App log</button>
          </span>
          <span className="report-period">
            Last {AMOUNTS.map(a => (
              <button key={a} className={`chip ${amount === a ? 'active' : ''}`} onClick={() => { setAmount(a); resetPage(); }}>{a}</button>
            ))}
            <span className="viz-toggle">
              <button className={`chip ${unit === 'h' ? 'active' : ''}`} onClick={() => { setUnit('h'); resetPage(); }}>hours</button>
              <button className={`chip ${unit === 'd' ? 'active' : ''}`} onClick={() => { setUnit('d'); resetPage(); }}>days</button>
            </span>
          </span>
        </div>

        <div className="log-filters">
          {source === 'activity' ? (
            <>
              <select className="field-input" value={kind} onChange={e => { setKind(e.target.value); resetPage(); }}>
                <option value="">Everything{res ? ` (${Object.values(res.counts.kind).reduce((s, v) => s + v, 0)})` : ''}</option>
                {Object.entries(KINDS).map(([k, label]) => <option key={k} value={k}>{label}{res?.counts.kind[k] ? ` (${res.counts.kind[k]})` : ''}</option>)}
              </select>
              <select className="field-input" value={status} onChange={e => { setStatus(e.target.value); resetPage(); }}>
                <option value="">Any result</option>
                <option value="ok">✓ OK</option>
                <option value="error">✗ Failed</option>
                <option value="timeout">⏱ Timed out</option>
                <option value="repeat">↺ Repeat stopped</option>
              </select>
            </>
          ) : (
            <select className="field-input" value={level} onChange={e => { setLevel(e.target.value); resetPage(); }}>
              <option value="">All levels</option>
              {['CRITICAL', 'ERROR', 'WARNING', 'INFO'].map(l => <option key={l} value={l}>{l}{res?.counts.level[l] ? ` (${res.counts.level[l]})` : ''}</option>)}
            </select>
          )}
          <input className="field-input" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { setSearch(q); resetPage(); } }} />
          <button className="ghost-btn" onClick={() => { setSearch(q); resetPage(); }}>Search</button>
          <button className="ghost-btn" onClick={load}>↻</button>
          <span style={{ flex: 1 }} />
          <button className="ghost-btn" disabled={!!busy} onClick={() => exportAs('txt')}>{busy === 'export-txt' ? 'Exporting…' : '⬇ Export .log'}</button>
          <button className="ghost-btn" disabled={!!busy} onClick={() => exportAs('jsonl')}>{busy === 'export-jsonl' ? 'Exporting…' : '⬇ Export .jsonl'}</button>
        </div>
        <div className="muted small">
          {res ? `${res.total} line(s) in the last ${amount} ${unit === 'd' ? 'day(s)' : 'hour(s)'}` : 'Loading…'}
          {res?.oldest ? ` · the log reaches back to ${res.oldest}` : ''}
          {' · Export uses the filters above; the AI analysis reads everything in the period.'}
        </div>
        {err && <div className="notice error">{err}</div>}
      </div>

      <div className="settings-card doctor-ai">
        <div className="probe-row">
          <h3 style={{ margin: 0 }}>🤖 Analyse with AI</h3>
          <button className="primary-btn" disabled={!!busy} onClick={analyze}>{busy === 'analyze' ? 'The model is reading the logs…' : analysis ? '↻ Analyse again' : `Analyse the last ${amount} ${unit === 'd' ? 'day(s)' : 'hour(s)'}`}</button>
        </div>
        <p className="muted small">The default model reads what ran, what failed, what was slow, what repeated and what the log complains about, and proposes options. Nothing starts until you click one.</p>
        {note && <div className={`notice ${note.ok ? '' : 'error'}`}>{note.text}</div>}
        {analysis && (
          <>
            {analysis.summary && <div className="doctor-ai-text">{analysis.summary}</div>}
            {analysis.options.map((o, i) => (
              <div key={i} className="ai-item log-option">
                <div><span className={`badge impact-${o.impact}`}>{o.impact}</span> <b>{o.title}</b></div>
                {o.why && <div className="muted small">Why: {o.why}</div>}
                {o.steps.length > 0 && <ol className="small log-steps">{o.steps.map((s, j) => <li key={j}>{s}</li>)}</ol>}
                <div className="probe-row">
                  {o.action.type === 'evolve' && (
                    <button className="primary-btn small-btn" disabled={!!busy} onClick={() => act(o, i)}
                      title={`${o.action.target_kind}${o.action.target ? ' · ' + o.action.target : ''}: ${o.action.goal}`}>{busy === `act-${i}` ? 'Starting…' : '🧬 Ask the AI for this change'}</button>
                  )}
                  {(o.action.type === 'investigate' || o.action.type === 'none') && (
                    <button className="ghost-btn small-btn" disabled={!!busy} onClick={() => act(o, i)}>{busy === `act-${i}` ? 'Starting…' : '🔎 Investigate with an agent'}</button>
                  )}
                  {o.action.type === 'setting' && <span className="muted small">⚙ Change it yourself: {o.action.where || 'see the steps'}</span>}
                </div>
              </div>
            ))}
            <div className="muted small">Analysed {analysis.at} · last {analysis.hours >= 24 && analysis.hours % 24 === 0 ? `${analysis.hours / 24} day(s)` : `${analysis.hours} h`}</div>
          </>
        )}
      </div>

      <div className="settings-card">
        {res && res.rows.length === 0 && (
          <div className="muted">{source === 'activity' ? 'Nothing recorded in this period yet. The activity log starts with this version.' : 'No log lines in this period.'}</div>
        )}
        {res && res.rows.length > 0 && (
          <div className="log-table-wrap">
            <table className="cost-table log-table">
              <thead>{source === 'activity'
                ? <tr><th>Time</th><th>What</th><th></th><th>Name</th><th>Agent</th><th>Took</th><th>Detail</th></tr>
                : <tr><th>Time</th><th>Level</th><th>Module</th><th>Message</th></tr>}</thead>
              <tbody>
                {res.rows.map((r, i) => source === 'activity' ? (
                  <tr key={i} className={r.status !== 'ok' ? `log-${r.status}` : ''}>
                    <td className="nowrap">{r.time}</td><td>{KINDS[r.kind] || r.kind}</td>
                    <td title={r.status}>{STATUS_ICON[r.status] || r.status}</td><td>{r.name}</td>
                    <td className="muted">{r.agent ? `@${r.agent}` : ''}</td>
                    <td className="nowrap muted">{r.ms === null ? '' : r.ms >= 1000 ? `${(r.ms / 1000).toFixed(1)} s` : `${r.ms} ms`}</td>
                    <td className="muted small">{r.detail}</td>
                  </tr>
                ) : (
                  <tr key={i} className={['ERROR', 'CRITICAL'].includes(r.level) ? 'log-error' : r.level === 'WARNING' ? 'log-warning' : ''}>
                    <td className="nowrap">{r.time}</td><td>{r.level}</td><td className="muted">{r.module}</td>
                    <td className="small"><pre className="log-msg">{r.message}</pre></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {res && res.total > res.rows.length && (
          <div className="save-actions">
            <span className="muted small">Showing {res.rows.length} of {res.total}</span>
            <button className="ghost-btn" onClick={() => setLimit(l => l + PAGE)}>Show {Math.min(PAGE, res.total - res.rows.length)} more</button>
          </div>
        )}
      </div>
    </div>
  );
};

export default DoctorLogs;
