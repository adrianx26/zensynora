// Rules page (2026-10-27): the user's rules every agent follows.
//
// Rules live in ~/.myclaw/rules/<slug>.md. You write, change, switch off and
// delete them here (deleted and replaced versions go to .trash). Agents can
// only propose a new rule, a change or a deletion — the proposals appear at
// the top with a diff, and nothing changes until you approve.
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

type Priority = 'high' | 'normal' | 'low';

interface Rule {
  slug: string;
  title: string;
  scope: string[];
  priority: Priority;
  enabled: boolean;
  author: string;
  created: string;
  updated: string;
  text: string;
  // 2026-10-27 (review): the file differs from what you last approved
  state?: 'ok' | 'changed' | 'unverified';
  review_diff?: string;
  has_approved?: boolean;
}

interface RuleProposal {
  id: number;
  action: 'create' | 'modify' | 'delete';
  slug: string;
  title: string;
  reason: string;
  proposed_by: string;
  created: string;
  status: 'pending' | 'approved' | 'rejected';
  decided_at: string | null;
  decided_by: string | null;
  note: string;
  diff?: string;
  stale?: boolean;
}

interface Draft { slug: string; title: string; text: string; scope: string[]; priority: Priority; enabled: boolean; isNew: boolean }

const ACTION_LABEL: Record<RuleProposal['action'], string> = { create: 'new rule', modify: 'change', delete: 'delete' };

