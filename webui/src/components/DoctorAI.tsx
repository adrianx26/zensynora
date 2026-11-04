// AI in the Doctor (2026-11-04): a health summary, "Explain" (plain-language cause and
// steps, kept so it shows again without a new model call) and "Investigate" (an agent
// job that checks the problem live and may file a change proposal). The model only
// explains; nothing is changed.
import React, { useEffect, useState } from 'react';
import { api } from '../api';
import type { Explanation } from '../doctorAi';

export const AISummaryCard: React.FC<{ hours: number }> = ({ hours }) => {
  const [res, setRes] = useState<{ text: string; at: string; hours: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      setRes(await api('/api/doctor/ai/summary', { method: 'POST', body: JSON.stringify({ hours }) }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="settings-card doctor-ai">
      <div className="probe-row">
        <h3 style={{ margin: 0 }}>🤖 AI health summary</h3>
        <button className="primary-btn" onClick={run} disabled={busy}>{busy ? 'The model is reading the report…' : res ? '↻ Write it again' : 'Write the summary'}</button>
      </div>
      <p className="muted small">The default model reads the checks, errors, restarts, failed jobs and model health of the period and says what is wrong and what to fix first. It changes nothing.</p>
      {err && <div className="notice error">{err}</div>}
      {res && <><div className="doctor-ai-text">{res.text}</div><div className="muted small">Written {res.at} · last {res.hours} h</div></>}
    </div>
  );
};

export const AskAI: React.FC<{
  kind: 'error' | 'check'; id: string; level?: string; hours?: number;
  known?: Explanation; onExplained?: () => void;
}> = ({ kind, id, level = '', hours = 24, known, onExplained }) => {
  const [text, setText] = useState<Explanation | null>(known || null);
  const [busy, setBusy] = useState<'' | 'explain' | 'investigate'>('');
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  // show a cached explanation that arrived later
  useEffect(() => { if (known && !text) setText(known); }, [known, text]);
  const body = (refresh = false) => JSON.stringify({ kind, key: id, level, hours, refresh });
  const explain = async (refresh = false) => {
    setBusy('explain'); setNote(null);
    try {
      setText(await api<Explanation>('/api/doctor/ai/explain', { method: 'POST', body: body(refresh) }));
      onExplained?.();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy('');
    }
  };
  const investigate = async () => {
    setBusy('investigate'); setNote(null);
    try {
      const r = await api<{ name: string }>('/api/doctor/ai/investigate', { method: 'POST', body: body() });
      setNote({ ok: true, text: `Started “${r.name}”. Its report is on the Jobs page; a code fix, if any, appears under Change proposals.` });
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy('');
    }
  };
  return (
    <div className="doctor-ask">
      <div className="probe-row">
        {!text && <button className="ghost-btn small-btn" disabled={!!busy} onClick={() => explain()}
          title="The default model explains what this means and what to do">{busy === 'explain' ? 'Explaining…' : '🤖 Explain'}</button>}
        <button className="ghost-btn small-btn" disabled={!!busy} onClick={investigate}
          title="An agent checks this problem live; if the code is at fault it files a change proposal for you to approve">{busy === 'investigate' ? 'Starting…' : '🔎 Investigate with an agent'}</button>
      </div>
      {note && <div className={`notice ${note.ok ? '' : 'error'}`}>{note.text}</div>}
      {text && (
        <div className="doctor-ai-text">
          {text.text}
          <div className="muted small">
            {text.how === 'automatic' ? 'Explained automatically' : 'Explained'} {text.at} ·{' '}
            <button className="link-btn" disabled={!!busy} onClick={() => explain(true)}>{busy === 'explain' ? 'asking…' : 'ask again'}</button>
          </div>
        </div>
      )}
    </div>
  );
};
