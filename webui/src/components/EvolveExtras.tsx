// Evolve page additions (2026-11-04): AI suggestions, a file / function picker for
// "Where", undo of the last installed change, the change-proposal settings, and
// creating an agent with AI.
import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api';

export interface Suggestion { title: string; action: string; target_kind: string; target: string; goal: string; why: string }

// ── AI suggestions ───────────────────────────────────────────────────────────
export const SuggestCard: React.FC<{
  onUse: (s: Suggestion) => void; onStart: (s: Suggestion) => Promise<void>;
}> = ({ onUse, onStart }) => {
  const [list, setList] = useState<Suggestion[] | null>(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const ask = async () => {
    setBusy('ask'); setErr(null);
    try {
      setList((await api<{ suggestions: Suggestion[] }>('/api/evolve/suggest', { method: 'POST' })).suggestions);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy('');
    }
  };
  return (
    <div className="evolve-suggest">
      <div className="probe-row">
        <button className="ghost-btn" disabled={!!busy} onClick={ask}
          title="The default model looks at recent problems, failing models and what was already proposed">
          {busy === 'ask' ? 'The model is looking…' : list ? '↻ Suggest again' : '🤖 Suggest improvements'}</button>
        <span className="muted small">Ideas from recent problems and model health. Nothing starts until you click Start.</span>
      </div>
      {err && <div className="notice error">{err}</div>}
      {list && list.length === 0 && <div className="muted small">No suggestions right now.</div>}
      {list && list.map((s, i) => (
        <div key={i} className="ai-item">
          <div><b>{s.title}</b> <span className="muted small">{s.action} · {s.target_kind}{s.target ? ` · ${s.target}` : ''}</span></div>
          <div className="small">{s.goal}</div>
          {s.why && <div className="muted small">Why: {s.why}</div>}
          <div className="probe-row">
            <button className="ghost-btn small-btn" onClick={() => onUse(s)}>Edit in the form</button>
            <button className="primary-btn small-btn" disabled={!!busy}
              onClick={async () => { setBusy(`s${i}`); try { await onStart(s); setList(l => (l || []).filter((_, j) => j !== i)); } finally { setBusy(''); } }}>
              {busy === `s${i}` ? 'Starting…' : 'Start'}</button>
          </div>
        </div>
      ))}
    </div>
  );
};

// ── "Where" with a picker of real program files and functions ───────────────
export const WherePicker: React.FC<{ value: string; placeholder: string; onChange: (v: string) => void }> = ({ value, placeholder, onChange }) => {
  const [options, setOptions] = useState<string[]>([]);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const file = value.split(':')[0];
      try {
        const files = (await api<{ files: string[] }>(`/api/evolve/files?q=${encodeURIComponent(file)}&limit=40`)).files;
        let opts = files;
        if (file.endsWith('.py') && files.includes(file)) {
          const fns = (await api<{ functions: string[] }>(`/api/evolve/functions?path=${encodeURIComponent(file)}`)).functions;
          opts = [file, ...fns.map(f => `${file}:${f}`)];
        }
        setOptions(opts);
      } catch {
        setOptions([]);
      }
    }, 250);
    return () => window.clearTimeout(timer.current);
  }, [value]);
  return (
    <>
      <input className="field-input" list="evolve-where" value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} />
      <datalist id="evolve-where">{options.map(o => <option key={o} value={o} />)}</datalist>
    </>
  );
};

// ── undo the last installed change ───────────────────────────────────────────
export const UndoCard: React.FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const [st, setSt] = useState<{ installed_version?: string; managed?: boolean; not_managed_reason?: string; update_running?: boolean } | null>(null);
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api<typeof st>('/api/update/status').then(setSt).catch(() => setSt(null)); }, []);
  const undo = async () => {
    setMsg(null);
    try {
      await api('/api/update/rollback', { method: 'POST' });
      setMsg({ ok: true, text: 'Going back to the previous version. Follow it on the Updates page; restart there when it is done.' });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setSure(false);
    }
  };
  return (
    <div className="settings-card">
      <h3>Undo the last installed change</h3>
      <p className="muted small">
        Switches the whole program back to the version before the last update or approved change. Your data, settings and knowledge stay as they are.
        {st?.installed_version ? ` Installed now: ${st.installed_version}.` : ''}
      </p>
      {st && st.managed === false && <div className="muted small">Not available here: {st.not_managed_reason}</div>}
      {msg && <div className={`notice ${msg.ok ? '' : 'error'}`}>{msg.text}</div>}
      <div className="save-actions">
        <button className="ghost-btn" onClick={onOpenUpdates}>Open Updates</button>
        {!sure
          ? <button className="ghost-btn" disabled={!st?.managed || st?.update_running} onClick={() => setSure(true)}>Undo…</button>
          : <><button className="ghost-btn" onClick={() => setSure(false)}>Cancel</button>
            <button className="primary-btn" onClick={undo}>Yes, go back to the previous version</button></>}
      </div>
    </div>
  );
};