function DiffView({ diff }: { diff: string }) {
  if (!diff) return <div className="muted small">No difference to the rule as it is now.</div>;
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

function scopeText(scope: string[]) {
  return scope.length === 0 || scope.includes('all') ? 'all agents' : scope.map(s => '@' + s).join(', ');
}

function when(iso: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const RulesView: React.FC<{ onChanged?: () => void }> = ({ onChanged }) => {
  const [rules, setRules] = useState<Rule[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [maxText, setMaxText] = useState(3000);
  const [proposals, setProposals] = useState<RuleProposal[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // an approve/reject/review in flight

  const load = useCallback(async () => {
    try {
      const [r, p] = await Promise.all([
        api<{ rules: Rule[]; agents: string[]; max_text: number }>('/api/rules'),
        api<{ proposals: RuleProposal[] }>('/api/rules/proposals'),
      ]);
      setRules(r.rules);
      setAgents(r.agents);
      setMaxText(r.max_text);
      setProposals(p.proposals);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice({ kind: 'ok', text: ok });
      await load();
      onChanged?.();
      return true;
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
      return false;
    }
  };

  const pending = proposals.filter(p => p.status === 'pending');
  const decided = proposals.filter(p => p.status !== 'pending');

  const once = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    if (busy) return;
    setBusy(key);
    try { await act(fn, ok); } finally { setBusy(null); }
  };

  const decide = (p: RuleProposal, verdict: 'approve' | 'reject') =>
    once(`p${p.id}`, () => api(`/api/rules/proposals/${p.id}/${verdict}`, { method: 'POST', body: JSON.stringify({ note: notes[p.id] || '' }) }),
      verdict === 'approve' ? `Approved #${p.id} — every agent follows it from its next message.` : `Rejected #${p.id} (kept in the history).`);

  const review = (r: Rule, how: 'accept' | 'restore') =>
    once(`r${r.slug}`, () => api(`/api/rules/${encodeURIComponent(r.slug)}/${how}`, { method: 'POST' }),
      how === 'accept' ? `“${r.title}” is used again in this version.` : `“${r.title}” is back to the approved version (the changed file is in .trash).`);

  const toggle = (r: Rule) =>
    act(() => api(`/api/rules/${encodeURIComponent(r.slug)}`, { method: 'PUT', body: JSON.stringify({ enabled: !r.enabled }) }),
      r.enabled ? `“${r.title}” switched off.` : `“${r.title}” switched on.`);

  const remove = (r: Rule) => {
    if (!window.confirm(`Delete the rule “${r.title}”? A copy is kept in ~/.myclaw/rules/.trash.`)) return;
    act(() => api(`/api/rules/${encodeURIComponent(r.slug)}`, { method: 'DELETE' }), `“${r.title}” deleted (a copy is in .trash).`);
    if (draft?.slug === r.slug) setDraft(null);
  };

  const save = async () => {
    if (!draft) return;
    const body = { title: draft.title, text: draft.text, scope: draft.scope.length ? draft.scope : ['all'], priority: draft.priority, enabled: draft.enabled };
    const ok = await act(() => draft.isNew
      ? api('/api/rules', { method: 'POST', body: JSON.stringify(body) })
      : api(`/api/rules/${encodeURIComponent(draft.slug)}`, { method: 'PUT', body: JSON.stringify(body) }),
    `Rule “${draft.title}” saved — agents follow it from their next message.`);
    if (ok) setDraft(null);
  };

  const toggleScope = (name: string) => {
    if (!draft) return;
    if (name === 'all') { setDraft({ ...draft, scope: ['all'] }); return; }
    const cur = draft.scope.filter(s => s !== 'all');
    const next = cur.includes(name) ? cur.filter(s => s !== name) : [...cur, name];
    setDraft({ ...draft, scope: next.length ? next : ['all'] });
  };

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <div className="card-head">
            <h3>Proposals from agents {pending.length > 0 && <span className="badge doctor-badge warn">{pending.length} waiting</span>}</h3>
          </div>
          <p className="muted">Agents cannot change rules. They propose; nothing changes until you approve.</p>
          {pending.length === 0 && <div className="muted">No proposals waiting.</div>}
          {pending.map(p => (
            <div key={p.id} className="settings-card rule-proposal">
              <div className="card-head">
                <h3>#{p.id} <span className="badge">{ACTION_LABEL[p.action]}</span> {p.title}</h3>
                <span className="muted small">by @{p.proposed_by} · {when(p.created)}</span>
              </div>
              <p className="pre"><b>Why:</b> {p.reason || '—'}</p>
              {p.stale && <div className="notice">The rule was changed after this proposal was made. The diff below is against the rule as it is now — approving replaces your newer text.</div>}
              <DiffView diff={p.diff || ''} />
              <div className="save-actions">
                <input className="field-input" placeholder="Note (optional)" value={notes[p.id] || ''}
                  onChange={e => setNotes({ ...notes, [p.id]: e.target.value })} />
                <button className="ghost-btn" disabled={!!busy} onClick={() => decide(p, 'reject')}>Reject</button>
                <button className="primary-btn" disabled={!!busy} onClick={() => decide(p, 'approve')}>{busy === `p${p.id}` ? 'Working…' : 'Approve'}</button>
              </div>
            </div>
          ))}
          {decided.length > 0 && (
            <>
              <button className="link-btn" onClick={() => setShowHistory(h => !h)}>
                {showHistory ? 'Hide' : 'Show'} decided proposals ({decided.length})
              </button>
              {showHistory && (
                <div className="muted small">
                  {decided.map(p => (
                    <div key={p.id} className="proposal-row">
                      <span className={`badge proposal-${p.status === 'approved' ? 'applied' : 'rejected'}`}>{p.status}</span>
                      <b>#{p.id} {ACTION_LABEL[p.action]}: {p.title}</b>
                      <span>by @{p.proposed_by} · {p.status} by {p.decided_by} {when(p.decided_at)}{p.note ? ` — ${p.note}` : ''}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <div className="settings-card">
          <div className="card-head">
            <h3>Rules</h3>
            <button className="primary-btn" onClick={() => setDraft({ slug: '', title: '', text: '', scope: ['all'], priority: 'normal', enabled: true, isNew: true })}>+ New rule</button>
          </div>
          <p className="muted">
            Every agent gets the enabled rules that apply to it in its instructions, highest priority first, and must follow them.
            Never put passwords or keys in a rule — rules are sent to the model with every message.
          </p>
        </div>

        {draft && (
          <div className="settings-card">
            <h3>{draft.isNew ? 'New rule' : `Edit “${draft.title}”`}</h3>
            <div className="agent-editor-grid">
              <div className="field" style={{ gridColumn: 'span 2' }}><label className="field-label">Title</label>
                <input className="field-input" value={draft.title} autoFocus placeholder="e.g. Answer in Romanian"
                  onChange={e => setDraft({ ...draft, title: e.target.value })} /></div>
              <div className="field"><label className="field-label">Priority</label>
                <select className="field-input" value={draft.priority} onChange={e => setDraft({ ...draft, priority: e.target.value as Priority })}>
                  <option value="high">high</option><option value="normal">normal</option><option value="low">low</option>
                </select></div>
            </div>
            <div className="field"><label className="field-label">Applies to</label>
              <div className="chip-row">
                <button type="button" className={`chip small-chip ${draft.scope.includes('all') ? 'active' : ''}`} onClick={() => toggleScope('all')}>all agents</button>
                {agents.map(a => (
                  <button type="button" key={a} className={`chip small-chip ${draft.scope.includes(a) ? 'active' : ''}`} onClick={() => toggleScope(a)}>@{a}</button>
                ))}
              </div>
            </div>
            <div className="field"><label className="field-label">Rule (Markdown, {draft.text.length}/{maxText} characters)</label>
              <textarea className="field-input mono" rows={10} value={draft.text} placeholder="What agents must do or not do."
                onChange={e => setDraft({ ...draft, text: e.target.value })} /></div>
            <label className="mini-toggle">
              <input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /> Enabled
            </label>
            <div className="save-actions">
              <button className="ghost-btn" onClick={() => setDraft(null)}>Cancel</button>
              <button className="primary-btn" disabled={!draft.title.trim() || !draft.text.trim() || draft.text.length > maxText} onClick={save}>Save rule</button>
            </div>
          </div>
        )}

        <div className="skill-grid">
          {rules.map(r => (
            <div className={`settings-card skill-card ${r.enabled ? '' : 'rule-off'}`} key={r.slug}>
              <div className="card-head">
                <h3 className="skill-name">{r.title}</h3>
                <span>
                  <span className={`badge ${r.priority === 'high' ? 'doctor-badge warn' : ''}`}>{r.priority}</span>
                  {!r.enabled && <span className="badge">off</span>}
                </span>
              </div>
              <p className="muted small">{scopeText(r.scope)} · by {r.author} · {r.slug}</p>
              {r.state && r.state !== 'ok' && (
                <div className="notice rule-review">
                  <b>Changed outside ZenSynora — review.</b> Agents do not see this rule until you decide.
                  <DiffView diff={r.review_diff || ''} />
                  <div className="row-actions">
                    <button className="primary-btn" disabled={!!busy} onClick={() => review(r, 'accept')}>Keep this version</button>
                    {r.has_approved && <button className="ghost-btn" disabled={!!busy} onClick={() => review(r, 'restore')}>Restore approved version</button>}
                  </div>
                </div>
              )}
              <pre className="skill-body">{r.text}</pre>
              <div className="row-actions">
                <label className="mini-toggle" title="Switched-off rules stay here but agents do not see them">
                  <input type="checkbox" checked={r.enabled} onChange={() => toggle(r)} /> Enabled
                </label>
                <button className="ghost-btn" onClick={() => setDraft({ slug: r.slug, title: r.title, text: r.text, scope: r.scope, priority: r.priority, enabled: r.enabled, isNew: false })}>Edit</button>
                <button className="ghost-btn danger" onClick={() => remove(r)}>Delete</button>
              </div>
            </div>
          ))}
          {rules.length === 0 && <div className="muted">No rules yet. Write one, or ask an agent to propose one.</div>}
        </div>
      </div>
    </div>
  );
};

export default RulesView;
