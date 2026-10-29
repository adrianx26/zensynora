// Agent fusion (2026-10-27): two agents become one — only after you approve.
//
// The medic (tool propose_agent_fusion) or you propose a fusion; the preview
// shows the fused agent (merged prompt as a diff, skills, the LOWER level of
// the two, model) and what moves (channels, jobs, direct conversations).
// Approve applies it; Undo puts both agents back from the backup.
import React, { useCallback, useEffect, useState } from 'react';
import { ago, api, type AgentFusion } from '../api';
import { LevelBadge } from './LevelBadge';

function DiffView({ diff }: { diff: string }) {
  if (!diff) return <div className="muted small">(no change)</div>;
  return (
    <pre className="diff">
      {diff.split('\n').map((l, i) => (
        <span key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : ''}>
          {l}{'\n'}
        </span>
      ))}
    </pre>
  );
}

interface Props {
  agents: { name: string; level?: number }[];
  dirty: boolean; // unsaved settings: applying would overwrite them
  onChanged: () => void; // reload settings after apply / undo
}

const STATUS: Record<AgentFusion['status'], string> = {
  pending: 'waiting for you', applying: 'applying…', applied: 'applied', failed: 'failed (rolled back)',
  rejected: 'rejected', undone: 'undone',
};
const statusText = (p: AgentFusion) => (p.status === 'applying' && p.interrupted ? 'interrupted — click Undo' : STATUS[p.status]);
const badgeClass = (p: AgentFusion) => (p.status === 'applied' ? 'applied' : p.status === 'failed' || p.interrupted ? 'failed' : 'pending');

