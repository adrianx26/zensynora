// Skills page (v0.7.5): reusable instructions agents load when a task needs them.
//
// Built-in skills ship with ZenSynora; yours live in ~/.myclaw/skills. Click an
// agent chip on a skill to give it to (or take it from) that agent. Agents see
// their skills' names in their system prompt and read one with use_skill.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { agentColor } from '../colors';

interface SkillRow {
  name: string;
  description: string;
  always: boolean;
  source: 'builtin' | 'yours';
  overrides_builtin?: boolean;
  size?: number;
  body?: string;
  agents: string[];
}

interface Draft { name: string; description: string; body: string; always: boolean; isNew: boolean }

const TEMPLATE = `# What this skill is for

## Steps
1. …
2. …

## Answer
- …`;

const SkillsView: React.FC = () => {
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [missing, setMissing] = useState<string[]>([]);
  const [open, setOpen] = useState<SkillRow | null>(null);   // read-only view
  const [draft, setDraft] = useState<Draft | null>(null);    // editor
  const [filter, setFilter] = useState('');
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<{ skills: SkillRow[]; agents: string[]; missing: string[] }>('/api/skills');
      setSkills(r.skills);
      setAgents(r.agents);
      setMissing(r.missing);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data load
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice({ kind: 'ok', text: ok });
      await load();
      return true;
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
      return false;
    }
  };

  const toggleAgent = (s: SkillRow, agent: string) => {
    const next = s.agents.includes(agent) ? s.agents.filter(a => a !== agent) : [...s.agents, agent];
    act(() => api(`/api/skills/${encodeURIComponent(s.name)}/agents`, { method: 'PUT', body: JSON.stringify({ agents: next }) }),
      s.agents.includes(agent) ? `@${agent} no longer has ${s.name}.` : `@${agent} can now use ${s.name}.`);
  };

  const view = async (s: SkillRow) => {
    setDraft(null);
    try {
      setOpen(await api<SkillRow>(`/api/skills/${encodeURIComponent(s.name)}`));
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const edit = async (s: SkillRow) => {
    try {
      const full = await api<SkillRow>(`/api/skills/${encodeURIComponent(s.name)}`);
      setOpen(null);
      setDraft({ name: full.name, description: full.description, body: full.body || '', always: full.always, isNew: false });
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const copy = (s: SkillRow) => act(async () => {
    const sk = await api<SkillRow>(`/api/skills/${encodeURIComponent(s.name)}/copy`, { method: 'POST' });
    setOpen(null);
    setDraft({ name: sk.name, description: sk.description, body: sk.body || '', always: sk.always, isNew: false });
  }, `${s.name} copied to your skills — change it below. Deleting your copy brings the built-in one back.`);

  const remove = (s: SkillRow) => {
    const what = s.overrides_builtin ? 'your changed copy (the built-in version comes back)' : 'this skill';
    if (!window.confirm(`Delete ${what}: ${s.name}?`)) return;
    act(() => api(`/api/skills/${encodeURIComponent(s.name)}`, { method: 'DELETE' }), `${s.name} deleted.`);
    setOpen(null);
  };

  const save = async () => {
    if (!draft) return;
    const ok = await act(() => api('/api/skills', { method: 'POST', body: JSON.stringify(draft) }),
      `Skill ${draft.name} saved. Give it to agents with the chips below.`);
    if (ok) setDraft(null);
  };

  const importFile = async (f: File | undefined) => {
    if (!f) return;
    const text = await f.text();
    const name = f.name.replace(/\.md$/i, '').replace(/^SKILL$/i, '');
    act(() => api('/api/skills/import', { method: 'POST', body: JSON.stringify({ text, name }) }), `Imported ${f.name}.`);
    if (fileRef.current) fileRef.current.value = '';
  };

  const shown = skills.filter(s => !filter || `${s.name} ${s.description}`.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <div className="card-head">
            <h3>Skills</h3>
            <div className="row-actions">
              <input ref={fileRef} type="file" accept=".md,text/markdown,text/plain" style={{ display: 'none' }}
                onChange={e => importFile(e.target.files?.[0])} />
              <button className="ghost-btn" onClick={() => fileRef.current?.click()} title="Add a SKILL.md file made elsewhere">Import…</button>
              <button className="primary-btn" onClick={() => { setOpen(null); setDraft({ name: '', description: '', body: TEMPLATE, always: false, isNew: true }); }}>+ New skill</button>
            </div>
          </div>
          <p className="muted">
            A skill is a set of instructions for one kind of task. Click an agent under a skill to give it that skill:
            the agent sees the skill's name and description and reads the full instructions (<code>use_skill</code>) when a task matches.
          </p>
          {missing.length > 0 && (
            <div className="notice">Some agents still list skills that no longer exist: {missing.join(', ')}. They are ignored.</div>
          )}
          <input className="field-input" placeholder="Filter skills…" value={filter} onChange={e => setFilter(e.target.value)} />
        </div>

        {draft && (
          <div className="settings-card">
            <h3>{draft.isNew ? 'New skill' : `Edit ${draft.name}`}</h3>
            <div className="agent-editor-grid">
              <div className="field"><label className="field-label">Name (letters, digits, dashes)</label>
                <input className="field-input" value={draft.name} disabled={!draft.isNew} placeholder="e.g. weather-report" autoFocus
                  onChange={e => setDraft({ ...draft, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]+/g, '-') })} /></div>
              <div className="field" style={{ gridColumn: 'span 2' }}><label className="field-label">When to use it (agents see this line)</label>
                <input className="field-input" value={draft.description} placeholder="e.g. Report the weather for a city, with the source"
                  onChange={e => setDraft({ ...draft, description: e.target.value })} /></div>
            </div>
            <div className="field"><label className="field-label">Instructions (Markdown)</label>
              <textarea className="field-input mono" rows={16} value={draft.body} onChange={e => setDraft({ ...draft, body: e.target.value })} /></div>
            <label className="mini-toggle" title="Put the full text in the agent's prompt every time — helps small models that rarely call tools, but uses more tokens">
              <input type="checkbox" checked={draft.always} onChange={e => setDraft({ ...draft, always: e.target.checked })} /> Always include the full instructions
            </label>
            <div className="save-actions">
              <button className="ghost-btn" onClick={() => setDraft(null)}>Cancel</button>
              <button className="primary-btn" disabled={!draft.name || !draft.body.trim()} onClick={save}>Save skill</button>
            </div>
          </div>
        )}

        {open && (
          <div className="settings-card">
            <div className="card-head">
              <h3>{open.name} {open.source === 'builtin' ? <span className="badge">built-in</span> : <span className="badge">yours</span>}</h3>
              <div className="row-actions">
                {open.source === 'builtin'
                  ? <button className="ghost-btn" onClick={() => copy(open)}>Copy to change</button>
                  : <button className="ghost-btn" onClick={() => edit(open)}>Edit</button>}
                <button className="ghost-btn" onClick={() => setOpen(null)}>Close</button>
              </div>
            </div>
            <p className="muted">{open.description}</p>
            <pre className="skill-body">{open.body}</pre>
          </div>
        )}

        <div className="skill-grid">
          {shown.map(s => (
            <div className="settings-card skill-card" key={s.name}>
              <div className="card-head">
                <h3 className="skill-name" onClick={() => view(s)}>{s.name}</h3>
                <span>
                  {s.source === 'builtin' ? <span className="badge">built-in</span>
                    : <span className="badge">{s.overrides_builtin ? 'yours (changed)' : 'yours'}</span>}
                  {s.always && <span className="badge" title="Full text is always in the prompt">always</span>}
                </span>
              </div>
              <p className="muted small">{s.description}</p>
              <div className="chip-row">
                <span className="muted small">Agents:</span>
                {agents.map(a => (
                  <button key={a} className={`chip small-chip ${s.agents.includes(a) ? 'active' : ''}`} onClick={() => toggleAgent(s, a)}
                    title={s.agents.includes(a) ? `Take ${s.name} away from @${a}` : `Give ${s.name} to @${a}`}>
                    <span className="member-dot" style={{ background: agentColor(a) }} /> @{a}
                  </button>
                ))}
              </div>
              <div className="row-actions">
                <button className="ghost-btn" onClick={() => view(s)}>View</button>
                {s.source === 'builtin'
                  ? <button className="ghost-btn" onClick={() => copy(s)}>Copy to change</button>
                  : <>
                    <button className="ghost-btn" onClick={() => edit(s)}>Edit</button>
                    <button className="ghost-btn danger" onClick={() => remove(s)}>Delete</button>
                  </>}
              </div>
            </div>
          ))}
          {shown.length === 0 && <div className="muted">No skills match.</div>}
        </div>
      </div>
    </div>
  );
};

export default SkillsView;
