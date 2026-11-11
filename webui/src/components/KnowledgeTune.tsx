// Knowledge → Tune (2026-11-11): similarity thresholds, word / meaning weights, chunking,
// the embedding model, temporal decay, a search test bench with 👍 / 👎, and who may
// change these values (you; the AI and agents only inside safe limits, or as proposals).
// Data: /api/knowledge/tuning/*. Relations live in KnowledgeRelations.tsx.
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { useAutoClear } from '../notice';
import KnowledgeRelations from './KnowledgeRelations';

interface Param {
  key: string; label: string; help: string; group: 'search' | 'cache' | 'embedding' | 'decay'; kind: 'float' | 'int';
  min: number; max: number; safe_min: number; safe_max: number; default: number; zero: string; reembed: boolean;
  value: number; effective: number;
}
interface Proposal { id: number; ts: number; actor: string; key: string; value: unknown; current: unknown; reason: string; safe: boolean }
interface Change { id: number; ts: number; actor: string; reason: string; changes: Record<string, [unknown, unknown]>; why?: Record<string, string> }
interface Model { name: string; dim: number | null; size_mb: number; licence: string; languages: string; min_score: number | null; offered: boolean }
interface Tuning {
  mode: 'suggest' | 'safe'; decay_ai: boolean; params: Param[]; decay_categories: Record<string, number>;
  embedding_model: string; proposals: Proposal[]; history: Change[]; models: Model[];
  decay_reasons: { ts: number; summary: string; why: Record<string, string> }[];
  semantic: { enabled: boolean; installed: boolean }; feedback: { queries: number; labelled: number; needed: number };
}
interface SetResult { applied: Record<string, [unknown, unknown]>; proposed: Proposal[]; errors: Record<string, string> }
interface AiResult extends SetResult { summary: string; why: Record<string, string>; mode: string }
interface Digest {
  notes: number; usage_log: boolean; enabled: boolean;
  ages: { age: string; notes: number; used_last_90_days: number }[];
  tags: { name: string; count: number; median_age_days: number }[];
  fact_categories: { name: string; count: number; median_age_days: number }[];
}
interface Row {
  permalink: string; title: string; position: number; before: number; word_rank: number | null; meaning_rank: number | null;
  similarity: number | null; decay: number; age_days: number; half_life_days: number; vote: number; score: number;
}
interface TestResult { query: string; results: Row[]; info: { mode?: string; fallback?: string }; decay: boolean; min_score: number }
interface AutoTune {
  labelled: number; needed: number; message: string; changes: Record<string, number>;
  current?: Record<string, number>; best?: Record<string, number>;
}
type Section = 'search' | 'decay' | 'bench' | 'relations' | 'history';

const q = (base: string) => `base=${encodeURIComponent(base)}`;
const show = (v: unknown) => (v && typeof v === 'object' ? Object.entries(v as Record<string, number>).map(([k, d]) => `${k}: ${d === 0 ? 'never' : `${d} d`}`).join(', ') || 'none' : String(v));
const when = (ts: number) => new Date(ts * 1000).toLocaleString();
const outside = (p: Param, v: number) => !(p.zero && v === 0) && (v < p.safe_min || v > p.safe_max);

