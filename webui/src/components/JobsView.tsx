// Jobs page (v0.7.1): add, monitor, modify, run, evaluate and benchmark jobs.
//
// A job is a prompt an agent runs on a schedule or on demand. Every execution
// is a run with output, duration and model; a run can be scored 0-10 by the
// main LLM against the job's success criteria, and a benchmark runs the same
// prompt on several models side by side.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAutoClear } from '../notice';
import {
  ago, api, fmtTime, type AgentInfo, type Channel, type Diagnosis, type Job, type JobRun, type ModelChoice, type ModelsOverview,
} from '../api';

interface Props {
  agents: AgentInfo[];
  channels: Channel[];
}

type Draft = Partial<Job> & { name: string; prompt: string };
type JobsLayout = 'compact' | 'table';
interface JobAIAnswer {
  answer: string;
  suggested_agent: string;
  reason: string;
  confidence: number;
  proposed_job_changes?: Partial<Job>;
  by?: string;
}
interface AITarget { jobId: string; runId?: number }

const EMPTY: Draft = {
  name: '', prompt: '', agent: 'default', schedule: 'manual', every_minutes: 60, at_time: '09:00', days: 'mon,tue,wed,thu,fri',
  enabled: true, channel_id: '', success_criteria: '', auto_evaluate: false, fresh_context: true, timeout_s: 300, retries: 0,
  on_failure: '', max_steps: 8, context_runs: 0, insight_notes: false, insight_profile: false,
  doctor_managed: false, doctor_recovery: false, doctor_auto_workflow: false,
  recovery_attempts: 3, recovery_timeout_s: 1800,
};

// Asked when you click the 💡 insights link on a compact tile — a focused AI Ask question.
const INSIGHT_QUESTION = 'What did the recent runs of this job teach us? Which insights are worth proposing ' +
  'as a knowledge-base note or a profile update?';

/** Tooltip for the 💡 insights link and the enabled destinations. */
function insightTitle(j: Partial<Job>): string {
  const ask = 'Click to ask AI what the recent runs taught us.';
  if (!j.insight_notes && !j.insight_profile) return `Job insights are off (opt in via Edit). ${ask}`;
  const notes = j.insight_notes ? 'Knowledge-base notes are saved automatically. ' : '';
  const profile = j.insight_profile ? 'Profile proposals require your review. ' : '';
  return `${notes}${profile}${ask}`;
}

const TEMPLATES: { label: string; job: Draft }[] = [
  {
    label: 'Morning briefing',
    job: { ...EMPTY, name: 'Morning briefing', schedule: 'daily', at_time: '08:00', channel_id: 'general',
      prompt: 'Summarise what is new in my knowledge base since yesterday and list my open tasks. Keep it under 150 words.',
      success_criteria: 'Short, mentions new notes and open tasks, no invented items.', auto_evaluate: true },
  },
  {
    label: 'Weekly knowledge clean-up',
    job: { ...EMPTY, name: 'Weekly knowledge clean-up', schedule: 'weekly', days: 'sun', at_time: '18:00',
      prompt: 'Look for duplicate or outdated notes in the knowledge base and report which ones to merge or delete. Do not delete anything yourself.' },
  },
  {
    label: 'Medic self-review',
    job: { ...EMPTY, name: 'Medic self-review', schedule: 'daily', at_time: '03:00', timeout_s: 900, auto_evaluate: true,
      prompt: 'Run the medic self-review: call get_recent_problems, pick at most one problem caused by ZenSynora\'s own code, read the code with read_source_file, propose a small fix with propose_code_change if you are confident (I approve it in Doctor → Change proposals), and record_lesson what you learned. Reply with a short report.',
      success_criteria: 'Names one concrete problem with its cause, and either files a proposal or explains why not. Records a lesson.' },
  },
];

const STATUS_ICON: Record<string, string> = { ok: '✅', error: '❌', timeout: '⏱', cancelled: '⏹', running: '⏳', interrupted: '⚠️' };

function scheduleText(j: Partial<Job>): string {
  switch (j.schedule) {
    case 'interval': return `every ${j.every_minutes} min`;
    case 'daily': return `daily at ${j.at_time}`;
    case 'weekly': return `${(j.days || '').split(',').filter(Boolean).join(', ')} at ${j.at_time}`;
    case 'once': return j.run_at ? `once, ${fmtTime(j.run_at)}` : 'once';
    default: return 'manual';
  }
}

const FIELD_LABEL: Record<string, string> = {
  prompt: 'Instruction', agent: 'Agent', timeout_s: 'Timeout (s)', retries: 'Retries', max_steps: 'Tool steps',
  success_criteria: 'Success criteria', fresh_context: 'Clean memory',
};
const fmtVal = (v: unknown) => (typeof v === 'boolean' ? (v ? 'yes' : 'no') : v == null || v === '' ? '—' : String(v));

