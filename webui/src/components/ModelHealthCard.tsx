// Model health (2026-11-01): how each model behaved since ZenSynora started —
// calls, failures, average answer time, last error — the order agents now try
// fallbacks in (best first), models that cannot work with their provider, and
// the automatic / manual replacements made. "Replace in all agents" opens the
// picker in the Models page (onReplace).
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

export interface HealthRow {
  provider: string; model: string; calls: number; failures: number; fail_rate: number;
  latency: number | null; last_error: string; score: number;
}
interface Change { time: string; old: string; new: string; changed: string[]; reason: string }
interface Mismatch { who: string; provider: string; model: string; why: string }
interface HealthData { models: HealthRow[]; history: Change[]; mismatches: Mismatch[]; attempt_seconds: number; budget_seconds: number }

interface Props { onReplace: (provider: string, model: string) => void; refreshKey?: number }

const ModelHealthCard: React.FC<Props> = ({ onReplace, refreshKey }) => {
  const [data, setData] = useState<HealthData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<HealthData>('/api/models/health'));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- data load (and reload after a replacement)
  useEffect(() => { load(); }, [load, refreshKey]);

  return (
    <div className="settings-card">
      <div className="card-head">
        <h3>Model health</h3>
        <button className="ghost-btn" onClick={load}>↻ Refresh</button>
      </div>
      <p className="muted">
        Every agent falls back to the default model and the rotation list when its own model fails, best first by
        these numbers. One model gets {data?.attempt_seconds ?? 90} s to start answering, a whole call at most
        {' '}{data?.budget_seconds ?? 170} s. A model that is gone for good (unknown, refused) is replaced in every
        agent by the one that answered.
      </p>
      {error && <div className="notice error">{error}</div>}
      {data && data.mismatches.length > 0 && (
        <div className="notice error">
          <b>Cannot work with their provider:</b>
          <ul className="skipped-tools">{data.mismatches.map(m => (
            <li key={m.who}>{m.who}: <code>{m.provider}/{m.model}</code> — {m.why}{' '}
              <button className="ghost-btn small-btn" onClick={() => onReplace(m.provider, m.model)}>Replace…</button></li>))}
          </ul>
        </div>
      )}
      {data && data.models.length === 0 && <div className="muted">No model calls since ZenSynora started.</div>}
      {data && data.models.length > 0 && (
        <table className="cost-table">
          <thead><tr><th>Model</th><th>Calls</th><th>Failed</th><th>Avg. answer</th><th>Last error</th><th /></tr></thead>
          <tbody>
            {data.models.map(r => (
              <tr key={`${r.provider}/${r.model}`}>
                <td><code>{r.provider}/{r.model}</code></td>
                <td>{r.calls}</td>
                <td className={r.fail_rate > 0.5 ? 'cost-error' : ''}>{r.failures}</td>
                <td>{r.latency === null ? '—' : `${r.latency.toFixed(1)} s`}</td>
                <td className="small muted">{r.last_error ? r.last_error.slice(0, 120) : ''}</td>
                <td><button className="ghost-btn small-btn" title="Use another model wherever this one is set"
                  onClick={() => onReplace(r.provider, r.model)}>Replace in all agents…</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && data.history.length > 0 && (
        <details>
          <summary className="muted">Replacements ({data.history.length})</summary>
          <ul className="skipped-tools">{data.history.map((h, i) => (
            <li key={i}>{h.time}: <code>{h.old}</code> → <code>{h.new}</code> in {h.changed.join(', ')}
              {h.reason && h.reason !== 'by hand' ? <span className="muted small"> — {h.reason.slice(0, 140)}</span> : ' (by hand)'}</li>))}
          </ul>
        </details>
      )}
    </div>
  );
};

export default ModelHealthCard;
