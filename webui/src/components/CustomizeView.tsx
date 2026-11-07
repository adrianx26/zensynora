// Customize page (2026-11-07, v0.8.1): how ZenSynora's agents work.
//
// Profiles hold the communication style, the response structure, the behaviour and
// the tools switched off. Every agent picks a profile per situation (chat, jobs, a
// task from another agent). Agents can only propose changes; nothing changes until
// you approve. The AI can review a profile or adapt it to a scenario (shown here,
// saved only when you choose). Telegram can be answered by one agent or by a team
// whose leader answers. The built-in features are the switches of Settings → Features.
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import SettingsView from './SettingsView';
import CustomizeCore, { type CoreInfo } from './CustomizeCore';

type Field = 'style' | 'structure' | 'behavior';
type Situation = 'chat' | 'job' | 'agent';
const FIELDS: Field[] = ['style', 'structure', 'behavior'];

interface Profile {
  slug: string; name: string; description: string;
  style: string; structure: string; behavior: string; tools_off: string[];
  builtin: boolean; modified: boolean; history: number; updated?: string; updated_by?: string;
}
interface Proposal {
  id: number; profile: string; profile_name: string; field: string; text: string; reason: string;
  agent: string; status: 'pending' | 'approved' | 'rejected'; created: string; decided: string | null; decided_by: string | null;
}
interface Data {
  profiles: Profile[]; use: Record<Situation, string>; choices: Choices; agents: string[];
  agents_may_propose: boolean; proposals: Proposal[]; pending: number;
  tools: { name: string; desc: string }[];
  situations: { id: Situation; label: string }[];
  fields: { id: Field; label: string }[];
  max_field: number;
  core: CoreInfo;
  core_users: { agent: string; uses: string }[];
  core_fields: { id: 'identity' | 'style' | 'behavior' | 'knowledge'; label: string }[];
}
type Choices = Record<string, Partial<Record<Situation, string>>>;
interface Draft { slug: string; name: string; description: string; style: string; structure: string; behavior: string; tools_off: string[] }
interface Review { summary: string; issues: string[]; improved: Record<Field, string> }

type Tab = 'core' | 'profiles' | 'use' | 'telegram' | 'proposals' | 'features';
const TAB_LABEL: Record<Tab, string> = {
  core: 'ZenSynora core', profiles: 'Profiles', use: 'Who uses what', telegram: 'Telegram', proposals: 'Proposals', features: 'Built-in features',
};
const HELP: Record<Field, string> = {
  style: 'Tone and wording: e.g. friendly and direct, Romanian, no jargon.',
  structure: 'How an answer is laid out: e.g. the answer first, then steps; at most 10 lines.',
  behavior: 'How to work: e.g. ask before deleting, check before reporting, say what is uncertain.',
};

const toDraft = (p: Profile): Draft => ({ slug: p.slug, name: p.name, description: p.description, style: p.style,
  structure: p.structure, behavior: p.behavior, tools_off: [...p.tools_off] });

