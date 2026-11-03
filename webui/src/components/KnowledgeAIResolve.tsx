// Knowledge → 🤖 AI resolve (2026-11-03): the default model proposes how to settle
// open conflicts and duplicate notes; nothing changes until you approve. Applying
// uses the same actions as the manual buttons (previous versions in .trash).
import React, { useState } from 'react';
import { api } from '../api';

interface NoteRef { permalink: string; title: string; links?: number; facts?: number }
interface Proposal {
  kind: 'conflict' | 'duplicate';
  id?: number; permalink?: string; title?: string; category?: string; a?: string; b?: string;
  keep?: NoteRef; drop?: NoteRef; reasons?: string[];
  action: string | null; reason: string; confidence: number; error: string | null;
}
interface ProposeResult { proposals: Proposal[]; stopped: string; remaining: { conflicts: number; duplicates: number } }
interface ApplyResult { applied: Proposal[]; failed: (Proposal & { message: string })[] }

const CHOICES: Record<Proposal['kind'], { value: string; label: (p: Proposal) => string }[]> = {
  conflict: [
    { value: 'a', label: p => `Keep “${p.a}”` },
    { value: 'b', label: p => `Keep “${p.b}”` },
    { value: 'both', label: () => 'Both are true (stop flagging)' },
  ],
  duplicate: [
    { value: 'merge', label: p => `Merge into “${p.keep?.title}”` },
    { value: 'swap', label: p => `Merge into “${p.drop?.title}”` },
    { value: 'keep_both', label: () => 'Different notes (stop flagging)' },
  ],
};

const key = (p: Proposal) => (p.kind === 'conflict' ? `c${p.id}` : `d${p.keep?.permalink}|${p.drop?.permalink}`);
const q = (base: string) => `base=${encodeURIComponent(base)}`;