const KnowledgeTune: React.FC<{ base: string; onOpenNote: (permalink: string) => void; onChanged: () => void }> = ({ base, onOpenNote, onChanged }) => {
  const [t, setT] = useState<Tuning | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [cats, setCats] = useState<[string, string][]>([]);
  const [model, setModel] = useState('');
  const [section, setSection] = useState<Section>('search');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useAutoClear(msg, setMsg);
  const [busy, setBusy] = useState('');
  const [ai, setAi] = useState<AiResult | null>(null);
  const [digest, setDigest] = useState<Digest | null>(null);

  const fill = (d: Tuning) => {
    setT(d);
    setDraft(Object.fromEntries(d.params.map(p => [p.key, String(p.value)])));
    setCats(Object.entries(d.decay_categories).map(([k, v]) => [k, String(v)]));
    setModel(d.embedding_model);
  };
  const load = useCallback(async () => {
    try { fill(await api<Tuning>(`/api/knowledge/tuning?${q(base)}`)); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  }, [base]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (section !== 'decay') return;
    api<Digest>(`/api/knowledge/tuning/decay?${q(base)}`).then(setDigest).catch(() => setDigest(null));
  }, [section, base, t?.history.length]);

  const run = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key); setMsg(null);
    try { const text = await fn(); if (text) setMsg({ ok: true, text }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(''); }
  };

  if (!t) return <div className="kb-empty">{msg ? msg.text : 'Loading…'}</div>;

  const catObj = Object.fromEntries(cats.filter(([k]) => k.trim()).map(([k, v]) => [k.trim().toLowerCase(), Number(v)]));
  const changes: Record<string, unknown> = {};
  for (const p of t.params) if (draft[p.key] !== '' && Number(draft[p.key]) !== p.value) changes[p.key] = Number(draft[p.key]);
  if (JSON.stringify(catObj) !== JSON.stringify(t.decay_categories)) changes.decay_categories = catObj;
  if (model.trim() && model.trim() !== t.embedding_model) changes.embedding_model = model.trim();
  const dirty = Object.keys(changes).length > 0;
  const reembed = t.params.some(p => p.reembed && p.key in changes) || 'embedding_model' in changes;

  const put = (body: object, done: string) => run('save', async () => {
    const r = await api<SetResult & { tuning: Tuning }>('/api/knowledge/tuning', { method: 'PUT', body: JSON.stringify(body) });
    fill({ ...t, ...r.tuning }); onChanged();
    const errs = Object.values(r.errors);
    if (errs.length) throw new Error(errs.join(' · '));
    return done;
  });
  const save = () => {
    if (reembed && !window.confirm('This re-embeds every note (in the background; search uses words until a note is done). A new model is downloaded first. Continue?')) return;
    put({ changes }, reembed ? 'Saved. The notes are being re-embedded.' : 'Saved — search uses the new values now.');
  };
  const askAi = (scope: 'all' | 'decay' | 'search') => run(`ai-${scope}`, async () => {
    const r = await api<AiResult>(`/api/knowledge/tuning/ai?${q(base)}&scope=${scope}`, { method: 'POST' });
    setAi(r); await load(); onChanged();
    const a = Object.keys(r.applied).length, p = r.proposed.length;
    return a || p ? `The AI ${a ? `changed ${a} value(s)` : ''}${a && p ? ' and ' : ''}${p ? `proposed ${p} change(s) — see Proposals` : ''}.` : 'The AI suggests no change.';
  });
  const decide = (id: number, approve: boolean) => run(`p${id}`, async () => {
    const r = await api<SetResult>(`/api/knowledge/tuning/proposals/${id}`, { method: 'POST', body: JSON.stringify({ approve }) });
    const errs = Object.values(r.errors || {});
    if (errs.length) throw new Error(errs.join(' · '));
    await load(); onChanged();
    return approve ? 'Approved and applied.' : 'Rejected.';
  });
  const undo = (id?: number) => run('undo', async () => {
    await api('/api/knowledge/tuning/undo', { method: 'POST', body: JSON.stringify({ id }) });
    await load(); onChanged();
    return 'The earlier values are back.';
  });

  const field = (p: Param) => {
    const v = Number(draft[p.key]);
    const slider = p.max <= 10;
    return (
      <div className="field kt-field" key={p.key}>
        <label className="field-label" htmlFor={`kt-${p.key}`}>{p.label}
          {draft[p.key] !== '' && outside(p, v) && <span className="badge kt-warn" title="You can use it; the AI and agents cannot set it by themselves.">outside the safe range</span>}
          {p.reembed && <span className="badge">re-embeds notes</span>}
        </label>
        <div className="field-row">
          {slider && <input type="range" min={p.min} max={p.max} step={p.kind === 'int' ? 1 : 0.01} value={draft[p.key] === '' ? p.value : v}
            aria-label={p.label} onChange={e => setDraft({ ...draft, [p.key]: e.target.value })} />}
          <input id={`kt-${p.key}`} className="field-input narrow" type="number" min={p.min} max={p.max} step={p.kind === 'int' ? 1 : 0.01}
            value={draft[p.key]} onChange={e => setDraft({ ...draft, [p.key]: e.target.value })} />
          {Number(draft[p.key]) !== p.default && <button type="button" className="link-btn" onClick={() => setDraft({ ...draft, [p.key]: String(p.default) })}>default {p.default}</button>}
        </div>
        <span className="field-help">{p.help} Safe range {p.safe_min}–{p.safe_max}{p.zero ? `; 0 = ${p.zero}${p.value === 0 && p.effective ? ` (now ${p.effective})` : ''}` : ''}.</span>
      </div>
    );
  };
  const group = (g: Param['group']) => t.params.filter(p => p.group === g).map(field);
  const info = t.models.find(m => m.name === model.trim());
  const saveBar = (
    <div className="save-actions">
      {dirty && <button type="button" className="ghost-btn" onClick={() => fill(t)}>Discard changes</button>}
      <button type="button" className="primary-btn" disabled={!dirty || !!busy} onClick={save}>{busy === 'save' ? 'Saving…' : 'Save'}</button>
    </div>
  );

  return (
    <div className="kt">
      <div className="settings-card kt-head">
        <div className="card-head">
          <h3>Tune search</h3>
          <div className="row-actions">
            <button type="button" className="primary-btn" disabled={!!busy} onClick={() => askAi('all')}
              title="The default model looks at measurements of this knowledge base (no note text) and suggests values">
              {busy === 'ai-all' ? 'Thinking…' : '🤖 AI suggest'}</button>
            {t.history.length > 0 && <button type="button" className="ghost-btn" disabled={!!busy} onClick={() => undo()}>↶ Undo last change</button>}
          </div>
        </div>
        <div className="field field-inline">
          <label className="field-label" htmlFor="kt-mode">The AI and agents</label>
          <select id="kt-mode" className="field-input kt-mode" value={t.mode} disabled={!!busy}
            onChange={e => put({ tuning_ai: e.target.value }, e.target.value === 'safe' ? 'The AI and agents may now change values inside the safe ranges.' : 'The AI and agents now only propose.')}>
            <option value="suggest">only propose — I approve every change</option>
            <option value="safe">may change values inside the safe ranges (logged, undoable)</option>
          </select>
        </div>
        <p className="muted small">You can set any value. Outside a safe range — and for the embedding model — the AI and agents always need your approval.</p>
        {msg && <div className={`notice ${msg.ok ? '' : 'error'}`} role="status">{msg.text}</div>}
        {ai && (ai.summary || Object.keys(ai.why).length > 0) && (
          <div className="notice">
            <b>🤖 {ai.summary || 'Suggestion'}</b>
            <ul className="plain-list">{Object.entries(ai.why).map(([k, w]) => <li key={k}><code>{k}</code> — {w || 'no reason given'}
              <span className="badge">{k in ai.applied ? 'applied' : ai.proposed.some(p => p.key === k) ? 'proposed' : 'not used'}</span></li>)}</ul>
          </div>
        )}
        {t.proposals.length > 0 && (
          <div className="kt-proposals">
            <h4>Proposals waiting for you</h4>
            {t.proposals.map(p => (
              <div className="kt-proposal" key={p.id}>
                <div><code>{p.key}</code>: {show(p.current)} → <b>{show(p.value)}</b>
                  {!p.safe && <span className="badge kt-warn">outside the safe range</span>}
                  <div className="muted small">{p.actor} · {when(p.ts)}{p.reason ? ` — ${p.reason}` : ''}</div></div>
                <div className="row-actions">
                  <button type="button" className="primary-btn" disabled={!!busy} onClick={() => decide(p.id, true)}>Approve</button>
                  <button type="button" className="ghost-btn" disabled={!!busy} onClick={() => decide(p.id, false)}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="kb-tabs" role="tablist" aria-label="Tune sections">
          {([['search', 'Thresholds & model'], ['decay', 'Temporal decay'], ['bench', 'Relevance test'], ['relations', 'Relations'], ['history', `History${t.history.length ? ` (${t.history.length})` : ''}`]] as [Section, string][]).map(([s, label]) => (
            <button key={s} type="button" role="tab" aria-selected={section === s} className={`settings-tab ${section === s ? 'active' : ''}`} onClick={() => setSection(s)}>{label}</button>
          ))}
        </div>
      </div>

      {section === 'search' && (
        <div className="kt-grid">
          <section className="settings-card">
            <h3>Search</h3>
            {!t.semantic.enabled && <p className="muted small">Semantic search is off (Settings → Features), so only the word search is used; these values apply once it is on.</p>}
            {group('search')}
            <h4>Answer cache (semantic cache)</h4>
            {group('cache')}
            {saveBar}
          </section>
          <section className="settings-card">
            <h3>Embedding model</h3>
            <div className="field">
              <label className="field-label" htmlFor="kt-model">Model</label>
              <select id="kt-model" className="field-input" value={t.models.some(m => m.name === model) ? model : ''} onChange={e => e.target.value && setModel(e.target.value)}>
                {!t.models.some(m => m.name === model) && <option value="">other: {model}</option>}
                <optgroup label="Offered">
                  {t.models.filter(m => m.offered).map(m => <option key={m.name} value={m.name}>{m.name} — {m.size_mb >= 1000 ? `${(m.size_mb / 1000).toFixed(1)} GB` : `${m.size_mb} MB`}, {m.languages}</option>)}
                </optgroup>
                {t.models.some(m => !m.offered) && <optgroup label="Other models of fastembed">
                  {t.models.filter(m => !m.offered).map(m => <option key={m.name} value={m.name}>{m.name}{m.size_mb ? ` — ${m.size_mb} MB` : ''}</option>)}
                </optgroup>}
              </select>
              <input className="field-input" value={model} onChange={e => setModel(e.target.value)} aria-label="Model name" placeholder="or type a model name, e.g. BAAI/bge-base-en-v1.5" />
              <span className="field-help">
                {info ? `${info.dim ?? '?'} dimensions · ${info.size_mb} MB download · ${info.licence || 'licence: see the model card'} · ${info.languages}.` : 'A model not in the list: check its size and licence on its model card.'}
                {' '}A bigger model ranks better but is slower and needs more memory. Changing it re-embeds every note (Overview → Semantic search shows the progress); a similarity threshold of 0 follows the new model.
              </span>
            </div>
            <h4>Chunking</h4>
            {group('embedding')}
            {saveBar}
          </section>
        </div>
      )}

      {section === 'decay' && (
        <div className="kt-grid">
          <section className="settings-card">
            <div className="card-head">
              <h3>Temporal decay <span className={`badge ${digest?.enabled ? 'proposal-applied' : ''}`}>{Number(draft.decay_half_life_days) > 0 || cats.some(([, v]) => Number(v) > 0) ? 'on' : 'off'}</span></h3>
              <button type="button" className="primary-btn" disabled={!!busy} onClick={() => askAi('decay')}
                title="The default model decides whether decay helps this base, the half-life and which tags / categories never or quickly go stale — and says why">
                {busy === 'ai-decay' ? 'Thinking…' : '🤖 Let the AI decide'}</button>
            </div>
            <p className="muted small">A note that was not changed or used by an agent for a long time ranks lower in search and in the context agents get. Nothing is hidden, changed or deleted. Notes tagged <code>evergreen</code> never decay.</p>
            {t.decay_reasons[0] && (
              <div className="notice"><b>🤖 Why ({when(t.decay_reasons[0].ts)})</b> {t.decay_reasons[0].summary}
                <ul className="plain-list">{Object.entries(t.decay_reasons[0].why).map(([k, w]) => <li key={k}><code>{k}</code> — {w}</li>)}</ul></div>
            )}
            {group('decay')}
            <h4>Own half-life per tag or fact category</h4>
            {cats.map(([k, v], i) => (
              <div className="field-row" key={i}>
                <input className="field-input" value={k} placeholder="tag or category, e.g. news" aria-label="Tag or category" onChange={e => setCats(cats.map((c, j) => j === i ? [e.target.value, c[1]] : c))} />
                <input className="field-input narrow" type="number" min={0} value={v} aria-label="Half-life in days" onChange={e => setCats(cats.map((c, j) => j === i ? [c[0], e.target.value] : c))} />
                <span className="muted small">{Number(v) === 0 ? 'never decays' : 'days'}</span>
                <button type="button" className="link-btn" onClick={() => setCats(cats.filter((_, j) => j !== i))}>Remove</button>
              </div>
            ))}
            <button type="button" className="ghost-btn kt-add" onClick={() => setCats([...cats, ['', '0']])}>+ Add</button>
            <span className="field-help">0 = never decays. For facts the half-life applies to their confidence from the next distillation on.</span>
            <label className="field field-inline kt-switch">
              <span className="field-label">AI reviews the decay policy once a week</span>
              <input type="checkbox" checked={t.decay_ai} disabled={!!busy} onChange={e => put({ decay_ai: e.target.checked }, e.target.checked ? 'The AI reviews the policy weekly while ZenSynora runs.' : 'Weekly review off.')} />
            </label>
            <span className="field-help">One model call a week while ZenSynora runs. Its changes follow the rule above (propose, or inside the safe ranges).</span>
            {saveBar}
          </section>
          <section className="settings-card">
            <h3>What the decision is based on</h3>
            {!digest && <div className="muted small">Loading…</div>}
            {digest && (
              <>
                <p className="muted small">{digest.notes} notes. Bar: notes of that age; filled part: used by an agent in the last 90 days{digest.usage_log ? '' : ' (no usage recorded yet)'}.</p>
                {digest.ages.map(a => (
                  <div className="kb-bar kt-age" key={a.age} title={`${a.used_last_90_days} of ${a.notes} used in the last 90 days`}>
                    <span>{a.age}</span>
                    <span className="kb-bar-track" style={{ width: `${Math.max(4, (a.notes / Math.max(1, ...digest.ages.map(x => x.notes))) * 100)}%` }}>
                      <span className="kb-bar-fill" style={{ width: `${a.notes ? (a.used_last_90_days / a.notes) * 100 : 0}%` }} /></span>
                    <b>{a.notes}</b>
                  </div>
                ))}
                {digest.tags.length > 0 && <><h4>Tags (median age)</h4>
                  <div className="kb-tags">{digest.tags.slice(0, 16).map(x => <button type="button" key={x.name} className="chip small-chip" title="Give this tag its own half-life"
                    onClick={() => !cats.some(([k]) => k === x.name) && setCats([...cats, [x.name, '0']])}>#{x.name} · {x.count} · {x.median_age_days} d</button>)}</div></>}
                {digest.fact_categories.length > 0 && <><h4>Fact categories (median age)</h4>
                  <div className="kb-tags">{digest.fact_categories.slice(0, 16).map(x => <button type="button" key={x.name} className="chip small-chip" title="Give this category its own half-life"
                    onClick={() => !cats.some(([k]) => k === x.name) && setCats([...cats, [x.name, '0']])}>{x.name} · {x.count} · {x.median_age_days} d</button>)}</div></>}
              </>
            )}
          </section>
        </div>
      )}

      {section === 'bench' && <Bench base={base} feedback={t.feedback} onOpenNote={onOpenNote} onApplied={async text => { await load(); onChanged(); setMsg({ ok: true, text }); }} />}
      {section === 'relations' && <KnowledgeRelations base={base} onOpenNote={onOpenNote} onChanged={onChanged} />}

      {section === 'history' && (
        <section className="settings-card">
          <h3>Changes</h3>
          {t.history.length === 0 && <div className="muted">No change yet.</div>}
          <ul className="plain-list">
            {t.history.map(h => (
              <li key={h.id} className="kt-proposal">
                <div>{Object.entries(h.changes).map(([k, [a, b]]) => <div key={k}><code>{k}</code>: {show(a)} → <b>{show(b)}</b>{h.why?.[k] ? <span className="muted small"> — {h.why[k]}</span> : null}</div>)}
                  <div className="muted small">{h.actor} · {when(h.ts)}{h.reason ? ` — ${h.reason}` : ''}</div></div>
                <button type="button" className="ghost-btn" disabled={!!busy} onClick={() => undo(h.id)}>↶ Undo</button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

const Bench: React.FC<{ base: string; feedback: Tuning['feedback']; onOpenNote: (p: string) => void; onApplied: (text: string) => Promise<void> }> = ({ base, feedback, onOpenNote, onApplied }) => {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('auto');
  const [res, setRes] = useState<TestResult | null>(null);
  const [tune, setTune] = useState<AutoTune | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState('');

  const search = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!query.trim()) return;
    setBusy('search'); setErr(null);
    try {
      setRes(await api<TestResult>(`/api/knowledge/tuning/test?${q(base)}`, { method: 'POST', body: JSON.stringify({ query: query.trim(), mode, limit: 15 }) }));
    } catch (x) { setErr((x as Error).message); } finally { setBusy(''); }
  };
  const vote = async (r: Row, v: number) => {
    try {
      await api(`/api/knowledge/tuning/feedback?${q(base)}`, { method: 'POST', body: JSON.stringify({ query: res?.query, permalink: r.permalink, vote: r.vote === v ? 0 : v }) });
      await search();
    } catch (x) { setErr((x as Error).message); }
  };
  const measure = async () => {
    setBusy('tune'); setErr(null);
    try { setTune(await api<AutoTune>(`/api/knowledge/tuning/autotune?${q(base)}`, { method: 'POST' })); } catch (x) { setErr((x as Error).message); } finally { setBusy(''); }
  };
  const apply = async () => {
    if (!tune) return;
    setBusy('apply');
    try {
      await api('/api/knowledge/tuning', { method: 'PUT', body: JSON.stringify({ changes: tune.changes, reason: `measured on ${tune.labelled} marked searches` }) });
      setTune(null);
      await onApplied('Applied the measured settings.');
    } catch (x) { setErr((x as Error).message); } finally { setBusy(''); }
  };

  return (
    <section className="settings-card kt-bench">
      <h3>Relevance test</h3>
      <p className="muted small">Search as an agent would and see why each note is where it is. Mark the right note 👍 (and a wrong one 👎): the same search then shows it first, and the marks are the test set for “Measure”.</p>
      <form className="field-row" onSubmit={search}>
        <input className="field-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="A question or keywords…" aria-label="Test query" />
        <select className="field-input narrow" value={mode} onChange={e => setMode(e.target.value)} aria-label="Search mode">
          <option value="auto">Auto</option><option value="fts">Words</option><option value="semantic">Meaning</option><option value="hybrid">Words + meaning</option>
        </select>
        <button type="submit" className="primary-btn" disabled={!!busy || !query.trim()}>{busy === 'search' ? 'Searching…' : 'Search'}</button>
      </form>
      {err && <div className="notice error">{err}</div>}
      {res && (
        <>
          <div className="muted small">Used: {res.info.mode === 'fts' ? 'words' : res.info.mode === 'semantic' ? 'meaning' : 'words + meaning'}{res.info.fallback ? ` (${res.info.fallback})` : ''} · similarity needed {res.min_score} · decay {res.decay ? 'on' : 'off'}</div>
          {res.results.length === 0 && <div className="kb-empty">No note matches.</div>}
          {res.results.length > 0 && (
            <div className="kt-scroll">
              <table className="data-table kt-table">
                <thead><tr><th>#</th><th>Note</th><th title="Position in the word search">Words</th><th title="Position by meaning, and the similarity">Meaning</th><th title="Weight from temporal decay (1 = fresh)">Decay</th><th>Right?</th></tr></thead>
                <tbody>
                  {res.results.map(r => (
                    <tr key={r.permalink}>
                      <td>{r.position}{r.before !== r.position && <span className="muted small" title="Position before decay and your marks"> ({r.before})</span>}</td>
                      <td><button type="button" className="kb-sort" onClick={() => onOpenNote(r.permalink)}>{r.title}</button></td>
                      <td>{r.word_rank ?? '—'}</td>
                      <td>{r.meaning_rank ? `${r.meaning_rank} · ${r.similarity}` : '—'}</td>
                      <td title={r.half_life_days ? `${r.age_days} days since changed or used; half-life ${r.half_life_days} days` : 'does not decay'}>{r.decay < 1 ? r.decay : '—'}</td>
                      <td className="row-actions">
                        <button type="button" className={`chip small-chip ${r.vote > 0 ? 'active' : ''}`} aria-pressed={r.vote > 0} aria-label={`${r.title}: right result`} onClick={() => vote(r, 1)}>👍</button>
                        <button type="button" className={`chip small-chip ${r.vote < 0 ? 'active' : ''}`} aria-pressed={r.vote < 0} aria-label={`${r.title}: wrong result`} onClick={() => vote(r, -1)}>👎</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <h4>Measure and tune</h4>
      <div className="row-actions">
        <button type="button" className="ghost-btn" disabled={!!busy} onClick={measure}>{busy === 'tune' ? 'Measuring…' : '📏 Measure my marked searches'}</button>
        <span className="muted small">{feedback.labelled} of {feedback.needed} searches marked 👍. Replays them with other weights and similarity floors; changes nothing by itself.</span>
      </div>
      {tune && (
        <div className="notice">
          {tune.message}
          {tune.best && tune.current && Object.keys(tune.changes).length > 0 && (
            <>
              <ul className="plain-list">{Object.entries(tune.changes).map(([k, v]) => <li key={k}><code>{k}</code>: {tune.current?.[k] ?? '—'} → <b>{v}</b></li>)}</ul>
              <button type="button" className="primary-btn" disabled={!!busy} onClick={apply}>{busy === 'apply' ? 'Applying…' : 'Apply these values'}</button>
            </>
          )}
        </div>
      )}
    </section>
  );
};

export default KnowledgeTune;
