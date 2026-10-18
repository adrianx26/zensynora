// Change proposals from agents (v0.7.1): approval-gated self-update.
//
// Agents (the medic's self-review job, or any agent you ask) can read the
// program code and propose changes. Nothing changes until you approve; the
// change is then installed by the safe updater as a new release (backup,
// side-by-side install, checks, automatic rollback). Shown on the Doctor page.
import React, { useCallback, useEffect, useState } from 'react';
import { ago, api, type Proposal } from '../api';

const STATUS_LABEL: Record<Proposal['status'], string> = {
  pending: 'waiting for you', approved: 'approved', applying: 'installing…', applied: 'installed',
  failed: 'failed (rolled back)', rejected: 'rejected',
};

function DiffView({ diff }: { diff: string }) {
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

const ProposalsPanel: React.FC = () => {
  const [items, setItems] = useState<Proposal[]>([]);
  const [open, setOpen] = useState<Proposal | null>(null);
  const [lessons, setLessons] = useState<string[]>([]);
  const [problems, setProblems] = useState<{
    errors: { message: string; count: number; level?: string }[];
    job_problems: { job: string; status: string }[];
    repeated_warnings?: { message: string; count: number }[];
    restarts?: { starts: number; max_in_15_min: number; crash_loop: boolean };
    risky_custom_tools?: { name: string; problems: string[] }[];
  } | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, l, pr] = await Promise.all([
        api<{ proposals: Proposal[] }>('/api/medic/proposals'),
        api<{ lessons: string[] }>('/api/medic/lessons'),
        api<typeof problems>('/api/medic/problems'),
      ]);
      setItems(p.proposals);
      setLessons(l.lessons);
      setProblems(pr);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data load
  useEffect(() => { load(); }, [load]);
  const applying = items.some(p => p.status === 'applying');
  useEffect(() => {
    const iv = setInterval(async () => {
      await load();
      if (open) {
        try { setOpen(await api<Proposal>(`/api/medic/proposals/${open.id}`)); } catch { /* deleted */ }
      }
    }, applying ? 3000 : 20000);
    return () => clearInterval(iv);
  }, [applying, open, load]);

  const show = async (id: string) => {
    try { setOpen(await api<Proposal>(`/api/medic/proposals/${id}`)); } catch (e) { setNotice({ kind: 'error', text: (e as Error).message }); }
  };

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setNotice(null);
    try {
      await fn();
      setNotice({ kind: 'ok', text: ok });
      await load();
      if (open) await show(open.id);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const approve = (p: Proposal) => {
    if (!window.confirm(`Install “${p.title}”?\n\nThe safe updater backs up the program and your data, installs the change as a new version next to the current one, checks it and switches only if everything works. Undo later with: zensynora update rollback`)) return;
    act(() => api(`/api/medic/proposals/${p.id}/approve`, { method: 'POST', body: JSON.stringify({ apply: true }) }), 'Approved — installing in the background (a few minutes).');
  };

  // Recent = newest 10 plus anything still needing attention.
  const visible = showAll ? items : items.filter((p, i) => ['pending', 'approved', 'applying', 'failed'].includes(p.status) || i < 10);

  return (
    <>
      <div className="settings-card">
        <div className="card-head">
          <h3>Change proposals {items.filter(p => p.status === 'pending').length > 0 && <span className="badge doctor-badge warn">{items.filter(p => p.status === 'pending').length} waiting</span>}</h3>
          <button className="ghost-btn" onClick={() => act(() => api('/api/medic/self-review', { method: 'POST' }), 'Self-review started — see Jobs → “Medic self-review”. New proposals appear here.')}>🩺 Run self-review</button>
        </div>
        <p className="muted">Agents can read ZenSynora's code and propose fixes. Nothing changes until you approve; then the change is installed like an update — backed up, checked, and rolled back automatically if anything fails.</p>
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}
        {problems && (() => {
          const warns = problems.repeated_warnings ?? [];
          const tools = problems.risky_custom_tools ?? [];
          const rs = problems.restarts;
          const parts: React.ReactNode[] = [];
          if (rs?.crash_loop) parts.push(<>a crash loop ({rs.starts} restarts, up to {rs.max_in_15_min} in 15 min)</>);
          if (problems.errors.length) parts.push(<>{problems.errors.length} kind(s) of error (most frequent: “{problems.errors[0].message.slice(0, 120)}” ×{problems.errors[0].count})</>);
          if (warns.length) parts.push(<>{warns.length} warning(s) that keep repeating (“{warns[0].message.slice(0, 80)}” ×{warns[0].count})</>);
          if (problems.job_problems.length) parts.push(<>{problems.job_problems.length} failed or low-scoring job run(s)</>);
          if (tools.length) parts.push(<>custom tool(s) with placeholder or hard-coded credentials: {tools.map(t => t.name).join(', ')}</>);
          if (!parts.length) return null;
          return (
            <div className="notice">
              <b>What the medic would look at (last 24 h):</b>{' '}
              {parts.map((p, i) => <React.Fragment key={i}>{i > 0 && '; '}{p}</React.Fragment>)}. Details: Doctor → Report.
            </div>
          );
        })()}
        {visible.length === 0 && <div className="muted">No proposals. Run a self-review, or ask an agent in chat to “propose a fix” for something.</div>}
        {visible.map(p => (
          <div key={p.id} className={`proposal-row ${open?.id === p.id ? 'selected' : ''}`} onClick={() => show(p.id)}>
            <span className={`badge proposal-${p.status}`}>{STATUS_LABEL[p.status]}</span>
            <b>{p.title}</b>
            <span className="muted small">by @{p.author} · {ago(p.created)} · {p.risk} risk</span>
          </div>
        ))}
        {items.length > visible.length && <button className="link-btn" onClick={() => setShowAll(true)}>Show older ({items.length - visible.length})</button>}
      </div>

      {open && (
        <div className="settings-card">
          <div className="card-head">
            <h3>{open.title}</h3>
            <div className="row-actions">
              {open.status === 'pending' && <>
                <button className="primary-btn" onClick={() => approve(open)}>Approve & install</button>
                <button className="ghost-btn" onClick={() => act(() => api(`/api/medic/proposals/${open.id}/reject`, { method: 'POST', body: JSON.stringify({ note: '' }) }), 'Rejected.')}>Reject</button>
              </>}
              {open.status === 'failed' && <button className="ghost-btn" onClick={() => act(() => api(`/api/medic/proposals/${open.id}/apply`, { method: 'POST' }), 'Retrying…')}>Retry install</button>}
              {open.status !== 'applying' && <button className="ghost-btn danger" onClick={() => act(async () => { await api(`/api/medic/proposals/${open.id}`, { method: 'DELETE' }); setOpen(null); }, 'Deleted.')}>Delete</button>}
              <button className="icon-btn" onClick={() => setOpen(null)}>×</button>
            </div>
          </div>
          <p className="muted small">by @{open.author} · {new Date(open.created * 1000).toLocaleString()} · {open.risk} risk · {Object.keys(open.files || {}).length} file(s): {Object.keys(open.files || {}).join(', ')}</p>
          <p className="pre">{open.reason}</p>
          {open.result && <div className={`notice ${open.result.ok ? '' : 'error'}`}>{open.result.note}<pre className="small">{open.result.log_tail.slice(-1500)}</pre></div>}
          {open.diff && <DiffView diff={open.diff} />}
          {open.history && (
            <div className="muted small">{open.history.map((h, i) => <div key={i}>{new Date(h.at * 1000).toLocaleString()} — {h.action} by {h.by}{h.note ? `: ${h.note}` : ''}</div>)}</div>
          )}
        </div>
      )}

      <div className="settings-card">
        <h3>What the medic learned</h3>
        <p className="muted">Lessons saved by agents with <code>record_lesson</code> (knowledge note <code>medic-lessons</code>, searchable by every agent).</p>
        {lessons.length === 0 && <div className="muted">Nothing yet.</div>}
        <ul className="lesson-list">{lessons.slice(0, 30).map((l, i) => <li key={i}>{l}</li>)}</ul>
      </div>
    </>
  );
};

export default ProposalsPanel;