const KnowledgeAIResolve: React.FC<{
  base: string; onDone: (message: string, ok: boolean) => void; onOpen: (permalink: string) => void;
}> = ({ base, onDone, onOpen }) => {
  const [kinds, setKinds] = useState({ conflicts: true, duplicates: true });
  const [res, setRes] = useState<ProposeResult | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<'' | 'ask' | 'apply'>('');
  const [err, setErr] = useState<string | null>(null);

  const ask = async () => {
    setBusy('ask'); setErr(null);
    try {
      const r = await api<ProposeResult>(`/api/knowledge/ai-resolve/propose?${q(base)}`, {
        method: 'POST',
        body: JSON.stringify({ limit: 10, kinds: Object.entries(kinds).filter(([, v]) => v).map(([k]) => k) }),
      });
      setRes(r);
      setChoice(Object.fromEntries(r.proposals.filter(p => p.action).map(p => [key(p), p.action as string])));
      setPicked(new Set());
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const applyPicked = async () => {
    if (!res) return;
    const items = res.proposals.filter(p => picked.has(key(p)) && choice[key(p)]).map(p => ({ ...p, action: choice[key(p)] }));
    setBusy('apply'); setErr(null);
    try {
      const r = await api<ApplyResult>(`/api/knowledge/ai-resolve/apply?${q(base)}`, { method: 'POST', body: JSON.stringify({ proposals: items }) });
      const done = new Set(r.applied.map(key));
      setRes({ ...res, proposals: res.proposals.filter(p => !done.has(key(p))) });
      setPicked(new Set());
      onDone(`${r.applied.length} applied${r.failed.length ? `, ${r.failed.length} failed: ${r.failed.map(f => f.message).join('; ')}` : ''}. Previous versions are in the trash folder.`,
        r.failed.length === 0);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const toggle = (k: string) => setPicked(s => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const selectable = (res?.proposals || []).filter(p => choice[key(p)]);
  const left = res ? res.remaining.conflicts + res.remaining.duplicates : 0;

  return (
    <div className="kb-detail ai-resolve">
      <h3>🤖 AI resolve</h3>
      <p className="muted small">
        The default model looks at up to 10 open conflicts and possible duplicates and proposes what to do, with a reason.
        Nothing changes until you tick items and click <b>Apply</b>. Previous versions go to the trash folder.
      </p>
      <div className="kb-buttons">
        <label className="small"><input type="checkbox" checked={kinds.conflicts} onChange={e => setKinds({ ...kinds, conflicts: e.target.checked })} /> Conflicts</label>
        <label className="small"><input type="checkbox" checked={kinds.duplicates} onChange={e => setKinds({ ...kinds, duplicates: e.target.checked })} /> Duplicates</label>
        <button type="button" className="primary-btn" disabled={!!busy || (!kinds.conflicts && !kinds.duplicates)} onClick={ask}>
          {busy === 'ask' ? 'Asking the model…' : res ? 'Ask again (10)' : 'Ask the AI (10)'}
        </button>
      </div>
      {err && <div className="notice error">{err}</div>}
      {res?.stopped && <div className="notice error">The model stopped: {res.stopped}</div>}
      {res && res.proposals.length === 0 && !res.stopped && <div className="muted">Nothing to resolve{left ? '' : ' — no open conflicts or duplicates'}.</div>}

      {res && res.proposals.length > 0 && (
        <>
          <div className="kb-buttons">
            <button type="button" className="ghost-btn small-btn" onClick={() => setPicked(new Set(selectable.map(key)))}>Select all</button>
            <button type="button" className="ghost-btn small-btn" onClick={() => setPicked(new Set(selectable.filter(p => p.confidence >= 0.8).map(key)))}>Select ≥ 80 % sure</button>
            <button type="button" className="ghost-btn small-btn" onClick={() => setPicked(new Set())}>None</button>
          </div>
          {res.proposals.map(p => {
            const k = key(p);
            return (
              <div key={k} className={`ai-item ${picked.has(k) ? 'picked' : ''}`}>
                <label className="ai-item-head">
                  <input type="checkbox" disabled={!choice[k]} checked={picked.has(k)} onChange={() => toggle(k)} />
                  <span className="badge">{p.kind === 'conflict' ? 'conflict' : 'duplicate'}</span>
                  {p.kind === 'conflict'
                    ? <button type="button" className="kb-sort" onClick={() => onOpen(p.permalink || '')}>{p.title}</button>
                    : <span><button type="button" className="kb-sort" onClick={() => onOpen(p.keep?.permalink || '')}>{p.keep?.title}</button>
                      {' ≈ '}<button type="button" className="kb-sort" onClick={() => onOpen(p.drop?.permalink || '')}>{p.drop?.title}</button></span>}
                </label>
                {p.kind === 'conflict' && <div className="muted small">[{p.category}] a: “{p.a}” · b: “{p.b}”</div>}
                {p.kind === 'duplicate' && <div className="muted small">{(p.reasons || []).join(', ')}</div>}
                {p.error ? <div className="muted small">⚠ {p.error}</div> : (
                  <div className="small">AI: {p.reason} <span className="muted">({Math.round(p.confidence * 100)} % sure)</span></div>
                )}
                <select className="field-input small" value={choice[k] || ''} onChange={e => { setChoice({ ...choice, [k]: e.target.value }); }}>
                  {!choice[k] && <option value="">Choose…</option>}
                  {CHOICES[p.kind].map(c => <option key={c.value} value={c.value}>{c.label(p)}{c.value === p.action ? '  ← AI' : ''}</option>)}
                </select>
              </div>
            );
          })}
          <div className="kb-buttons">
            <button type="button" className="primary-btn" disabled={!!busy || picked.size === 0} onClick={applyPicked}>
              {busy === 'apply' ? 'Applying…' : `Apply ${picked.size} selected`}
            </button>
            {left > 0 && <span className="muted small">{left} more waiting</span>}
          </div>
        </>
      )}
    </div>
  );
};

export default KnowledgeAIResolve;
