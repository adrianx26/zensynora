import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import DoctorReport from './DoctorReport';
import ProposalsPanel from './ProposalsPanel';

// Same checks as `zensynora doctor` (myclaw/doctor/checks.py).
interface CheckResult {
  id: string;
  title: string;
  category: string;
  status: 'ok' | 'warn' | 'fail' | 'skip';
  detail: string;
  hint: string;
  fixable: boolean;
  duration_ms: number;
}

interface Report {
  overall: 'ok' | 'warn' | 'fail';
  summary: Record<string, number>;
  version: string;
  generated_at: string;
  results: CheckResult[];
}

const ICON: Record<CheckResult['status'], string> = { ok: '✔', warn: '!', fail: '✘', skip: '–' };
const ORDER: Record<CheckResult['status'], number> = { fail: 0, warn: 1, ok: 2, skip: 3 };

const DoctorView: React.FC = () => {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fixNote, setFixNote] = useState<string[] | null>(null);
  const [tab, setTab] = useState<'checks' | 'report'>('checks');

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await api<Report>('/api/system/doctor'));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { run(); }, [run]);

  const fix = async () => {
    setLoading(true);
    try {
      const res = await api<{ applied: string[]; report: Report }>('/api/system/doctor/fix', { method: 'POST' });
      setFixNote(res.applied.length ? res.applied : ['Nothing needed fixing automatically.']);
      setReport(res.report);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const categories = report ? Array.from(new Set(report.results.map(r => r.category))) : [];
  const fixable = report?.results.some(r => r.fixable) ?? false;

  return (
    <div className="settings-view">
      <div className="settings-tabs">
        <button className={`settings-tab ${tab === 'checks' ? 'active' : ''}`} onClick={() => setTab('checks')}>Checks</button>
        <button className={`settings-tab ${tab === 'report' ? 'active' : ''}`} onClick={() => setTab('report')}>Report</button>
      </div>
      {tab === 'report' && <div className="settings-body"><DoctorReport /></div>}
      {tab === 'checks' && <div className="settings-body">
        <div className="settings-card">
          <h3>
            System doctor
            {report && <span className={`badge doctor-badge ${report.overall}`}>{report.overall === 'ok' ? 'healthy' : report.overall === 'warn' ? 'warnings' : 'problems'}</span>}
          </h3>
          <p className="muted">
            Checks packages, provider libraries, your settings, the model server, databases, permissions, recent
            errors, restarts, custom tools and the knowledge index. The Report tab shows the errors with their log lines
            and lets you download everything as one file. The same checks run in a terminal with <code>zensynora doctor</code>.
          </p>
          <div className="probe-row">
            <button className="ghost-btn" onClick={run} disabled={loading}>{loading ? 'Checking…' : '↻ Run checks again'}</button>
            {fixable && <button className="primary-btn" onClick={fix} disabled={loading}>Fix automatically</button>}
            {report && (
              <span className="muted">
                {report.summary.ok} ok · {report.summary.warn} warnings · {report.summary.fail} failures · {report.generated_at}
              </span>
            )}
          </div>
          {fixNote && <div className="notice">{fixNote.map((n, i) => <div key={i}>{n}</div>)}</div>}
          {error && <div className="notice error">{error}</div>}
        </div>

        {/* v0.7.1: approval-gated self-update — agents' code proposals and lessons */}
        <ProposalsPanel />

        {report && categories.map(cat => (
          <div className="settings-card" key={cat}>
            <h3 className="doctor-cat">{cat}</h3>
            <div className="doctor-list">
              {report.results
                .filter(r => r.category === cat)
                .sort((a, b) => ORDER[a.status] - ORDER[b.status])
                .map(r => (
                  <div className={`doctor-row ${r.status}`} key={r.id}>
                    <span className={`doctor-icon ${r.status}`} aria-label={r.status}>{ICON[r.status]}</span>
                    <div className="doctor-text">
                      <div><b>{r.title}</b> <span className="muted">— {r.detail}</span></div>
                      {r.hint && (r.status === 'warn' || r.status === 'fail') && (
                        <div className="doctor-hint">→ {r.hint}{r.fixable ? ' (can be fixed automatically)' : ''}</div>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>}
    </div>
  );
};

export default DoctorView;