export const AgentFusions: React.FC<Props> = ({ agents, dirty, onChanged }) => {
  const [items, setItems] = useState<AgentFusion[]>([]);
  const [open, setOpen] = useState<AgentFusion | null>(null);
  const [form, setForm] = useState({ agent_a: '', agent_b: '', new_name: '', reason: '', use_model: true, allow_default: false });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await api<{ fusions: AgentFusion[] }>('/api/agents/fusions')).fusions);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const show = async (id: string) => {
    if (open?.id === id) { setOpen(null); return; }
    try {
      setOpen(await api<AgentFusion>(`/api/agents/fusions/${id}`));
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const propose = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      const p = await api<AgentFusion>('/api/agents/fusions', { method: 'POST', body: JSON.stringify(form) });
      setOpen(p);
      setForm({ ...form, new_name: '', reason: '' });
      setNotice({ kind: 'ok', text: 'Preview ready — nothing has changed yet. Review it and approve or reject it below.' });
      load();
    } catch (err) {
      setNotice({ kind: 'error', text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const act = async (p: AgentFusion, action: 'approve' | 'reject' | 'undo') => {
    if (action === 'approve' && !window.confirm(
      `Fuse @${p.agent_a} and @${p.agent_b} into @${p.fused.name}? The two agents are replaced; you can undo it later.`)) return;
    let body = '{}';
    if (action === 'undo' && p.status === 'applied') {
      // 2026-10-28: warn before an undo that would lose later changes (or is blocked by a later fusion).
      try {
        const pv = await api<AgentFusion>(`/api/agents/fusions/${p.id}`);
        const check = pv.undo_check;
        if (check?.blocked_by.length) {
          setNotice({ kind: 'error', text: `@${p.fused.name} is part of a later fusion (${check.blocked_by.join(', ')}) — undo that one first.` });
          return;
        }
        if (check?.changed.length) {
          if (!window.confirm(`@${p.fused.name} was changed after the fusion:\n- ${check.changed.join('\n- ')}\n\nUndo anyway? These changes will be lost.`)) return;
          body = JSON.stringify({ force: true });
        }
      } catch (err) {
        setNotice({ kind: 'error', text: (err as Error).message });
        return;
      }
    }
    setBusy(true);
    setNotice(null);
    try {
      const r = await api<AgentFusion>(`/api/agents/fusions/${p.id}/${action}`, { method: 'POST', body });
      setOpen(null);
      setNotice({
        kind: 'ok',
        text: action === 'approve' ? `@${r.fused.name} is ready (level ${r.fused.level}). Moved ${r.moved?.channels ?? 0} channel membership(s), ${r.moved?.jobs ?? 0} job(s), ${r.moved?.messages ?? 0} message(s).`
          : action === 'undo' ? `Undone: @${p.agent_a} and @${p.agent_b} are back.` : 'Rejected.',
      });
      load();
      if (action !== 'reject') onChanged();
    } catch (err) {
      setNotice({ kind: 'error', text: (err as Error).message });
      load();
    } finally {
      setBusy(false);
    }
  };

  const names = agents.map(a => a.name);
  const visible = items.filter(p => ['pending', 'applying', 'applied', 'failed'].includes(p.status));

  return (
    <div className="settings-card fusion-card">
      <h3>Fusion proposals</h3>
      <p className="muted">
        Two agents can become one: merged prompt, both skills, and the <b>lower</b> level of the two. The medic can
        propose a fusion; nothing changes until you approve it. Channels, jobs and direct conversations move to the
        fused agent; both old agents are kept in a backup so you can undo it.
      </p>
      <form className="fusion-row" onSubmit={propose}>
        <select className="field-input" value={form.agent_a} onChange={e => setForm({ ...form, agent_a: e.target.value })} aria-label="First agent">
          <option value="">First agent…</option>
          {names.filter(n => n !== 'default' || form.allow_default).map(n => <option key={n} value={n}>@{n}</option>)}
        </select>
        <span>+</span>
        <select className="field-input" value={form.agent_b} onChange={e => setForm({ ...form, agent_b: e.target.value })} aria-label="Second agent">
          <option value="">Second agent…</option>
          {names.filter(n => n !== form.agent_a && (n !== 'default' || form.allow_default)).map(n => <option key={n} value={n}>@{n}</option>)}
        </select>
        <input className="field-input" placeholder={`New name (default: ${form.agent_a || 'first agent'})`} value={form.new_name}
          onChange={e => setForm({ ...form, new_name: e.target.value.replace(/\s+/g, '_') })} />
        <input className="field-input" placeholder="Why (optional)" value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} />
        <label className="small"><input type="checkbox" checked={form.use_model} onChange={e => setForm({ ...form, use_model: e.target.checked })} /> merge prompts with the default model</label>
        <label className="small" title="The default agent stays the default agent: it keeps its model and prompt and gets the other agent's skills.">
          <input type="checkbox" checked={form.allow_default} onChange={e => setForm({ ...form, allow_default: e.target.checked })} /> include the default agent
        </label>
        <button className="ghost-btn" type="submit" disabled={busy || !form.agent_a || !form.agent_b}>{busy ? 'Working…' : 'Preview fusion'}</button>
      </form>
      {notice && <div className={notice.kind === 'ok' ? 'probe-ok' : 'probe-err'}>{notice.text}</div>}
      {dirty && <div className="muted small">Save or discard your unsaved settings before applying or undoing a fusion.</div>}
      {items.some(p => p.status === 'applying') && (
        <div className="probe-err small">
          A fusion {items.some(p => p.status === 'applying' && p.interrupted) ? 'was interrupted and is half done' : 'is being applied'}:
          {' '}new fusions can be proposed or applied only after it is finished or undone (Undo below).
        </div>
      )}
      {visible.length === 0 && <div className="muted small">No open or applied fusions.</div>}
      {visible.map(p => (
        <div className="fusion-item" key={p.id}>
          <div className="fusion-row">
            <b>@{p.agent_a}</b><LevelBadge level={p.originals[p.agent_a]?.level} /> + <b>@{p.agent_b}</b><LevelBadge level={p.originals[p.agent_b]?.level} />
            → <b>@{p.fused.name}</b><LevelBadge level={p.fused.level} />
            <span className={`badge proposal-${badgeClass(p)}`}>{statusText(p)}</span>
            <span className="muted small">by {p.author} · {ago(p.created)}</span>
            <button className="ghost-btn" onClick={() => show(p.id)}>{open?.id === p.id ? 'Hide' : 'Preview'}</button>
            {p.status === 'pending' && <>
              <button className="primary-btn" disabled={busy || dirty} onClick={() => act(p, 'approve')}>Approve and apply</button>
              <button className="ghost-btn" disabled={busy} onClick={() => act(p, 'reject')}>Reject</button>
            </>}
            {(p.status === 'applied' || (p.status === 'applying' && p.interrupted)) && <button className="ghost-btn" disabled={busy || dirty} onClick={() => act(p, 'undo')}>Undo</button>}
          </div>
          {p.reason && <div className="muted small">Why: {p.reason}</div>}
          {p.error && <div className="probe-err small">{p.status === 'failed' ? 'Applying failed; everything was put back: ' : ''}{p.error}</div>}
          {open?.id === p.id && (
            <div className="fusion-preview">
              <p>{open.summary}</p>
              <div className="small">
                Skills: {open.fused.skills.join(', ') || 'none'} · Model: {open.fused.provider || 'default'}/{open.fused.model || 'default'}
                {' '}· Context: {open.fused.context_length || 'model default'} · Prompt: {open.prompt_source}
              </div>
              {open.impact && (
                <div className="small">
                  Moves: {open.impact.channels.map(c => `#${c.name}`).join(', ') || 'no channels'};
                  {' '}{open.impact.dms.map(c => `conversation with @${c.name} (${c.messages} messages)`).join(', ') || 'no conversations'};
                  {' '}{open.impact.jobs.map(j => `job "${j.name}"`).join(', ') || 'no jobs'}.
                  {' '}Private conversation memory files stay as they are.
                </div>
              )}
              <h4>Prompt: @{open.agent_a} → fused</h4>
              <DiffView diff={open.diff_a || ''} />
              <h4>Prompt: @{open.agent_b} → fused</h4>
              <DiffView diff={open.diff_b || ''} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default AgentFusions;
