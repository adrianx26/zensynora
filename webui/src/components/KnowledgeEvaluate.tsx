// Knowledge → ✔ Evaluate (2026-11-03): check notes, links, tags and embeddings and
// mark them valid / invalid. Manual batches of 1 / 10 / 100 (rules, or rules + the
// default model), or automatically at idle time (Settings → Features). Fixes are
// proposals: nothing changes until you tick them and click Apply (previous versions
// go to .trash).
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { SegBar } from './viz';

interface Issue { kind: 'note' | 'link' | 'tag' | 'embedding'; item: string; reason: string; fix: string; value: string; by: 'rules' | 'ai' }
interface InvalidNote { permalink: string; title: string; mode: string; checked_at: number; issues: Issue[] }
interface Run { running: boolean; done: number; count: number; mode: string | null; trigger?: string;
  last: { evaluated: number; valid?: number; invalid?: number; stopped?: string; mode?: string; trigger?: string; skipped_unchanged?: number } | null }
interface Status {
  counts: { notes: number; valid: number; invalid: number; unchecked: number; changed: number };
  by_kind: Record<string, number>; invalid: InvalidNote[]; run: Run;
  options: { idle: boolean; idle_minutes: number; mode: string; batch: number };
}

const FIX_TEXT: Record<string, (i: Issue) => string> = {
  trash_note: () => 'Move the note to the trash',
  remove_link: i => `Remove the link to “${i.item}”`,
  remove_tag: i => `Remove #${i.item}`,
  rename_tag: i => `Rename #${i.item} to #${i.value}`,
  reembed: () => 'Prepare it for semantic search again',
  none: () => 'Only flagged (fix it by hand)',
};
const KIND_TEXT: Record<string, string> = { note: 'Notes', link: 'Links', tag: 'Tags', embedding: 'Embeddings' };
const q = (base: string) => `base=${encodeURIComponent(base)}`;
const fixKey = (p: string, i: Issue) => `${p}|${i.kind}|${i.item}|${i.fix}`;