const CustomizeView: React.FC = () => {
  const [data, setData] = useState<Data | null>(null);
  const [choices, setChoices] = useState<Choices>({});
  const [use, setUse] = useState<Record<Situation, string> | null>(null);
  const [tab, setTab] = useState<Tab>('core');
  const [sel, setSel] = useState<string>('default');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const [scenario, setScenario] = useState('');
  const [toolQ, setToolQ] = useState('');
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [tg, setTg] = useState<{ lead: string; team: string[] } | null>(null);

  const load = useCallback(async (keepSel?: string) => {
    try {
      const d = await api<Data>('/api/customize');
      setData(d);
      setUse(d.use);
      setChoices(d.choices || {});
      const slug = keepSel && d.profiles.some(p => p.slug === keepSel) ? keepSel : (d.profiles[0]?.slug || 'default');
      setSel(slug);
      const p = d.profiles.find(x => x.slug === slug);
      if (p) setDraft(toDraft(p));
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!draft?.slug) { setPreview(''); return; }
    api<{ text: string }>(`/api/customize/profiles/${draft.slug}/preview`).then(r => setPreview(r.text)).catch(() => setPreview(''));
  }, [draft?.slug, data]);

  const loadTelegram = useCallback(async () => {
    try {
      const s = await api<{ config: { channels?: { telegram?: { answer_agent?: string; team?: string[] } } } }>('/api/settings');
      const t = s.config.channels?.telegram || {};
      setTg({ lead: t.answer_agent || '', team: t.team || [] });
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);
  useEffect(() => { if (tab === 'telegram') void loadTelegram(); }, [tab, loadTelegram]);

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key); setNotice(null);
    try { await fn(); } catch (e) { setNotice({ kind: 'error', text: (e as Error).message }); } finally { setBusy(''); }
  };

  if (!data || !use) return <div className="settings-view muted">{notice?.text || 'Loading…'}</div>;

  const prof = data.profiles.find(p => p.slug === sel);
  const usedFor = (slug: string) => data.situations.filter(s => use[s.id] === slug).map(s => s.id);
  const dirty = !!draft && (!prof || FIELDS.some(f => draft[f] !== prof[f]) || draft.name !== prof.name
    || draft.description !== prof.description || draft.tools_off.join(',') !== prof.tools_off.join(','));
  const tooLong = !!draft && FIELDS.some(f => draft[f].length > data.max_field);

  const pick = (slug: string) => {
    const p = data.profiles.find(x => x.slug === slug);
    setSel(slug); setReview(null); if (p) setDraft(toDraft(p));
  };

  const save = () => run('save', async () => {
    if (!draft) return;
    const body = { name: draft.name, description: draft.description, style: draft.style, structure: draft.structure,
      behavior: draft.behavior, tools_off: draft.tools_off };
    const p = draft.slug
      ? await api<Profile>(`/api/customize/profiles/${draft.slug}`, { method: 'PUT', body: JSON.stringify(body) })
      : await api<Profile>('/api/customize/profiles', { method: 'POST', body: JSON.stringify(body) });
    setNotice({ kind: 'ok', text: `Saved “${p.name}”. Agents using it get it with their next message.` });
    setReview(null);
    await load(p.slug);
  });

  const action = (what: 'undo' | 'reset' | 'delete') => run(what, async () => {
    if (!prof) return;
    if (what === 'delete') {
      if (!window.confirm(`Delete “${prof.name}”? It is kept in ~/.myclaw/.trash; agents using it go back to Default.`)) return;
      await api(`/api/customize/profiles/${prof.slug}`, { method: 'DELETE' });
      setNotice({ kind: 'ok', text: `Deleted “${prof.name}” (kept in ~/.myclaw/.trash).` });
      await load('default');
      return;
    }
    await api(`/api/customize/profiles/${prof.slug}/${what}`, { method: 'POST' });
    setNotice({ kind: 'ok', text: what === 'undo' ? 'The earlier version is back.' : 'Back to ZenSynora’s text.' });
    await load(prof.slug);
  });

  const aiReview = () => run('review', async () => {
    if (!prof) return;
    setReview(await api<Review>('/api/customize/review', { method: 'POST', body: JSON.stringify({ profile: prof.slug }) }));
  });

  const aiAdapt = () => run('adapt', async () => {
    if (!prof) return;
    const d = await api<Draft & { from: string }>('/api/customize/adapt', { method: 'POST',
      body: JSON.stringify({ profile: prof.slug, scenario }) });
    setSel(''); setReview(null);
    setDraft({ slug: '', name: d.name, description: d.description, style: d.style, structure: d.structure,
      behavior: d.behavior, tools_off: d.tools_off || [] });
    setNotice({ kind: 'ok', text: 'Draft ready below — check it, then “Create profile”. Nothing is saved yet.' });
  });

  const saveUse = (agentsMayPropose?: boolean) => run('use', async () => {
    await api('/api/customize/use', { method: 'PUT', body: JSON.stringify({ use, agents: choices,
      ...(agentsMayPropose === undefined ? {} : { agents_may_propose: agentsMayPropose }) }) });
    setNotice({ kind: 'ok', text: 'Saved. It applies to the next message.' });
    await load(sel);
  });

  const saveTelegram = () => run('tg', async () => {
    if (!tg) return;
    await api('/api/settings', { method: 'PUT', body: JSON.stringify({ changes: {
      'channels.telegram.answer_agent': tg.lead, 'channels.telegram.team': tg.team.filter(t => t !== (tg.lead || 'default')) } }) });
    setNotice({ kind: 'ok', text: 'Saved. The next Telegram message is answered this way.' });
    await loadTelegram();
  });

  const decide = (p: Proposal, verdict: 'approve' | 'reject') => run(`p${p.id}`, async () => {
    await api(`/api/customize/proposals/${p.id}/${verdict}`, { method: 'POST' });
    setNotice({ kind: 'ok', text: `Proposal #${p.id} ${verdict === 'approve' ? 'approved' : 'rejected'}.` });
    await load(sel);
  });

  const pending = data.proposals.filter(p => p.status === 'pending');
  const decided = data.proposals.filter(p => p.status !== 'pending');
  const toolList = data.tools.filter(t => !toolQ || t.name.includes(toolQ.toLowerCase()) || t.desc.toLowerCase().includes(toolQ.toLowerCase()));

  return (
    <div className="settings-view customize-view">
      <div className="settings-tabs" role="tablist">
        {(Object.keys(TAB_LABEL) as Tab[]).map(t => (
          <button key={t} role="tab" aria-selected={tab === t} className={`settings-tab ${tab === t ? 'active' : ''}`} onClick={() => { setTab(t); setNotice(null); }}>
            {TAB_LABEL[t]}{t === 'proposals' && data.pending > 0 && <span className="badge doctor-badge warn">{data.pending}</span>}
          </button>
        ))}
      </div>
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        {tab === 'core' && (
          <CustomizeCore core={data.core} users={data.core_users} labels={data.core_fields} maxField={data.max_field}
            onSaved={async text => { setNotice({ kind: 'ok', text }); await load(sel); }}
            onError={text => setNotice({ kind: 'error', text })} />
        )}

        {tab === 'profiles' && (
          <div className="cz-layout">
            <div className="cz-list">
              {data.profiles.map(p => (
                <button key={p.slug} className={`cz-item ${sel === p.slug ? 'active' : ''}`} onClick={() => pick(p.slug)}>
                  <b>{p.name}</b>
                  <span className="muted small">{p.description || '—'}</span>
                  <span className="cz-badges">
                    {p.builtin && <span className="badge">built-in</span>}
                    {p.builtin && p.modified && <span className="badge">changed</span>}
                    {usedFor(p.slug).map(s => <span key={s} className="badge cz-use">{s}</span>)}
                  </span>
                </button>
              ))}
              <button className="ghost-btn" onClick={() => { setSel(''); setReview(null);
                setDraft({ slug: '', name: '', description: '', style: '', structure: '', behavior: '', tools_off: [] }); }}>+ New profile</button>
            </div>

            {draft && (
              <div className="settings-card cz-editor">
                <div className="card-head">
                  <h3>{draft.slug ? `Profile “${prof?.name}”` : 'New profile'}</h3>
                  {prof?.updated && <span className="muted small">changed {prof.updated} by {prof.updated_by}</span>}
                </div>
                <p className="muted small">
                  Added to the agent’s instructions on top of ZenSynora’s built-in prompt (tool calling and your Rules stay as they are).
                  Empty fields add nothing. Never put passwords or keys here.
                </p>
                <div className="agent-editor-grid">
                  <div className="field"><label className="field-label">Name</label>
                    <input className="field-input" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></div>
                  <div className="field" style={{ gridColumn: 'span 2' }}><label className="field-label">What it is for</label>
                    <input className="field-input" value={draft.description} onChange={e => setDraft({ ...draft, description: e.target.value })} /></div>
                </div>
                {FIELDS.map(f => (
                  <div className="field" key={f}>
                    <label className="field-label">{data.fields.find(x => x.id === f)?.label}
                      <span className={`muted small ${draft[f].length > data.max_field ? 'cz-over' : ''}`}> · {draft[f].length}/{data.max_field}</span></label>
                    <textarea className="field-input" rows={4} value={draft[f]} placeholder={HELP[f]}
                      onChange={e => setDraft({ ...draft, [f]: e.target.value })} />
                    {review?.improved[f] && review.improved[f] !== draft[f] && (
                      <div className="cz-suggest">
                        <span className="muted small">🤖 Suggested:</span> <span className="pre">{review.improved[f]}</span>
                        <button className="link-btn" onClick={() => setDraft({ ...draft, [f]: review.improved[f] })}>Use this</button>
                      </div>
                    )}
                  </div>
                ))}
                <details className="cz-tools">
                  <summary>Tools switched off ({draft.tools_off.length}) — the agent is not offered them</summary>
                  <input className="field-input" placeholder="Filter tools…" value={toolQ} onChange={e => setToolQ(e.target.value)} />
                  <div className="cz-tool-grid">
                    {toolList.map(t => (
                      <label key={t.name} className="mini-toggle" title={t.desc}>
                        <input type="checkbox" checked={draft.tools_off.includes(t.name)} onChange={e => setDraft({ ...draft,
                          tools_off: e.target.checked ? [...draft.tools_off, t.name].sort() : draft.tools_off.filter(x => x !== t.name) })} />
                        {t.name}
                      </label>
                    ))}
                  </div>
                </details>
                <div className="save-actions">
                  {prof && prof.history > 0 && <button className="ghost-btn" disabled={!!busy} onClick={() => action('undo')}>↶ Undo last change</button>}
                  {prof?.builtin && prof.modified && <button className="ghost-btn" disabled={!!busy} onClick={() => action('reset')}>Reset to built-in</button>}
                  {prof && !prof.builtin && <button className="ghost-btn danger" disabled={!!busy} onClick={() => action('delete')}>Delete</button>}
                  {dirty && prof && <button className="ghost-btn" onClick={() => pick(prof.slug)}>Discard changes</button>}
                  <button className="primary-btn" disabled={!!busy || !draft.name.trim() || tooLong || (!dirty && !!draft.slug)} onClick={save}>
                    {busy === 'save' ? 'Saving…' : draft.slug ? 'Save' : 'Create profile'}</button>
                </div>

                {prof && (
                  <div className="cz-ai">
                    <h4>🤖 AI</h4>
                    <div className="row-actions">
                      <button className="ghost-btn" disabled={!!busy} onClick={aiReview}>{busy === 'review' ? 'Reviewing…' : 'Review and improve'}</button>
                      <input className="field-input" placeholder="Adapt for a scenario, e.g. answers on Telegram while I drive"
                        value={scenario} onChange={e => setScenario(e.target.value)} />
                      <button className="ghost-btn" disabled={!!busy || !scenario.trim()} onClick={aiAdapt}>{busy === 'adapt' ? 'Writing…' : 'Adapt'}</button>
                    </div>
                    {review && (
                      <div className="notice">
                        <b>{review.summary || 'Review'}</b>
                        {review.issues.length > 0 && <ul className="plain-list">{review.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}
                        <div className="row-actions">
                          <button className="link-btn" onClick={() => setDraft({ ...draft, ...Object.fromEntries(
                            FIELDS.filter(f => review.improved[f]).map(f => [f, review.improved[f]])) })}>Use all suggestions</button>
                          <span className="muted small">Suggestions fill the fields above; nothing is saved until you press Save.</span>
                        </div>
                      </div>
                    )}
                    <p className="muted small">Uses the default model (with its fallbacks). The profile is sent to it as information to review.</p>
                  </div>
                )}

                {preview && !dirty && (
                  <details><summary className="muted small">What the agent gets ({preview.length} characters)</summary>
                    <pre className="skill-body">{preview}</pre></details>
                )}
              </div>
            )}
          </div>
        )}

        {tab === 'use' && (
          <div className="settings-card">
            <h3>Which profile each agent uses</h3>
            <p className="muted">
              The situation is found by itself: <b>chat</b> (Web UI channels and Telegram), <b>jobs</b> (a job run) and
              <b> tasks from another agent</b> (delegate, swarms). Agents can also propose their own choice — you approve it.
            </p>
            <table className="cz-table">
              <thead><tr><th></th>{data.situations.map(s => <th key={s.id}>{s.label}</th>)}</tr></thead>
              <tbody>
                <tr><td><b>All agents</b></td>{data.situations.map(s => (
                  <td key={s.id}><select className="field-input" value={use[s.id]} onChange={e => setUse({ ...use, [s.id]: e.target.value })}>
                    {data.profiles.map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}</select></td>))}</tr>
                {data.agents.map(a => (
                  <tr key={a}><td>@{a}</td>{data.situations.map(s => (
                    <td key={s.id}><select className="field-input" value={choices[a]?.[s.id] || ''}
                      onChange={e => setChoices({ ...choices, [a]: { ...(choices[a] || {}), [s.id]: e.target.value } })}>
                      <option value="">same as all agents</option>
                      {data.profiles.map(p => <option key={p.slug} value={p.slug}>{p.name}</option>)}</select></td>))}</tr>
                ))}
              </tbody>
            </table>
            <label className="mini-toggle">
              <input type="checkbox" checked={data.agents_may_propose} onChange={e => saveUse(e.target.checked)} />
              Agents may propose changes (you approve each one on the Proposals tab)
            </label>
            <div className="save-actions"><button className="primary-btn" disabled={!!busy} onClick={() => saveUse()}>{busy === 'use' ? 'Saving…' : 'Save'}</button></div>
          </div>
        )}

        {tab === 'telegram' && (
          <div className="settings-card">
            <h3>Who answers on Telegram</h3>
            <p className="muted">
              A message without <code>@name</code> goes to the agent below. With a team, that agent is the <b>team leader</b>: it answers you
              and manages the team — it gives each member its part (delegate), checks the results and sends one answer.
              A message that starts with <code>@Agent</code> always goes straight to that agent.
            </p>
            {!tg ? <div className="muted">Loading…</div> : (
              <>
                <div className="field"><label className="field-label">Answering agent (team leader)</label>
                  <select className="field-input" value={tg.lead || 'default'} onChange={e => setTg({ ...tg, lead: e.target.value === 'default' ? '' : e.target.value })}>
                    {data.agents.map(a => <option key={a} value={a}>@{a}</option>)}</select></div>
                <div className="field"><label className="field-label">Team members (none = the agent answers alone)</label>
                  <div className="chip-row">
                    {data.agents.filter(a => a !== (tg.lead || 'default')).map(a => (
                      <button type="button" key={a} className={`chip small-chip ${tg.team.includes(a) ? 'active' : ''}`}
                        onClick={() => setTg({ ...tg, team: tg.team.includes(a) ? tg.team.filter(x => x !== a) : [...tg.team, a] })}>@{a}</button>
                    ))}
                  </div></div>
                <div className="save-actions"><button className="primary-btn" disabled={!!busy} onClick={saveTelegram}>{busy === 'tg' ? 'Saving…' : 'Save'}</button></div>
              </>
            )}
          </div>
        )}

        {tab === 'proposals' && (
          <div className="settings-card">
            <h3>Proposals from agents</h3>
            <p className="muted">Agents cannot change the customization. They propose; nothing changes until you approve.</p>
            {pending.length === 0 && <div className="muted">No proposals waiting.</div>}
            {pending.map(p => (
              <div key={p.id} className="settings-card rule-proposal">
                <div className="card-head">
                  <h3>#{p.id} {p.field.startsWith('use_') ? `@${p.agent} wants to use “${p.text}” for ${p.field.slice(4)}`
                    : `${p.profile_name || p.profile} → ${p.field}`}</h3>
                  <span className="muted small">by @{p.agent} · {p.created}</span>
                </div>
                <p className="pre"><b>Why:</b> {p.reason || '—'}</p>
                {!p.field.startsWith('use_') && (() => {
                  const isCore = p.profile === 'core';
                  const cur = isCore ? undefined : data.profiles.find(x => x.slug === p.profile);
                  const now = isCore ? String(data.core.fields[p.field as keyof CoreInfo['fields']] ?? '')
                    : cur ? (p.field === 'tools_off' ? cur.tools_off.join(', ') : String(cur[p.field as Field] ?? '')) : '';
                  return (
                    <div className="cz-compare">
                      <div><span className="muted small">{cur || isCore ? 'Now' : 'New profile'}</span><pre className="skill-body">{cur || isCore ? (now || '(empty)') : p.profile_name}</pre></div>
                      <div><span className="muted small">Proposed</span><pre className="skill-body">{p.text}</pre></div>
                    </div>
                  );
                })()}
                <div className="save-actions">
                  <button className="ghost-btn" disabled={!!busy} onClick={() => decide(p, 'reject')}>Reject</button>
                  <button className="primary-btn" disabled={!!busy} onClick={() => decide(p, 'approve')}>{busy === `p${p.id}` ? 'Working…' : 'Approve'}</button>
                </div>
              </div>
            ))}
            {decided.length > 0 && (
              <details><summary className="muted small">Decided ({decided.length})</summary>
                {decided.map(p => (
                  <div key={p.id} className="proposal-row muted small">
                    <span className={`badge proposal-${p.status === 'approved' ? 'applied' : 'rejected'}`}>{p.status}</span>
                    <b>#{p.id} {p.profile} → {p.field}</b> <span>by @{p.agent} · {p.decided_by} {p.decided}</span>
                  </div>
                ))}
              </details>
            )}
          </div>
        )}

        {tab === 'features' && (
          <>
            <p className="muted">The built-in features of ZenSynora — the same switches as Settings → Features.</p>
            <SettingsView only="Features" />
          </>
        )}
      </div>
    </div>
  );
};

export default CustomizeView;