/** The medic's diagnosis of a failed run, with the fix buttons (v0.7.5). */
function DiagnosisPanel({ d, busy, onFix, onCode }: {
  d: Diagnosis; busy: boolean; onFix: (runAgain: boolean) => void; onCode: () => void;
}) {
  const changes = Object.entries(d.job_changes || {});
  return (
    <div className="medic-panel">
      <div className="medic-head">🩺 <b>{d.cause}</b> <span className="badge">{d.category.replace('_', ' ')}</span>
        <span className="muted small"> · {d.by === 'rules' ? 'known problem' : `by ${d.by}`} · confidence {Math.round(d.confidence * 100)}%</span></div>
      {d.explanation && <p>{d.explanation}</p>}
      {d.note && <p className="muted small">{d.note}</p>}
      {changes.length > 0 && (
        <table className="data-table">
          <thead><tr><th>Change</th><th>Now</th><th>Medic suggests</th></tr></thead>
          <tbody>{changes.map(([k, v]) => (
            <tr key={k}><td>{FIELD_LABEL[k] || k}</td><td className="pre small">{fmtVal(d.current?.[k])}</td><td className="pre small"><b>{fmtVal(v)}</b></td></tr>
          ))}</tbody>
        </table>
      )}
      {d.manual_steps?.length > 0 && (
        <div><b>You need to:</b><ul>{d.manual_steps.map((s, i) => <li key={i}>{s}</li>)}</ul></div>
      )}
      <div className="row-actions">
        {d.applied
          ? <span className="muted">✔ Fix applied{d.rerun_id ? ` — run #${d.rerun_id}` : ''}.</span>
          : changes.length > 0 && <>
            <button className="primary-btn" disabled={busy} onClick={() => onFix(true)}>Apply fix &amp; run again</button>
            <button className="ghost-btn" disabled={busy} onClick={() => onFix(false)}>Apply only</button>
          </>}
        {(d.code_fix_needed || d.category === 'code' || d.category === 'unknown') && (
          <button className="ghost-btn" disabled={busy} onClick={onCode}
            title="The medic reads ZenSynora's code and files a change proposal; you approve it on the Doctor page">
            🛠 Ask the medic to fix the code</button>
        )}
      </div>
    </div>
  );
}

function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="muted">—</span>;
  const cls = score >= 7 ? 'ok' : score >= 4 ? 'warn' : 'err';
  return <span className={`score ${cls}`}>{score.toFixed(score % 1 ? 1 : 0)}/10</span>;
}