const KnowledgeEvaluate: React.FC<{
  base: string; onDone: (message: string, ok: boolean) => void; onOpen: (permalink: string) => void;
}> = ({ base, onDone, onOpen }) => {
  const [st, setSt] = useState<Status | null>(null);
  const [mode, setMode] = useState<'rules' | 'ai'>('rules');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api<Status>(`/api/knowledge/evaluate/status?${q(base)}`));
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [base]);

  useEffect(() => { load(); }, [load]);
  // while a batch runs: refresh every 2 s
  useEffect(() => {
    if (!st?.run.running) return;
    const t = window.setInterval(load, 2000);
    return () => window.clearInterval(t);
  }, [st?.run.running, load]);

  const run = async (count: 1 | 10 | 100) => {
    setErr(null);
    try {
      await api(`/api/knowledge/evaluate?${q(base)}`, { method: 'POST', body: JSON.stringify({ count, mode }) });
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const fixable = (st?.invalid || []).flatMap(n => n.issues.filter(i => i.fix !== 'none').map(i => ({ ...i, permalink: n.permalink })));
  const apply = async () => {
    const fixes = fixable.filter(f => picked.has(fixKey(f.permalink, f)));
    setBusy(true); setErr(null);
    try {
      const r = await api<{ applied: unknown[]; failed: { message: string }[] }>(`/api/knowledge/evaluate/apply?${q(base)}`,
        { method: 'POST', body: JSON.stringify({ fixes }) });
      setPicked(new Set());
      onDone(`${r.applied.length} fix(es) applied${r.failed.length ? `, ${r.failed.length} failed: ${r.failed.map(f => f.message).join('; ')}` : ''}. Previous versions are in the trash folder.`,
        r.failed.length === 0);
      load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (k: string) => setPicked(s => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  const c = st?.counts;
  const r = st?.run;
  return (
    <div className="kb-detail ai-resolve viz">
      <h3>✔ Evaluate</h3>
      <p className="muted small">
        Checks notes, links, tags and embeddings and marks each note <b>valid</b> or <b>invalid</b>, with the reason and a proposed fix.
        Notes never checked come first, then notes changed since. Nothing changes until you tick fixes and click <b>Apply</b>.
      </p>
      {err && <div className="notice error">{err}</div>}
      {c && (
        <>
          <SegBar parts={[
            { label: 'Valid', value: c.valid, color: 'var(--viz-good)' },
            { label: 'Invalid', value: c.invalid, color: 'var(--viz-critical)' },
            { label: 'Not checked', value: c.unchecked, color: 'var(--viz-rest)' },
          ]} />
          {c.changed > 0 && <div className="muted small">{c.changed} checked note(s) changed since their check.</div>}
        </>
      )}

      <div className="kb-buttons" style={{ marginTop: 8 }}>
        <span className="viz-toggle">
          <button type="button" className={`chip ${mode === 'rules' ? 'active' : ''}`} onClick={() => setMode('rules')} title="Fast, no model calls">Rules</button>
          <button type="button" className={`chip ${mode === 'ai' ? 'active' : ''}`} onClick={() => setMode('ai')} title="Rules + the default model reads each note (one call per note)">AI</button>
        </span>
        {([1, 10, 100] as const).map(n => (
          <button key={n} type="button" className="ghost-btn" disabled={r?.running} onClick={() => run(n)}>Check {n}</button>
        ))}
      </div>
      {r?.running && <div className="muted small">Checking… {r.done} of {r.count} ({r.mode}{r.trigger === 'idle' ? ', idle time' : ''})</div>}
      {!r?.running && r?.last && (
        <div className="muted small">
          Last {r.last.trigger === 'idle' ? 'idle-time ' : ''}check ({r.last.mode}): {r.last.evaluated} note(s){r.last.invalid !== undefined ? `, ${r.last.invalid} invalid` : ''}
          {r.last.evaluated === 0 && !r.last.stopped ? ' — every note is up to date.' : '.'}
          {r.last.stopped && <> Stopped: {r.last.stopped}</>}
        </div>
      )}
      {st && (
        <div className="muted small">
          At idle time: <b>{st.options.idle ? `on — ${st.options.batch} notes (${st.options.mode}) after ${st.options.idle_minutes} min without agent work` : 'off'}</b> (Settings → Features).
        </div>
      )}

      {st && st.invalid.length > 0 && (
        <>
          <h4 style={{ marginTop: 12 }}>Invalid <span className="muted small">{Object.entries(st.by_kind).map(([k, v]) => `${KIND_TEXT[k] || k} ${v}`).join(' · ')}</span></h4>
          <div className="kb-buttons">
            <button type="button" className="ghost-btn small-btn" onClick={() => setPicked(new Set(fixable.filter(f => f.by === 'rules').map(f => fixKey(f.permalink, f))))}>Select rule fixes</button>
            <button type="button" className="ghost-btn small-btn" onClick={() => setPicked(new Set(fixable.map(f => fixKey(f.permalink, f))))}>Select all</button>
            <button type="button" className="ghost-btn small-btn" onClick={() => setPicked(new Set())}>None</button>
          </div>
          {st.invalid.map(n => (
            <div key={n.permalink} className="ai-item">
              <div className="ai-item-head">
                <button type="button" className="kb-sort" onClick={() => onOpen(n.permalink)}>{n.title}</button>
                <span className="muted small">checked {new Date(n.checked_at * 1000).toLocaleString()} · {n.mode}</span>
              </div>
              {n.issues.map(i => {
                const k = fixKey(n.permalink, i);
                return (
                  <label key={k} className="small ai-fix">
                    <input type="checkbox" disabled={i.fix === 'none'} checked={picked.has(k)} onChange={() => toggle(k)} />
                    <span className="badge">{i.kind}{i.by === 'ai' ? ' · AI' : ''}</span> {i.reason} <span className="muted">→ {(FIX_TEXT[i.fix] || (() => i.fix))(i)}</span>
                  </label>
                );
              })}
            </div>
          ))}
          <div className="kb-buttons">
            <button type="button" className="primary-btn" disabled={busy || picked.size === 0} onClick={apply}>{busy ? 'Applying…' : `Apply ${picked.size} fix(es)`}</button>
          </div>
        </>
      )}
    </div>
  );
};

export default KnowledgeEvaluate;