// ── change-proposal settings ─────────────────────────────────────────────────
const SETTINGS = [
  { key: 'enabled', label: 'Agents can read the code and file change proposals' },
  { key: 'run_tests', label: 'Run the test suite on the new version before switching to it (needs pytest)' },
  { key: 'auto_approve_low_risk', label: 'Install documentation-only changes without asking' },
] as const;

export const ProposalSettings: React.FC<{ values: Record<string, boolean | undefined>; onSaved: () => void }> = ({ values, onSaved }) => {
  const [err, setErr] = useState<string | null>(null);
  const save = async (key: string, v: boolean) => {
    setErr(null);
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify({ changes: { [`medic.self_update.${key}`]: v } }) });
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="evolve-settings">
      {SETTINGS.map(s => (
        <label key={s.key} className="kb-check small"><input type="checkbox" checked={!!values[s.key]} onChange={e => save(s.key, e.target.checked)} /> {s.label}</label>
      ))}
      {err && <div className="notice error">{err}</div>}
    </div>
  );
};

// ── create an agent with AI ──────────────────────────────────────────────────
interface Draft { name: string; description: string; system_prompt: string; skills: string[]; level: number; note: string }

export const CreateAgent: React.FC<{ onCreated: (text: string) => void }> = ({ onCreated }) => {
  const [wish, setWish] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key); setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(''); }
  };
  return (
    <div>
      <h4>Create an agent with AI</h4>
      <p className="muted small">Describe the agent you need. The AI writes its name, description, prompt and picks skills; you review it. It starts at level 1 with the default model (change that in Settings → Agents).</p>
      <textarea className="field-input" rows={2} value={wish} onChange={e => setWish(e.target.value)}
        placeholder="e.g. Someone who reviews my pull requests for bugs and missing tests" />
      <div className="save-actions">
        <button className="ghost-btn" disabled={!!busy || !wish.trim()}
          onClick={() => run('draft', async () => setDraft(await api<Draft>('/api/evolve/agents/draft', { method: 'POST', body: JSON.stringify({ description: wish }) })))}>
          {busy === 'draft' ? 'The AI is writing…' : 'Draft the agent'}</button>
      </div>
      {err && <div className="notice error">{err}</div>}
      {draft && (
        <div className="settings-card evolve-preview">
          {draft.note && <div className="muted small">⚠ {draft.note}</div>}
          <div className="evolve-form">
            <label className="field"><span className="field-label">Name</span>
              <input className="field-input" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
            <label className="field"><span className="field-label">Description</span>
              <input className="field-input" value={draft.description} onChange={e => setDraft({ ...draft, description: e.target.value })} /></label>
          </div>
          <label className="field"><span className="field-label">System prompt</span>
            <textarea className="field-input" rows={8} value={draft.system_prompt} onChange={e => setDraft({ ...draft, system_prompt: e.target.value })} /></label>
          <div className="muted small">Level 1 · skills: {draft.skills.join(', ') || 'none'}</div>
          <div className="save-actions">
            <button className="ghost-btn" onClick={() => setDraft(null)}>Cancel</button>
            <button className="primary-btn" disabled={!!busy || !draft.name.trim() || !draft.system_prompt.trim()}
              onClick={() => run('create', async () => {
                await api('/api/evolve/agents', { method: 'POST', body: JSON.stringify({ name: draft.name, description: draft.description, system_prompt: draft.system_prompt, skills: draft.skills }) });
                onCreated(`${draft.name} was added at level 1.`);
                setDraft(null); setWish('');
              })}>{busy === 'create' ? 'Adding…' : `Add ${draft.name || 'the agent'}`}</button>
          </div>
        </div>
      )}
    </div>
  );
};
