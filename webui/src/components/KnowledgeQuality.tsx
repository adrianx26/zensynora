// Knowledge base quality tools (KB phase 2, 2026-10-24): contradicting facts
// ("conflicts") on a note, the fact-category registry with merge suggestions,
// and duplicate notes. Every change to note files is previewed first; the
// server keeps the previous versions in the base's .trash folder.
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

export interface Conflict {
  id: number;
  permalink: string;
  category: string;
  a: string;
  b: string;
  kind: string;
  status: string;
  title?: string | null;
}

const q = (base: string) => `base=${encodeURIComponent(base)}`;

// ── Conflicts of one note (side panel) ───────────────────────────────────────

export const ConflictsSection: React.FC<{
  base: string;
  conflicts: Conflict[];
  onDone: (message: string, ok: boolean) => void;
}> = ({ base, conflicts, onDone }) => {
  const [busy, setBusy] = useState<number | null>(null);
  if (!conflicts.length) return null;
  const resolve = async (c: Conflict, keep: 'a' | 'b' | 'both') => {
    if (keep !== 'both' && !window.confirm(`Remove “${keep === 'a' ? c.b : c.a}” from the note? The previous version is kept in the trash folder.`)) return;
    setBusy(c.id);
    try {
      const r = await api<{ message: string }>(`/api/knowledge/conflicts/${c.id}?${q(base)}`, {
        method: 'POST', body: JSON.stringify({ keep }),
      });
      onDone(r.message, true);
    } catch (e) {
      onDone((e as Error).message, false);
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="kb-conflicts">
      <h4 style={{ color: 'var(--warning-color, #d29922)' }}>⚠ Facts that contradict each other <span className="muted">{conflicts.length}</span></h4>
      {conflicts.map(c => (
        <div key={c.id} className="kb-link" style={{ cursor: 'default', borderColor: '#d29922' }}>
          <div className="kb-obs-cat">{c.category}</div>
          <div><b>A</b> {c.a}</div>
          <div><b>B</b> {c.b}</div>
          <div className="kb-buttons" style={{ marginTop: 6 }}>
            <button type="button" className="ghost-btn" disabled={busy !== null} onClick={() => resolve(c, 'a')} title="Keep A, remove B from the note">Keep A</button>
            <button type="button" className="ghost-btn" disabled={busy !== null} onClick={() => resolve(c, 'b')} title="Keep B, remove A from the note">Keep B</button>
            <button type="button" className="ghost-btn" disabled={busy !== null} onClick={() => resolve(c, 'both')} title="Both are right — do not flag this pair again">Both are fine</button>
          </div>
        </div>
      ))}
    </section>
  );
};

// ── Category registry (Tags & types tab) ─────────────────────────────────────

interface Category { name: string; count: number; description?: string | null; variants: Record<string, number> }
export interface Suggestion { from: string; to: string; from_count: number; to_count: number; reason: string }

export const CategoriesCard: React.FC<{
  base: string;
  reloadKey: unknown; // changes whenever the graph data was reloaded (save, re-index, enrich, merge)
  active: string[];
  onToggle: (name: string) => void;
  onMerge: (s: Suggestion) => void;
}> = ({ base, reloadKey, active, onToggle, onMerge }) => {
  const [data, setData] = useState<{ categories: Category[]; suggestions: Suggestion[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api<{ categories: Category[]; suggestions: Suggestion[] }>(`/api/knowledge/categories?${q(base)}`)
      .then(d => { if (live) { setData(d); setErr(null); } })
      .catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [base, reloadKey]);
  const max = Math.max(1, ...(data?.categories || []).map(c => c.count));
  return (
    <section className="settings-card">
      <h3>Fact categories</h3>
      <p className="muted small">Facts are lines like <code>- [category] text</code>. Spellings of the same category (<code>Specs</code>, <code>spec</code>) are counted together. Click to show only notes with that category.</p>
      {err && <div className="notice error">{err}</div>}
      {data?.categories.map(c => {
        const variants = Object.keys(c.variants || {}).filter(v => v !== c.name);
        return (
          <button type="button" className={`kb-bar ${active.includes(c.name) ? 'active' : ''}`} key={c.name} onClick={() => onToggle(c.name)}
            title={[c.description, variants.length ? `written as: ${variants.join(', ')}` : ''].filter(Boolean).join(' · ')}>
            <span>{c.name}{variants.length > 0 && <span className="muted small"> +{variants.length}</span>}</span>
            <span className="kb-bar-track"><span className="kb-bar-fill" style={{ width: `${(c.count / max) * 100}%` }} /></span><b>{c.count}</b>
          </button>
        );
      })}
      {data && data.categories.length === 0 && <span className="muted">No facts yet.</span>}
      {data && data.suggestions.length > 0 && (
        <>
          <h4 style={{ marginTop: 12 }}>Merge suggestions</h4>
          {data.suggestions.map(s => (
            <div key={`${s.from}→${s.to}`} className="kb-bar" style={{ gridTemplateColumns: '1fr auto' }}>
              <span title={s.reason}><code>{s.from}</code> <span className="muted">({s.from_count})</span> → <code>{s.to}</code> <span className="muted">({s.to_count}) · {s.reason}</span></span>
              <button type="button" className="ghost-btn" onClick={() => onMerge(s)}>Preview…</button>
            </div>
          ))}
        </>
      )}
    </section>
  );
};

interface CategoryPreview { from: string; to: string; dry_run: boolean; facts: number; notes: { permalink: string; title: string; changes: { before: string; after: string }[] }[] }

export const CategoryMergePanel: React.FC<{
  base: string;
  from: string;
  to: string;
  onDone: (message: string, ok: boolean) => void;
  onCancel: () => void;
}> = ({ base, from, to, onDone, onCancel }) => {
  const [target, setTarget] = useState(to);
  const [preview, setPreview] = useState<CategoryPreview | null>(null);
  const [previewed, setPreviewed] = useState<string | null>(null); // the target the preview is for
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (dry: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<CategoryPreview>(`/api/knowledge/categories/merge?${q(base)}`, {
        method: 'POST', body: JSON.stringify({ from, to: target, dry_run: dry }),
      });
      if (dry) { setPreview(r); setPreviewed(target); }
      else onDone(`Renamed ${r.facts} fact(s) in ${r.notes.length} note(s) from “${from}” to “${r.to}”. Previous versions are in the trash folder.`, true);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [base, from, target, onDone]);
  useEffect(() => { setTarget(to); }, [to]);
  useEffect(() => { run(true); }, [from]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="kb-detail">
      <h3>Merge category</h3>
      <p className="muted small">Facts written as <code>[{from}]</code> are rewritten in their notes. Nothing changes until you click Apply.</p>
      <div className="field"><label className="field-label">Into</label>
        <input className="field-input" value={target} onChange={e => setTarget(e.target.value)} onBlur={() => run(true)} /></div>
      {previewed !== null && previewed !== target && <p className="muted small">Click outside the field to preview this name before applying.</p>}
      {err && <div className="notice error">{err}</div>}
      {preview && (
        <section>
          <h4>{preview.facts} fact(s) in {preview.notes.length} note(s)</h4>
          {preview.notes.map(n => (
            <div key={n.permalink} className="kb-obs-group">
              <div className="kb-obs-cat">{n.title}</div>
              <ul>{n.changes.map((c, i) => <li key={i}><span className="muted">{c.before}</span> → {c.after}</li>)}</ul>
            </div>
          ))}
        </section>
      )}
      <div className="kb-buttons">
        <button type="button" className="primary-btn" disabled={busy || !preview || preview.facts === 0 || !target.trim() || previewed !== target} onClick={() => run(false)}>{busy ? 'Working…' : 'Apply'}</button>
        <button type="button" className="ghost-btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
};

// ── Duplicate notes ──────────────────────────────────────────────────────────

interface NoteRef { permalink: string; title: string; facts: number; links: number; created: string | null }
interface Pair { keep: NoteRef; drop: NoteRef; reasons: string[] }
interface MergePreview {
  keep: string; drop: string; title: string; dry_run: boolean; tags: string[]; aliases: string[];
  facts: string[]; links: string[]; body: string;
  added: { tags: string[]; aliases: string[]; facts: number; links: number; text: boolean };
}

export const DuplicatesPanel: React.FC<{
  base: string;
  onDone: (message: string, ok: boolean) => void;
  onOpen: (permalink: string) => void;
  onCancel: () => void;
}> = ({ base, onDone, onOpen, onCancel }) => {
  const [pairs, setPairs] = useState<Pair[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<{ keep: string; drop: string } | null>(null);
  const [preview, setPreview] = useState<MergePreview | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      setPairs((await api<{ pairs: Pair[] }>(`/api/knowledge/duplicates?${q(base)}`)).pairs);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const showPreview = async (keep: string, drop: string) => {
    setActive({ keep, drop });
    setPreview(null);
    setErr(null);
    try {
      setPreview(await api<MergePreview>(`/api/knowledge/merge?${q(base)}`, { method: 'POST', body: JSON.stringify({ keep, drop, dry_run: true }) }));
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const merge = async () => {
    if (!active) return;
    setBusy(true);
    try {
      await api<MergePreview>(`/api/knowledge/merge?${q(base)}`, { method: 'POST', body: JSON.stringify({ ...active, dry_run: false }) });
      onDone(`Merged “${active.drop}” into “${active.keep}”. The merged file is in the trash folder; links to it now open “${active.keep}”.`, true);
      setActive(null);
      setPreview(null);
      load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (active) {
    return (
      <div className="kb-detail">
        <h3>Merge preview</h3>
        <p className="muted small">“{active.drop}” is merged into “{active.keep}”. Nothing changes until you click Merge.</p>
        <button type="button" className="ghost-btn" onClick={() => showPreview(active.drop, active.keep)}>⇄ Keep the other one</button>
        {err && <div className="notice error">{err}</div>}
        {!preview && !err && <div className="muted small">Loading…</div>}
        {preview && (
          <>
            <section>
              <h4>Result: {preview.title}</h4>
              <div className="kb-tags">{preview.tags.map(t => <span key={t} className="kb-tag">#{t}</span>)}</div>
              {preview.aliases.length > 0 && <div className="muted small">Also known as: {preview.aliases.join(', ')}</div>}
              <div className="muted small">
                Adds {preview.added.facts} fact(s), {preview.added.links} link(s){preview.added.tags.length ? `, tags ${preview.added.tags.map(t => '#' + t).join(' ')}` : ''}{preview.added.text ? ', and the other note’s text' : ''}.
              </div>
            </section>
            {preview.body && <div className="kb-body" style={{ maxHeight: 220, overflow: 'auto' }}>{preview.body}</div>}
            {preview.facts.length > 0 && <section><h4>Facts <span className="muted">{preview.facts.length}</span></h4><div className="kb-obs-group"><ul>{preview.facts.map((f, i) => <li key={i}>{f}</li>)}</ul></div></section>}
            {preview.links.length > 0 && <section><h4>Links <span className="muted">{preview.links.length}</span></h4><div className="kb-obs-group"><ul>{preview.links.map((l, i) => <li key={i}>{l}</li>)}</ul></div></section>}
          </>
        )}
        <div className="kb-buttons">
          <button type="button" className="primary-btn" disabled={busy || !preview} onClick={merge}>{busy ? 'Merging…' : 'Merge'}</button>
          <button type="button" className="ghost-btn" onClick={() => { setActive(null); setPreview(null); setErr(null); }}>Back</button>
        </div>
      </div>
    );
  }

  return (
    <div className="kb-detail">
      <h3>Possible duplicates</h3>
      <p className="muted small">Same title, a title that is the other note’s alias, or (nearly) the same text. Pick a pair to see the merged note first.</p>
      {err && <div className="notice error">{err}</div>}
      {!pairs && !err && <div className="muted small">Looking…</div>}
      {pairs && pairs.length === 0 && <div className="muted">No duplicates found.</div>}
      {pairs?.map(p => (
        <div key={`${p.keep.permalink}|${p.drop.permalink}`} className="kb-link" style={{ cursor: 'default' }}>
          <div>
            <button type="button" className="kb-sort" onClick={() => onOpen(p.keep.permalink)}>{p.keep.title}</button>
            <span className="muted small"> ({p.keep.links} links, {p.keep.facts} facts)</span>
          </div>
          <div>
            ← <button type="button" className="kb-sort" onClick={() => onOpen(p.drop.permalink)}>{p.drop.title}</button>
            <span className="muted small"> ({p.drop.links} links, {p.drop.facts} facts)</span>
          </div>
          <div className="muted small">{p.reasons.join(' · ')}</div>
          <div className="kb-buttons" style={{ marginTop: 4 }}>
            <button type="button" className="ghost-btn" onClick={() => showPreview(p.keep.permalink, p.drop.permalink)}>Preview merge…</button>
          </div>
        </div>
      ))}
      <div className="kb-buttons"><button type="button" className="ghost-btn" onClick={onCancel}>Close</button></div>
    </div>
  );
};
