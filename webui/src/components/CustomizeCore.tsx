// Customize → ZenSynora core (2026-11-07): the identity, communication style, behaviour and
// knowledge habits every agent without its own prompt starts with (the default agent too).
// You edit them here; agents can only propose a change (Proposals tab). The tools and safety
// part is shown but fixed — a change there could break tool calling or let an agent change
// its own rules.
import React, { useEffect, useState } from 'react';
import { api } from '../api';

type CoreField = 'identity' | 'style' | 'behavior' | 'knowledge';
const CORE_FIELDS: CoreField[] = ['identity', 'style', 'behavior', 'knowledge'];
const HELP: Record<CoreField, string> = {
  identity: 'Who the agent is and what it can do. Every agent without its own prompt starts with this.',
  style: 'How it talks to you: tone, language, length.',
  behavior: 'How it works: checks, questions, what to do before acting.',
  knowledge: 'What it saves to the knowledge base, and when.',
};

export interface CoreInfo {
  fields: Record<CoreField, string>; builtin: Record<CoreField, string>; modified: CoreField[];
  history: number; technical: string; updated?: string | null; updated_by?: string | null;
}
interface Props {
  core: CoreInfo;
  users: { agent: string; uses: string }[];
  labels: { id: CoreField; label: string }[];
  maxField: number;
  onSaved: (text: string) => Promise<void>;
  onError: (text: string) => void;
}
interface Review { summary: string; issues: string[]; improved: Record<CoreField, string> }

const CustomizeCore: React.FC<Props> = ({ core, users, labels, maxField, onSaved, onError }) => {
  const [draft, setDraft] = useState<Record<CoreField, string>>(core.fields);
  const [review, setReview] = useState<Review | null>(null);
  const [preview, setPreview] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => { setDraft(core.fields); }, [core]);
  useEffect(() => {
    api<{ text: string }>('/api/customize/core/preview').then(r => setPreview(r.text)).catch(() => setPreview(''));
  }, [core]);

  const dirty = CORE_FIELDS.some(f => draft[f] !== core.fields[f]);
  const tooLong = CORE_FIELDS.some(f => draft[f].length > maxField);
  const label = (f: CoreField) => labels.find(x => x.id === f)?.label || f;

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try { await fn(); } catch (e) { onError((e as Error).message); } finally { setBusy(''); }
  };
  const save = () => run('save', async () => {
    const changed = Object.fromEntries(CORE_FIELDS.filter(f => draft[f] !== core.fields[f]).map(f => [f, draft[f]]));
    await api('/api/customize/core', { method: 'PUT', body: JSON.stringify(changed) });
    setReview(null);
    await onSaved('Saved the ZenSynora core. Agents get it with their next message.');
  });
  const post = (path: string, body: object, text: string) => run(path, async () => {
    await api(`/api/customize/core/${path}`, { method: 'POST', body: JSON.stringify(body) });
    setReview(null);
    await onSaved(text);
  });
  const aiReview = () => run('review', async () => {
    setReview(await api<Review>('/api/customize/review', { method: 'POST', body: JSON.stringify({ profile: 'core' }) }));
  });

  return (
    <div className="settings-card cz-editor">
      <div className="card-head">
        <h3>ZenSynora core</h3>
        {core.updated && <span className="muted small">changed {core.updated} by {core.updated_by}</span>}
      </div>
      <p className="muted">
        The key characteristics every agent starts with — before the profile of the situation (Profiles tab) and your Rules.
        You edit them here; agents can propose a change, which waits for you on the Proposals tab.
      </p>
      <p className="muted small">
        Used by: {users.map(u => (
          <span key={u.agent} className={`badge ${u.uses === 'core' ? 'cz-use' : ''}`} title={u.uses === 'core' ? 'uses the core' : `uses ${u.uses}`}>
            @{u.agent}{u.uses === 'core' ? '' : ' — own prompt'}
          </span>
        ))}
        {users.some(u => u.uses !== 'core') && <> · agents with their own prompt (Settings → Agents) use it instead of the core.</>}
      </p>

      {CORE_FIELDS.map(f => (
        <div className="field" key={f}>
          <label className="field-label">{label(f)}
            {core.modified.includes(f) && <span className="badge">changed</span>}
            <span className={`muted small ${draft[f].length > maxField ? 'cz-over' : ''}`}> · {draft[f].length}/{maxField}</span>
          </label>
          <textarea className="field-input" rows={f === 'identity' ? 3 : 4} value={draft[f]} placeholder={HELP[f]}
            onChange={e => setDraft({ ...draft, [f]: e.target.value })} />
          <div className="row-actions cz-field-actions">
            <span className="muted small">{HELP[f]}</span>
            {draft[f] !== core.builtin[f] && (
              <button className="link-btn" onClick={() => setDraft({ ...draft, [f]: core.builtin[f] })}>Use ZenSynora’s text</button>
            )}
          </div>
          {review?.improved[f] && review.improved[f] !== draft[f] && (
            <div className="cz-suggest">
              <span className="muted small">🤖 Suggested:</span> <span className="pre">{review.improved[f]}</span>
              <button className="link-btn" onClick={() => setDraft({ ...draft, [f]: review.improved[f] })}>Use this</button>
            </div>
          )}
        </div>
      ))}

      <div className="save-actions">
        {core.history > 0 && <button className="ghost-btn" disabled={!!busy} onClick={() => post('undo', {}, 'The earlier version of the core is back.')}>↶ Undo last change</button>}
        {core.modified.length > 0 && (
          <button className="ghost-btn" disabled={!!busy} onClick={() => {
            if (window.confirm('Put back ZenSynora’s text for the whole core? Your version stays in the history (Undo).')) {
              void post('reset', {}, 'The core is back to ZenSynora’s text.');
            }
          }}>Reset all to built-in</button>
        )}
        {dirty && <button className="ghost-btn" onClick={() => setDraft(core.fields)}>Discard changes</button>}
        <button className="primary-btn" disabled={!!busy || !dirty || tooLong || !draft.identity.trim()} onClick={save}>
          {busy === 'save' ? 'Saving…' : 'Save'}</button>
      </div>

      <div className="cz-ai">
        <h4>🤖 AI</h4>
        <div className="row-actions">
          <button className="ghost-btn" disabled={!!busy} onClick={aiReview}>{busy === 'review' ? 'Reviewing…' : 'Review and improve the core'}</button>
          <span className="muted small">Suggestions fill the fields above; nothing is saved until you press Save.</span>
        </div>
        {review && (
          <div className="notice">
            <b>{review.summary || 'Review'}</b>
            {review.issues.length > 0 && <ul className="plain-list">{review.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}
            <button className="link-btn" onClick={() => setDraft({ ...draft, ...Object.fromEntries(
              CORE_FIELDS.filter(f => review.improved[f]).map(f => [f, review.improved[f]])) })}>Use all suggestions</button>
          </div>
        )}
      </div>

      <details className="cz-tools">
        <summary>🔒 Tools and safety — fixed ({core.technical.length} characters)</summary>
        <p className="muted small">
          How to call tools, the tool list, and the safety lines (no secrets in the knowledge base; agents cannot change
          Rules or this page themselves). It is not editable: a change could break tool calling. To keep an agent away
          from a tool, switch the tool off in a profile instead.
        </p>
        <pre className="skill-body">{core.technical}</pre>
      </details>
      {preview && !dirty && (
        <details><summary className="muted small">What an agent gets ({preview.length} characters, before the profile and Rules)</summary>
          <pre className="skill-body">{preview}</pre></details>
      )}
    </div>
  );
};

export default CustomizeCore;
