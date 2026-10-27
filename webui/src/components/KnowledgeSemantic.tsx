// Semantic search of the knowledge base (KB phase 3, 2026-10-24): status card
// (installed? N of M notes embedded, Backfill with progress, Install when the
// optional package is missing) and the search-mode picker. Data:
// /api/knowledge/semantic/*. Everything runs on this computer.
import React, { useState } from 'react';
import { api } from '../api';
import type { SearchMode, SemanticStatus, useSemanticStatus } from '../knowledgeSemantic';

export const SearchModePicker: React.FC<{ mode: SearchMode; onChange: (m: SearchMode) => void; status: SemanticStatus | null; note?: string | null }> = ({ mode, onChange, status, note }) => {
  if (!status?.enabled) return null;
  const title = note ? `Semantic search not used: ${note}` : !status.ready ? (status.reason || 'Semantic search is not ready') : 'How to match notes';
  return (
    <select className="field-input kb-mode" value={mode} onChange={e => onChange(e.target.value as SearchMode)} aria-label="Search mode" title={title}
      style={note ? { borderColor: '#d29922' } : undefined}>
      <option value="auto">Auto</option>
      <option value="fts">Words</option>
      <option value="semantic">Meaning</option>
      <option value="hybrid">Words + meaning</option>
    </select>
  );
};

const q = (base: string) => `base=${encodeURIComponent(base)}`;

export const SemanticCard: React.FC<{ base: string; status: ReturnType<typeof useSemanticStatus> }> = ({ base, status }) => {
  const { st, err, refresh } = status;
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showLog, setShowLog] = useState(false);

  const call = async (path: string, method: string) => {
    setMsg(null);
    try {
      await api(path, { method });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    refresh();
  };

  const job = st?.job;
  const running = job?.status === 'running';
  const pct = st && st.notes ? Math.round((st.embedded / st.notes) * 100) : 0;
  const info = st?.model_info;

  return (
    <section className="settings-card">
      <h3>Semantic search</h3>
      <p className="muted small">Finds notes by meaning, not only by their words (“my car” finds a note about an <i>automobile</i>). Runs on this computer; the model is downloaded once{info?.size_mb ? ` (${info.size_mb} MB)` : ''} into <code>~/.myclaw/models</code>.</p>
      {err && <div className="notice error">{err}</div>}
      {msg && <div className={`notice ${msg.ok ? '' : 'error'}`}>{msg.text}</div>}
      {st && (
        <>
          <div className="small" style={{ marginBottom: 6 }}>
            <b>{st.enabled ? 'On' : 'Off'}</b>
            {!st.enabled && <span className="muted"> — turn on in Settings → Features (“Semantic search”). Notes can be embedded before that.</span>}
          </div>
          <div className="muted small" style={{ marginBottom: 8 }} title={info?.known === false ? 'A model not in the list: check its size and licence on its model card.' : undefined}>
            Model <code>{st.model}</code>{info?.known !== false && info ? ` · ${info.licence} · ${info.languages}` : ''}
          </div>

          {!st.installed ? (
            <>
              <p className="small">Needs the optional package <code>fastembed</code> (ONNX Runtime, CPU only).</p>
              {st.in_venv ? (
                <button type="button" className="primary-btn" disabled={!!st.install?.running} onClick={() => call('/api/knowledge/semantic/install', 'POST')}>
                  {st.install?.running ? 'Installing…' : 'Install'}
                </button>
              ) : (
                <p className="muted small">Install it in ZenSynora’s Python environment: <code>{st.install_command}</code></p>
              )}
              {st.install?.ok === false && st.install.error && <div className="notice error" style={{ marginTop: 8 }}>{st.install.error}</div>}
            </>
          ) : (
            <>
              <div className="kb-bar" style={{ gridTemplateColumns: '1fr 2fr auto' }}>
                <span>Embedded</span>
                <span className="kb-bar-track"><span className="kb-bar-fill" style={{ width: `${pct}%` }} /></span>
                <b>{st.embedded}/{st.notes}</b>
              </div>
              {st.waiting > 0 && !running && <p className="muted small">{st.waiting_text || `${st.waiting} note(s) waiting to be embedded`}{st.queued !== 0 ? ' (being embedded now…)' : '.'}</p>}
              {running && (
                <p className="small">{job?.phase === 'loading the model' || !job?.total
                  ? `Loading the model… (the first time it is downloaded${info?.size_mb ? `, ${info.size_mb} MB` : ''})`
                  : `Embedding ${job.done}/${job.total} notes…`}</p>
              )}
              {job && job.status !== 'running' && (job.error || job.result?.message) && (
                <div className={`notice ${job.status === 'done' ? '' : 'error'}`} style={{ marginTop: 6 }}>{job.error || job.result?.message}</div>
              )}
              {!running && st.last_error && !job?.error && <div className="notice error" style={{ marginTop: 6 }}>{st.last_error}</div>}
              <div className="kb-buttons" style={{ marginTop: 8 }}>
                {running ? (
                  <button type="button" className="ghost-btn" onClick={() => call(`/api/knowledge/semantic/backfill?${q(base)}`, 'DELETE')}>Stop</button>
                ) : (
                  <>
                    <button type="button" className="primary-btn" disabled={st.stale === 0} onClick={() => call(`/api/knowledge/semantic/backfill?${q(base)}`, 'POST')}
                      title="Compute vectors for the notes that have none (new notes are embedded automatically while semantic search is on)">Backfill</button>
                    <button type="button" className="ghost-btn" onClick={() => call(`/api/knowledge/semantic/backfill?${q(base)}&all=true`, 'POST')}
                      title="Check every note; parts that did not change are not embedded again">Re-check all</button>
                  </>
                )}
              </div>
            </>
          )}
          {st.install?.log && (
            <div style={{ marginTop: 8 }}>
              <button type="button" className="chip small-chip" onClick={() => setShowLog(s => !s)}>{showLog ? 'Hide' : 'Show'} install log</button>
              {showLog && <pre className="kb-body" style={{ maxHeight: 200, overflow: 'auto', fontSize: '0.75rem' }}>{st.install.log}</pre>}
            </div>
          )}
        </>
      )}
    </section>
  );
};