const JobsView: React.FC<Props> = ({ agents, channels }) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [runs, setRuns] = useState<JobRun[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  useAutoClear(notice, setNotice);  // 2026-11-08: success messages go away within 3 s
  const [openRun, setOpenRun] = useState<number | null>(null);
  const [models, setModels] = useState<ModelsOverview | null>(null);
  const [bench, setBench] = useState<ModelChoice[]>([]);
  const [benchInput, setBenchInput] = useState<ModelChoice>({ provider: '', model: '' });
  const [tab, setTab] = useState<'runs' | 'bench'>('runs');
  const [medicBusy, setMedicBusy] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null); // job id waiting for “Delete?” (2026-10-27)
  const [layout, setLayout] = useState<JobsLayout>('compact');
  const [aiTarget, setAITarget] = useState<AITarget | null>(null);
  const [aiQuestion, setAIQuestion] = useState('');
  const [aiAnswer, setAIAnswer] = useState<JobAIAnswer | null>(null);
  const [aiBusy, setAIBusy] = useState(false);
  const [doctorBusy, setDoctorBusy] = useState(false);
  const [audit, setAudit] = useState<{ jobId: string; text: string } | null>(null);
  const aiRequest = useRef(0);

  const loadJobs = useCallback(async () => {
    try {
      const res = await api<{ jobs: Job[] }>('/api/jobs');
      setJobs(res.jobs);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  const loadRuns = useCallback(async (id: string) => {
    try {
      const res = await api<{ runs: JobRun[] }>(`/api/jobs/${id}/runs?limit=50`);
      setRuns(res.runs);
    } catch { /* job may have been deleted */ }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- data loading on mount / selection */
  useEffect(() => {
    loadJobs();
    api<ModelsOverview>('/api/models').then(m => {
      setModels(m);
      setBenchInput(m.default);
    }).catch(() => { /* models page shows the error */ });
  }, [loadJobs]);

  // Poll while something runs, slower otherwise.
  const anyRunning = jobs.some(j => j.running) || runs.some(r => r.status === 'running');
  useEffect(() => {
    const iv = setInterval(() => {
      loadJobs();
      if (selected) loadRuns(selected);
    }, anyRunning ? 2500 : 15000);
    return () => clearInterval(iv);
  }, [anyRunning, selected, loadJobs, loadRuns]);

  useEffect(() => { if (selected) loadRuns(selected); }, [selected, loadRuns]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const job = jobs.find(j => j.id === selected) || null;

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice({ kind: 'ok', text: ok });
      await loadJobs();
      if (selected) await loadRuns(selected);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const save = () => draft && act(async () => {
    const body = { ...draft };
    if (body.schedule !== 'once') delete body.run_at;
    const saved = editingId
      ? await api<Job>(`/api/jobs/${editingId}`, { method: 'PATCH', body: JSON.stringify(body) })
      : await api<Job>('/api/jobs', { method: 'POST', body: JSON.stringify(body) });
    setDraft(null);
    setEditingId(null);
    setSelected(saved.id);
  }, editingId ? 'Job saved.' : 'Job created.');

  const runNow = (id: string) => act(() => api(`/api/jobs/${id}/run`, { method: 'POST' }), 'Started — the result appears below.');
  const doctorAction = async (path: string, body: object = {}) => {
    setDoctorBusy(true);
    try {
      await act(async () => {
        const result = await api<{ status?: string; run_id?: number; uid?: string }>(path, { method: 'POST', body: JSON.stringify(body) });
        if (result.status === 'already_completed') setNotice({ kind: 'ok',
          text: `Already recovered by run #${result.run_id} (${result.uid}); no actions repeated.` });
      });
    } finally {
      setDoctorBusy(false);
    }
  };
  const toggle = (j: Job) => act(() => api(`/api/jobs/${j.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !j.enabled }) }));
  // 2026-10-27: delete from the list too, with an inline confirmation instead of a browser dialog.
  const remove = (j: Job) => act(async () => {
    await api(`/api/jobs/${j.id}`, { method: 'DELETE' });
    setConfirmDelete(null);
    if (selected === j.id) setSelected(null);
    if (editingId === j.id) { setDraft(null); setEditingId(null); }
  }, `Job “${j.name}” deleted.`);
  const deleteButtons = (j: Job) => confirmDelete === j.id ? (
    <span className="inline-confirm" role="alertdialog" aria-label={`Delete job ${j.name}`}>
      <span className="muted">Delete it and its run history?</span>
      <button className="ghost-btn danger" onClick={() => remove(j)}>Delete</button>
      <button className="ghost-btn" onClick={() => setConfirmDelete(null)}>Cancel</button>
    </span>
  ) : (
    <button className="ghost-btn danger" disabled={j.running} onClick={() => setConfirmDelete(j.id)}
      title={j.running ? 'The job is running — wait for it to finish (or cancel the run) before deleting it' : 'Delete this job and its run history'}>🗑 Delete</button>
  );
  const askAI = async (jobId: string, runId?: number, question = '') => {
    const request = ++aiRequest.current;
    setAITarget({ jobId, runId });
    setSelected(jobId);
    setAIQuestion(question);
    setAIAnswer(null);
    setAIBusy(true);
    setNotice(null);
    try {
      const answer = await api<JobAIAnswer>(`/api/jobs/${jobId}/ai-ask`, {
        method: 'POST',
        body: JSON.stringify({ ...(runId ? { run_id: runId } : {}), question }),
      });
      if (request === aiRequest.current) setAIAnswer(answer);
    } catch (e) {
      if (request === aiRequest.current) {
        setNotice({ kind: 'error', text: (e as Error).message });
      }
    } finally {
      if (request === aiRequest.current) setAIBusy(false);
    }
  };
  const applySuggestedAgent = () => aiTarget && aiAnswer && agents.some(a => a.name === aiAnswer.suggested_agent) &&
    act(() => api(`/api/jobs/${aiTarget.jobId}`, {
      method: 'PATCH',
      body: JSON.stringify({ agent: aiAnswer.suggested_agent, doctor_managed: false, team_agents: [] }),
    }), `Job now uses @${aiAnswer.suggested_agent}.`);
  const evaluate = (r: JobRun) => act(() => api(`/api/jobs/runs/${r.id}/evaluate`, { method: 'POST', body: '{}' }), 'Evaluated with the main model.');
  const askMedic = async (r: JobRun) => {
    setOpenRun(r.id);
    setMedicBusy(r.id);
    await act(() => api(`/api/jobs/runs/${r.id}/diagnose`, { method: 'POST' }));
    setMedicBusy(null);
  };
  const applyFix = async (r: JobRun, runAgain: boolean) => {
    setMedicBusy(r.id);
    await act(() => api(`/api/jobs/runs/${r.id}/fix`, { method: 'POST', body: JSON.stringify({ run_again: runAgain }) }),
      runAgain ? 'Fix applied — the job is running again.' : 'Fix applied to the job.');
    setMedicBusy(null);
  };
  const codeFix = (r: JobRun) => act(async () => {
    const res = await api<{ name: string }>(`/api/jobs/runs/${r.id}/code-fix`, { method: 'POST' });
    return res;
  }, 'The medic is looking at the code (job “Medic code fix”). A fix appears in Doctor → Change proposals for you to approve.');
  const cancel = (r: JobRun) => act(() => api(`/api/jobs/runs/${r.id}/cancel`, { method: 'POST' }));
  const exportInvestigation = (r: JobRun) => act(async () => {
    const report = await api<{ text: string }>(`/api/doctor/ai/investigations/${r.id}/report`);
    const url = URL.createObjectURL(new Blob([report.text], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `medic-investigation-${r.id}.md`;
    link.click();
    URL.revokeObjectURL(url);
  }, 'Downloaded a privacy-filtered action journal. Review and complete it before posting to GitHub; nothing was uploaded.');
  const runBench = () => job && act(() => api(`/api/jobs/${job.id}/benchmark`, { method: 'POST', body: JSON.stringify({ models: bench, evaluate: true }) }),
    `Benchmark started on ${bench.length} model(s).`);

  const benchRuns = useMemo(() => runs.filter(r => r.kind === 'bench'), [runs]);
  const normalRuns = useMemo(() => runs.filter(r => r.kind === 'run' || r.kind === 'step'), [runs]);

  // ── editor ────────────────────────────────────────────────────────────────
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft(d => (d ? { ...d, [k]: v } : d));

  const editor = draft && (
    <div className="settings-card">
      <h3>{editingId ? `Edit “${draft.name}”` : 'New job'}</h3>
      {!editingId && (
        <div className="chip-row">
          <span className="muted">Start from:</span>
          {TEMPLATES.map(t => <button key={t.label} className="chip" onClick={() => setDraft({ ...t.job })}>{t.label}</button>)}
        </div>
      )}
      <div className="agent-editor-grid">
        <div className="field"><label className="field-label">Name</label>
          <input className="field-input" value={draft.name} onChange={e => set('name', e.target.value)} /></div>
        <div className="field"><label className="field-label">Agent</label>
          <select className="field-input" value={draft.agent || ''} disabled={draft.doctor_managed}
            onChange={e => set('agent', e.target.value)}>
            {!draft.agent && <option value="">Unassigned — Doctor will analyze</option>}
            {agents.map(a => <option key={a.name} value={a.name}>@{a.name}</option>)}
          </select></div>
        <div className="field"><label className="field-label">Post results in</label>
          <select className="field-input" value={draft.channel_id} onChange={e => set('channel_id', e.target.value)}>
            <option value="">nowhere (history only)</option>
            {channels.map(c => <option key={c.id} value={c.id}>{c.kind === 'dm' ? '@' : '#'}{c.kind === 'dm' ? c.members[0] : c.name}</option>)}
          </select></div>
      </div>
      <div className="field field-inline">
        <label className="field-label">Let Doctor assign this job</label>
        <label className="switch"><input type="checkbox" checked={!!draft.doctor_managed}
          onChange={e => setDraft(d => d && ({ ...d, doctor_managed: e.target.checked, agent: e.target.checked ? '' : 'default' }))} />
          <span className="switch-track"><span className="switch-thumb" /></span></label>
      </div>
      {draft.doctor_managed && <p className="muted small">Created without an agent. Enable automatic Doctor planning in Settings → Features,
        or use Analyze / reassign below. Teams and subtasks require approval unless you explicitly allow automatic workflows.</p>}
      <div className="field"><label className="field-label">What the agent should do</label>
        <textarea className="field-input" rows={4} value={draft.prompt} onChange={e => set('prompt', e.target.value)} /></div>
      <div className="agent-editor-grid">
        <div className="field"><label className="field-label">Schedule</label>
          <select className="field-input" value={draft.schedule} onChange={e => set('schedule', e.target.value as Job['schedule'])}>
            <option value="manual">Manual (Run now)</option>
            <option value="once">Once</option>
            <option value="interval">Every N minutes</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select></div>
        {draft.schedule === 'interval' && (
          <div className="field"><label className="field-label">Every (minutes)</label>
            <input className="field-input narrow" type="number" min={1} value={draft.every_minutes} onChange={e => set('every_minutes', Number(e.target.value))} /></div>
        )}
        {(draft.schedule === 'daily' || draft.schedule === 'weekly') && (
          <div className="field"><label className="field-label">At (HH:MM)</label>
            <input className="field-input narrow" type="time" value={draft.at_time} onChange={e => set('at_time', e.target.value)} /></div>
        )}
        {draft.schedule === 'once' && (
          <div className="field"><label className="field-label">When</label>
            <input className="field-input" type="datetime-local"
              value={draft.run_at ? new Date(draft.run_at * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''}
              onChange={e => set('run_at', e.target.value ? new Date(e.target.value).getTime() / 1000 : null)} /></div>
        )}
      </div>
      {draft.schedule === 'weekly' && (
        <div className="chip-row">
          {['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(d => {
            const days = (draft.days || '').split(',').filter(Boolean);
            const on = days.includes(d);
            return <button key={d} className={`chip ${on ? 'active' : ''}`}
              onClick={() => set('days', (on ? days.filter(x => x !== d) : [...days, d]).join(','))}>{d}</button>;
          })}
        </div>
      )}
      <div className="field"><label className="field-label">Success criteria (used to score runs)</label>
        <input className="field-input" value={draft.success_criteria} placeholder="e.g. lists every open task, under 200 words"
          onChange={e => set('success_criteria', e.target.value)} /></div>
      <div className="agent-editor-grid">
        <div className="field"><label className="field-label">Timeout (s)</label>
          <input className="field-input narrow" type="number" value={draft.timeout_s} onChange={e => set('timeout_s', Number(e.target.value))} /></div>
        <div className="field"><label className="field-label">Retries on failure</label>
          <input className="field-input narrow" type="number" min={0} max={5} value={draft.retries} onChange={e => set('retries', Number(e.target.value))} /></div>
        <div className="field"><label className="field-label" title="How many extra tool rounds (search, read, retry…) the agent may use per run">Tool steps</label>
          <input className="field-input narrow" type="number" min={1} max={30} value={draft.max_steps ?? 8} onChange={e => set('max_steps', Number(e.target.value))} /></div>
        <div className="field"><label className="field-label" title="How many previous finished runs the agent sees as background context (0 = none)">Prior runs context</label>
          <input className="field-input narrow" type="number" min={0} max={20} value={draft.context_runs ?? 0} onChange={e => set('context_runs', Number(e.target.value))} /></div>
        <div className="field"><label className="field-label">When a run fails</label>
          <select className="field-input" value={draft.on_failure ?? ''} onChange={e => set('on_failure', e.target.value as Job['on_failure'])}>
            <option value="">do nothing</option>
            <option value="diagnose">the medic diagnoses it</option>
            <option value="fix">the medic diagnoses, fixes the job and runs it again</option>
          </select></div>
      </div>
      {([['enabled', 'Enabled'], ['auto_evaluate', 'Score every run with the main model'], ['fresh_context', 'Start each run with a clean memory'],
        ['insight_notes', 'Save insight notes to the knowledge base'],
        ['doctor_recovery', 'Doctor / Medic assisted recovery on failure'],
        ['doctor_auto_workflow', 'Allow Doctor teams and subtask workflows without another approval'],
        ['insight_profile', 'Propose profile updates from results']] as const).map(([k, label]) => (
        <div className="field field-inline" key={k}>
          <label className="field-label">{label}</label>
          <label className="switch"><input type="checkbox" checked={!!draft[k]} onChange={e => set(k, e.target.checked)} />
            <span className="switch-track"><span className="switch-thumb" /></span></label>
        </div>
      ))}
      {draft.doctor_recovery && <div className="agent-editor-grid">
        <div className="field"><label className="field-label">Doctor recovery attempts</label>
          <input className="field-input" type="number" min={1} value={draft.recovery_attempts ?? 3}
            onChange={e => set('recovery_attempts', Number(e.target.value))} /></div>
        <div className="field"><label className="field-label">Total recovery time (seconds)</label>
          <input className="field-input" type="number" min={10} value={draft.recovery_timeout_s ?? 1800}
            onChange={e => set('recovery_timeout_s', Number(e.target.value))} /></div>
      </div>}
      {(draft.insight_notes || draft.insight_profile) && (
        <p className="insight-hint">Insight notes are saved automatically when enabled.
          Profile changes are proposed for your review, not applied automatically.</p>
      )}
      <div className="save-actions">
        <button className="ghost-btn" onClick={() => { setDraft(null); setEditingId(null); }}>Cancel</button>
        <button className="primary-btn" onClick={save} disabled={!draft.name.trim() || !draft.prompt.trim()}>{editingId ? 'Save job' : 'Create job'}</button>
      </div>
    </div>
  );

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <div className="card-head">
            <h3>Jobs</h3>
            <div className="row-actions">
              <div className="view-toggle" role="group" aria-label="Jobs layout">
                <button className={layout === 'compact' ? 'active' : ''} aria-pressed={layout === 'compact'}
                  onClick={() => setLayout('compact')} title="Compact grid">▦ <span>Compact</span></button>
                <button className={layout === 'table' ? 'active' : ''} aria-pressed={layout === 'table'}
                  onClick={() => setLayout('table')} title="Table view">☷ <span>Table</span></button>
              </div>
              <button className="primary-btn" onClick={() => { setDraft({ ...EMPTY }); setEditingId(null); }}>+ New job</button>
            </div>
          </div>
          <p className="muted">Tasks your agents run on a schedule or when you click Run. Scheduled jobs run while <code>zensynora webui</code> is running.</p>
          {jobs.length === 0 && <div className="muted">No jobs yet — create one or start from a template.</div>}
          {jobs.length > 0 && layout === 'compact' && (
            <div className="jobs-grid" data-view="compact">
              {jobs.map(j => (
                <article key={j.id} className={`job-tile ${selected === j.id ? 'selected' : ''}`}
                  onClick={() => setSelected(j.id)}>
                  <div className="job-tile-head">
                    <div>
                      <b title={j.name}>{j.name}</b>
                      <div className="job-tile-sub">{j.agent ? `@${j.agent}` : `Doctor: ${j.planning_state || 'pending'}`} · {scheduleText(j)}</div>
                    </div>
                    <span className="job-tile-status" title={j.running ? 'Running' : j.last_status || 'Not run'}>
                      {j.running ? '⏳' : STATUS_ICON[j.last_status] || '○'}
                    </span>
                  </div>
                  <div className="job-tile-meta">
                    {!j.enabled && <span className="badge">paused</span>}
                    {j.stats?.success_rate != null && <span>{Math.round(j.stats.success_rate * 100)}% success</span>}
                    <ScoreBadge score={j.stats?.avg_score} />
                    <button className={`insight-link ${j.insight_notes || j.insight_profile ? 'on' : ''}`}
                      title={insightTitle(j)} aria-label={`Insights about ${j.name}`}
                      onClick={e => { e.stopPropagation(); void askAI(j.id, undefined, INSIGHT_QUESTION); }}>
                      💡 insights</button>
                  </div>
                  <div className="job-tile-actions row-actions" onClick={e => e.stopPropagation()}>
                    <button className="icon-btn" disabled={j.running || j.planning_state === 'needs_approval'} onClick={() => runNow(j.id)}
                      aria-label={`Run ${j.name}`} title="Run now">▶</button>
                    <button className="icon-btn" onClick={() => toggle(j)}
                      aria-label={`${j.enabled ? 'Pause' : 'Resume'} ${j.name}`} title={j.enabled ? 'Pause' : 'Resume'}>
                      {j.enabled ? '⏸' : '▶'}
                    </button>
                    <button className="icon-btn" onClick={() => { setDraft({ ...j }); setEditingId(j.id); }}
                      aria-label={`Edit ${j.name}`} title="Edit">✎</button>
                    <button className="icon-btn danger" disabled={j.running} onClick={() => setConfirmDelete(j.id)}
                      aria-label={`Delete ${j.name}`} title="Delete">🗑</button>
                    <button className="icon-btn ai" onClick={() => void askAI(j.id)}
                      aria-label={`AI Ask about ${j.name}`} title="AI Ask">✦</button>
                    {confirmDelete === j.id && (
                      <span className="inline-confirm" role="alertdialog" aria-label={`Delete job ${j.name}`}>
                        <button className="ghost-btn danger" onClick={() => remove(j)}>Delete?</button>
                        <button className="ghost-btn" onClick={() => setConfirmDelete(null)}>Cancel</button>
                      </span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          {jobs.length > 0 && layout === 'table' && (
            <table className="data-table">
              <thead><tr><th>Job</th><th>Agent</th><th>Schedule</th><th>Last run</th><th>Next run</th><th>Success</th><th>Score</th><th /></tr></thead>
              <tbody>
                {jobs.map(j => (
                  <tr key={j.id} className={selected === j.id ? 'selected' : ''} onClick={() => setSelected(j.id)}>
                    <td><b>{j.name}</b>{!j.enabled && <span className="badge">paused</span>}{j.created_by !== 'you' && <span className="badge">by {j.created_by}</span>}</td>
                    <td>{j.agent ? `@${j.agent}` : `Doctor: ${j.planning_state || 'pending'}`}</td>
                    <td>{scheduleText(j)}</td>
                    <td>{j.running ? '⏳ running' : <>{STATUS_ICON[j.last_status] || ''} {ago(j.last_run)}</>}</td>
                    <td>{j.enabled && j.next_run ? ago(j.next_run) : '—'}</td>
                    <td>{j.stats?.success_rate != null ? `${Math.round(j.stats.success_rate * 100)}% of ${j.stats.runs}` : '—'}</td>
                    <td><ScoreBadge score={j.stats?.avg_score} /></td>
                    <td className="row-actions" onClick={e => e.stopPropagation()}>
                      <button className="ghost-btn" disabled={j.running || j.planning_state === 'needs_approval'} onClick={() => runNow(j.id)}>▶ Run</button>
                      <button className="ghost-btn" onClick={() => toggle(j)}>{j.enabled ? 'Pause' : 'Resume'}</button>
                      <button className="ghost-btn" onClick={() => { setDraft({ ...j }); setEditingId(j.id); }}>Edit</button>
                      <button className="ghost-btn" onClick={() => void askAI(j.id)} title="Ask AI about this job">✦ AI Ask</button>
                      {deleteButtons(j)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {aiTarget && (
          <div className="settings-card job-ai-panel">
            <div className="card-head">
              <h3>✦ AI Ask{aiTarget.runId ? ` · run #${aiTarget.runId}` : ` · ${jobs.find(j => j.id === aiTarget.jobId)?.name || 'job'}`}</h3>
              <button className="icon-btn" onClick={() => {
                aiRequest.current += 1;
                setAITarget(null); setAIAnswer(null); setAIBusy(false);
              }}
                aria-label="Close AI answer" title="Close">×</button>
            </div>
            <div className="field-row">
              <input className="field-input" value={aiQuestion} maxLength={2000}
                placeholder="Optional: ask about the job, latest run, or agent choice…"
                onChange={e => setAIQuestion(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && aiTarget) void askAI(aiTarget.jobId, aiTarget.runId, aiQuestion);
                }} />
              <button className="primary-btn" disabled={aiBusy}
                onClick={() => void askAI(aiTarget.jobId, aiTarget.runId, aiQuestion)}>
                {aiBusy ? 'Asking…' : 'Ask AI'}
              </button>
            </div>
            {aiAnswer && (
              <div className="job-ai-answer">
                <p>{aiAnswer.answer}</p>
                <div className="job-ai-recommendation">
                  <strong>@{aiAnswer.suggested_agent}</strong>
                  <span>{aiAnswer.reason}</span>
                  <span className="muted small">{Math.round(aiAnswer.confidence * 100)}% confidence
                    {aiAnswer.by ? ` · ${aiAnswer.by}` : ''}</span>
                </div>
                {Object.entries(aiAnswer.proposed_job_changes || {}).length > 0 && (
                  <div className="job-ai-changes">
                    <b>Suggested job changes (not applied)</b>
                    {Object.entries(aiAnswer.proposed_job_changes || {}).map(([key, value]) => (
                      <div key={key}><span>{FIELD_LABEL[key] || key}</span><span className="pre">{fmtVal(value)}</span></div>
                    ))}
                  </div>
                )}
                <div className="row-actions">
                  <button className="primary-btn"
                    disabled={!agents.some(a => a.name === aiAnswer.suggested_agent)}
                    onClick={() => void applySuggestedAgent()}>
                    Use @{aiAnswer.suggested_agent}
                  </button>
                  <span className="muted small">Nothing is changed until you click this button.</span>
                </div>
              </div>
            )}
          </div>
        )}

        {editor}

        {job && !draft && (
          <div className="settings-card">
            <div className="card-head">
              <h3>{job.name}</h3>
              <div className="row-actions">
                <button className="ghost-btn" disabled={job.running || doctorBusy || job.planning_state === 'needs_approval'}
                  onClick={() => runNow(job.id)}>▶ Run now</button>
                {job.running && <button className="ghost-btn" onClick={() => doctorAction(`/api/jobs/${job.id}/stop`)}>Stop job / recovery</button>}
                <button className="ghost-btn" disabled={job.running || doctorBusy}
                  onClick={() => doctorAction(`/api/jobs/${job.id}/plan`)}>Doctor: analyze / reassign</button>
                <button className="ghost-btn" onClick={() => act(async () => {
                  const data = await api<unknown>(`/api/jobs/${job.id}/audit`);
                  setAudit({ jobId: job.id, text: JSON.stringify(data, null, 2) });
                })}>Audit / subtasks</button>
                <button className="ghost-btn" onClick={() => { setDraft({ ...job }); setEditingId(job.id); }}>Edit</button>
                {deleteButtons(job)}
              </div>
            </div>
            <p className="muted pre">{job.prompt}</p>
            <p className="muted small">Job ID: <code>{job.id}</code> · Audit UUID: <code>{job.uid}</code>
              {job.parent_job_id && <> · Parent: <code>{job.parent_job_id}</code></>}</p>
            <p className="muted small">Assignment: {job.agent ? `@${job.agent}` : 'unassigned'} · Doctor: {job.planning_state || 'ready'}
              {job.team_agents?.length ? ` · team: ${job.team_agents.join(', ')}` : ''}
              {job.recovery_state && ` · recovery: ${job.recovery_state}`}</p>
            {job.doctor_plan?.reason && <div className="medic-panel">
              <b>Doctor plan:</b> {job.doctor_plan.reason}
              <p>Proposed assignment: @{job.doctor_plan.agent}{job.doctor_plan.team_agents?.length
                ? ` · team: ${job.doctor_plan.team_agents.join(', ')}` : ''}</p>
              {job.doctor_plan.fallback_reason && <p className="muted small">Model unavailable; deterministic routing: {job.doctor_plan.fallback_reason}</p>}
              {job.doctor_plan.subtasks?.map((step, i) => <p key={i}>{i + 1}. {step.name} — {step.agents.join(', ')}<br />{step.prompt}</p>)}
              {!!job.doctor_plan.tools?.length && <p>Suggested tools (no new permissions): {job.doctor_plan.tools.join(', ')}</p>}
              {job.planning_state === 'needs_approval' && <button className="primary-btn" disabled={job.running || doctorBusy}
                onClick={() => doctorAction(`/api/jobs/${job.id}/plan/approve`, { plan_id: job.doctor_plan?.id })}>Approve this workflow</button>}
            </div>}
            {audit?.jobId === job.id && <details open><summary>Privacy-filtered audit and subtasks</summary><pre>{audit.text}</pre></details>}
            {job.success_criteria && <p className="muted"><b>Success:</b> {job.success_criteria}</p>}
            <div className="settings-tabs">
              <button className={`settings-tab ${tab === 'runs' ? 'active' : ''}`} onClick={() => setTab('runs')}>Runs ({normalRuns.length})</button>
              <button className={`settings-tab ${tab === 'bench' ? 'active' : ''}`} onClick={() => setTab('bench')}>Benchmark ({benchRuns.length})</button>
            </div>

            {tab === 'runs' && (
              <>
                {normalRuns.length === 0 && <div className="muted">No runs yet.</div>}
                {normalRuns.map(r => (
                  <div className="run-row" key={r.id}>
                    <div className="run-head" onClick={() => setOpenRun(openRun === r.id ? null : r.id)}>
                      <span>{STATUS_ICON[r.status] || r.status}{r.diagnosis && <span title="Diagnosed by the medic"> 🩺</span>}</span>
                      <span>{fmtTime(r.started)}</span>
                      <span className="muted">{r.kind === 'step' ? 'team step · ' : ''}{r.trigger}{r.attempt > 1 ? ` · attempt ${r.attempt}` : ''}</span>
                      <span className="muted">{r.ms != null ? `${(r.ms / 1000).toFixed(1)} s` : ''}</span>
                      <span className="muted">{r.provider && `${r.provider}/${r.model}`}</span>
                      <ScoreBadge score={r.score} />
                      <span className="row-actions" onClick={e => e.stopPropagation()}>
                        {r.status === 'running'
                          ? <button className="ghost-btn" onClick={() => cancel(r)}>Stop</button>
                          : <button className="ghost-btn" onClick={() => evaluate(r)}>{r.score == null ? 'Evaluate' : 'Re-evaluate'}</button>}
                        <button className="icon-btn ai" onClick={() => void askAI(job.id, r.id)}
                          aria-label={`AI Ask about run ${r.id}`} title="AI Ask about this run">✦</button>
                        {r.status !== 'running' && ['doctor', 'medic'].includes(job.created_by || '') && (
                          <>
                            <button className="ghost-btn" onClick={() => exportInvestigation(r)}
                              title="Download a GitHub-ready draft with action timestamps and outcomes, without private log text">
                              Export bug report</button>
                            <a className="ghost-btn" href="https://github.com/adrianx26/zensynora/issues/new?template=bug_report.md"
                              target="_blank" rel="noopener noreferrer"
                              title="Open GitHub, then paste only your reviewed report. Nothing is uploaded automatically.">
                              Report on GitHub</a>
                          </>
                        )}
                        {['error', 'timeout', 'interrupted'].includes(r.status) && (
                          <button className="ghost-btn" disabled={medicBusy === r.id} onClick={() => askMedic(r)}
                            title="The medic finds why this run failed and suggests a fix">
                            {medicBusy === r.id ? '🩺 Diagnosing…' : r.diagnosis ? '🩺 Diagnose again' : '🩺 Ask the medic'}</button>
                        )}
                        {['error', 'timeout', 'interrupted', 'blocked'].includes(r.status) &&
                          <button className="ghost-btn" disabled={job.running || doctorBusy}
                            onClick={() => doctorAction(`/api/jobs/runs/${r.id}/retry-assisted`)}
                            title="Investigate this failure, adjust safe execution limits, choose active agents and retry within configured limits">
                            Doctor / Medic: investigate &amp; retry</button>}
                      </span>
                    </div>
                    {openRun === r.id && (
                      <div className="run-body">
                        <p className="muted small">Run UUID: <code>{r.uid}</code> · Agent: {r.agent || job.agent}
                          {r.parent_run_id && ` · linked run #${r.parent_run_id}`}</p>
                        {r.error && <div className="probe-err">{r.error}</div>}
                        {r.output && <pre>{r.output}</pre>}
                        {r.diagnosis && (
                          <DiagnosisPanel d={r.diagnosis} busy={medicBusy === r.id}
                            onFix={runAgain => applyFix(r, runAgain)} onCode={() => codeFix(r)} />
                        )}
                        {r.evaluation && Object.keys(r.evaluation).length > 0 && (
                          <div className="notice"><b>Evaluation{r.evaluation.judge ? ` (${r.evaluation.judge})` : ''}:</b> {r.evaluation.verdict} — {r.evaluation.reasons || r.evaluation.error}</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}

            {tab === 'bench' && (
              <>
                <p className="muted">Run this job's prompt on several models at once and compare speed and quality (scored by the main model).</p>
                <div className="field-row">
                  <select className="field-input" value={benchInput.provider} onChange={e => setBenchInput(b => ({ ...b, provider: e.target.value }))}>
                    {(models?.providers || []).map(p => <option key={p.name} value={p.name}>{p.label}</option>)}
                  </select>
                  <input className="field-input" placeholder="model" value={benchInput.model} onChange={e => setBenchInput(b => ({ ...b, model: e.target.value }))} />
                  <button className="ghost-btn" disabled={!benchInput.provider || !benchInput.model || bench.length >= 8}
                    onClick={() => setBench(b => [...b.filter(x => !(x.provider === benchInput.provider && x.model === benchInput.model)), benchInput])}>Add</button>
                </div>
                <div className="chip-row">
                  {models?.rotation.entries.concat([models.default]).map(m => (
                    <button key={`${m.provider}/${m.model}`} className="chip" onClick={() => setBench(b => (b.some(x => x.provider === m.provider && x.model === m.model) ? b : [...b, m]))}>
                      + {m.provider}/{m.model}
                    </button>
                  ))}
                </div>
                {bench.length > 0 && (
                  <div className="chip-row">
                    {bench.map(m => <span key={`${m.provider}/${m.model}`} className="chip active" onClick={() => setBench(b => b.filter(x => x !== m))}>{m.provider}/{m.model} ×</span>)}
                    <button className="primary-btn" onClick={runBench}>Run benchmark</button>
                  </div>
                )}
                {benchRuns.length > 0 && (
                  <table className="data-table">
                    <thead><tr><th>When</th><th>Model</th><th>Status</th><th>Time</th><th>Score</th><th>Answer</th><th>AI</th></tr></thead>
                    <tbody>
                      {benchRuns.map(r => (
                        <tr key={r.id} onClick={() => setOpenRun(openRun === r.id ? null : r.id)}>
                          <td>{ago(r.started)}</td>
                          <td>{r.provider}/{r.model}</td>
                          <td>{STATUS_ICON[r.status] || r.status}</td>
                          <td>{r.ms != null ? `${(r.ms / 1000).toFixed(1)} s` : '…'}</td>
                          <td><ScoreBadge score={r.score} /></td>
                          <td className="clip">{openRun === r.id ? <pre>{r.output || r.error}</pre> : (r.output || r.error).slice(0, 90)}</td>
                          <td onClick={e => e.stopPropagation()}>
                            <button className="icon-btn ai" onClick={() => void askAI(job.id, r.id)}
                              aria-label={`AI Ask about benchmark run ${r.id}`} title="AI Ask about this run">✦</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default JobsView;
