// Evolve page (2026-11-03): everything that lets the AI improve ZenSynora, in one place.
//
// 1. Ask the AI for a change (optimise / edit / add / move / remove a module, function,
//    script or code, or review the LLM setup). It becomes a one-off job; the agent reads
//    the code and files a change proposal.
// 2. Change proposals: review the diff, approve (installed by the safe updater: backup,
//    side-by-side install, checks, automatic rollback) or reject. Existing panel.
// 3. Models (LLMs): health of every model, replace one in all agents (Models page).
// 4. Agents: level 1-9, and fusion of two agents into one (the lower level; the
//    originals go to the Graveyard and can be revived by Medic).
import React, { useCallback, useEffect, useState } from 'react';
import { ago, api } from '../api';
import ProposalsPanel from './ProposalsPanel';
import ModelHealthCard from './ModelHealthCard';
import { CreateAgent, ProposalSettings, SuggestCard, UndoCard, WherePicker } from './EvolveExtras';
import { useAutoClear } from '../notice';

interface Agent { name: string; level: number; provider: string; model: string; description: string; skills: string[] }
interface Req { id: string; name: string; agent: string; created: number; last_run: number | null; last_status: string }
interface Trashed { id: string; agent: { name: string; level: number }; fused_into: string; at: string }
interface PendingFusion { id: string; sources: string[]; destination: string }
interface Overview {
  agents: Agent[]; requests: Req[]; trash: Trashed[];
  pending_fusions: PendingFusion[];
  self_update: { enabled?: boolean; run_tests?: boolean; auto_approve_low_risk?: boolean };
}
interface Preview {
  a: string; b: string; name: string; description: string; system_prompt: string; skills: string[];
  level: number; model_from: string; models: Record<string, string>; note: string;
}
interface FusionFinding { claim: string; evidence_ids: string[] }
interface FusionSection { findings: FusionFinding[]; limitations: string[] }
interface FusionEstimate { value: number; rationale: string; evidence_ids: string[] }
interface FusionCheck { name: string; ok: boolean }
interface FusionNoteSection { pass: boolean; checks?: FusionCheck[]; notes?: string[] }
interface FusionCandidate {
  a: string; b: string;
  status: 'selected' | 'assessed' | 'error'; error?: string; blocked_reason?: string;
  probability?: number; compatibility?: number; progress?: number; can_fuse?: boolean;
  rationale?: string; selection_rationale?: string; evidence_ids?: string[];
  quality_assurance?: FusionNoteSection; performance_optimization?: FusionNoteSection; security?: FusionNoteSection;
  assessment?: {
    estimates: { compatibility: FusionEstimate; fusion_success_probability: FusionEstimate };
    quality_assurance: FusionSection; performance_optimization: FusionSection; security: FusionSection;
  };
  job_progress?: Record<string, { state: string; reason: string; runs_observed: number; successful_runs: number; failed_runs: number }>;
}
interface FusionCandidateResponse {
  candidates: FusionCandidate[]; heuristic_pool: FusionCandidate[]; checked_pairs: number; assessed_pairs: number;
  selection_method: 'model' | 'heuristic_fallback'; rationale: string; evidence_ids: string[]; at: string;
  selection_error?: string; model_scope?: 'local' | 'configured';
}

const ACTIONS = [
  { value: 'optimise', label: 'Optimise', help: 'faster, simpler or more reliable; same behaviour' },
  { value: 'edit', label: 'Change', help: 'change what it does' },
  { value: 'add', label: 'Add', help: 'something new' },
  { value: 'move', label: 'Move or rename', help: 'to another place or name' },
  { value: 'remove', label: 'Remove', help: 'delete it and what uses it' },
];
const KINDS = [
  { value: 'module', label: 'Module (Python file)', placeholder: 'myclaw/knowledge/overview.py' },
  { value: 'function', label: 'Function', placeholder: 'myclaw/knowledge/overview.py:_fill' },
  { value: 'script', label: 'Script', placeholder: 'update.sh' },
  { value: 'code', label: 'Code (anywhere)', placeholder: 'leave empty: the agent finds the place' },
  { value: 'llm', label: 'LLM setup (models per agent)', placeholder: 'leave empty, or an agent name' },
];
const STATUS: Record<string, string> = { ok: 'finished — see its report', error: '✗ failed', timeout: '✗ timed out', running: '… running', '': 'not run yet' };

const EvolveView: React.FC<{ onOpenModels: () => void; onOpenJobs: () => void; onOpenUpdates: () => void }> = ({ onOpenModels, onOpenJobs, onOpenUpdates }) => {
  const [ov, setOv] = useState<Overview | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  useAutoClear(notice, setNotice);  // 2026-11-08: success messages go away within 3 s
  const [form, setForm] = useState({ action: 'optimise', target_kind: 'function', target: '', goal: '', agent: 'default' });
  const [busy, setBusy] = useState('');
  const [pair, setPair] = useState({ a: '', b: '' });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [fusionEval, setFusionEval] = useState<FusionCandidateResponse | null>(null);
  const [configuredFusionModel, setConfiguredFusionModel] = useState(false);

  const load = useCallback(async () => {
    try {
      setOv(await api<Overview>('/api/evolve/overview'));
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key); setNotice(null);
    try {
      const text = await fn();
      if (text) setNotice({ ok: true, text });
      await load();
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setBusy('');
    }
  };

  const kind = KINDS.find(k => k.value === form.target_kind) || KINDS[0];
  const named = (ov?.agents || []).filter(a => a.name !== 'default');
  const su = ov?.self_update || {};

  return (
    <div className="settings-view evolve">
      <div className="settings-body">
        <div className="settings-card">
          <h3>🧬 Evolve</h3>
          <p className="muted">
            Here the AI can improve ZenSynora: its code, scripts and functions, which models the agents use, and the agents themselves.
            <b> Nothing in the program changes until you approve it.</b> An approved change is installed like an update: a full backup,
            installed next to the current version, checked{su.run_tests ? ' and tested' : ''}, then switched to, with automatic rollback if anything fails.
            You can undo it later on the Updates page (Roll back).
          </p>
          <ol className="evolve-steps muted small">
            <li><b>Ask</b> — describe the change below. An agent reads the code and files a change proposal.</li>
            <li><b>Review</b> — read the reason and the exact diff under <i>Change proposals</i>.</li>
            <li><b>Approve or reject</b> — approved changes are installed safely; rejected ones are kept for the record.</li>
          </ol>
          {!su.enabled && su.enabled !== undefined && <div className="notice error">Change proposals are switched off (medic.self_update.enabled).</div>}
          {notice && <div className={`notice ${notice.ok ? '' : 'error'}`}>{notice.text}</div>}
        </div>

        {/* 1. ask for a change */}
        <div className="settings-card">
          <h3>1 · Ask the AI for a change</h3>
          <SuggestCard
            onUse={sg => setForm({ ...form, action: sg.action, target_kind: sg.target_kind, target: sg.target, goal: sg.goal })}
            onStart={sg => act('req', async () => {
              const r = await api<{ name: string }>('/api/evolve/request', { method: 'POST', body: JSON.stringify({ action: sg.action, target_kind: sg.target_kind, target: sg.target, goal: sg.goal, agent: form.agent }) });
              return `Started “${r.name}”. Its proposal will appear under Change proposals.`;
            })} />
          <div className="evolve-form">
            <label className="field"><span className="field-label">What to do</span>
              <select className="field-input" value={form.action} onChange={e => setForm({ ...form, action: e.target.value })}>
                {ACTIONS.map(a => <option key={a.value} value={a.value}>{a.label} — {a.help}</option>)}
              </select></label>
            <label className="field"><span className="field-label">To what</span>
              <select className="field-input" value={form.target_kind} onChange={e => setForm({ ...form, target_kind: e.target.value })}>
                {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
              </select></label>
            <label className="field"><span className="field-label">Where (file, file:function, or empty)</span>
              <WherePicker value={form.target} placeholder={kind.placeholder} onChange={v => setForm({ ...form, target: v })} /></label>
            <label className="field"><span className="field-label">Agent that does the work</span>
              <select className="field-input" value={form.agent} onChange={e => setForm({ ...form, agent: e.target.value })}>
                {(ov?.agents || []).map(a => <option key={a.name} value={a.name}>{a.name} (level {a.level} · {a.model})</option>)}
              </select></label>
          </div>
          <label className="field"><span className="field-label">Goal — what should be different afterwards, and how to tell</span>
            <textarea className="field-input" rows={4} value={form.goal} onChange={e => setForm({ ...form, goal: e.target.value })}
              placeholder="e.g. The Knowledge overview loads in under 1 second with 5,000 notes. Same numbers as now." /></label>
          <p className="muted small">
            {form.target_kind === 'llm'
              ? 'The agent looks at recent model errors and which model each agent uses, and recommends changes. It does not change code.'
              : 'The agent reads the code and its tests, makes the smallest change that reaches the goal (with tests), and files it as a proposal for you to review. If the goal is unclear or unsafe it explains why instead.'}
            {' '}It runs as a job (up to 30 steps, 15 minutes): progress and the agent’s report are on the Jobs page.
          </p>
          <div className="save-actions">
            <button className="primary-btn" disabled={!!busy || !form.goal.trim()}
              onClick={() => act('req', async () => {
                const r = await api<{ job_id: string; name: string }>('/api/evolve/request', { method: 'POST', body: JSON.stringify(form) });
                setForm({ ...form, goal: '' });
                return `Started “${r.name}”. Its proposal will appear under Change proposals.`;
              })}>{busy === 'req' ? 'Starting…' : 'Ask the AI'}</button>
          </div>
          {ov && ov.requests.length > 0 && (
            <>
              <h4>Your requests</h4>
              <ul className="plain-list">
                {ov.requests.map(r => (
                  <li key={r.id}><b>{r.name}</b> <span className="muted small">@{r.agent} · {ago(r.created)} · {STATUS[r.last_status] ?? r.last_status}</span></li>
                ))}
              </ul>
              <div className="save-actions">
                <button className="ghost-btn small-btn" onClick={onOpenJobs}>Open Jobs for the reports</button>
                <button className="ghost-btn small-btn" onClick={load}>↻ Refresh</button>
              </div>
            </>
          )}
        </div>

        {/* 2. proposals (existing panel: diff, approve and install, reject) */}
        <h3 className="evolve-h">2 · Change proposals</h3>
        <div className="settings-card">
          <ProposalSettings values={ov?.self_update || {}} onSaved={load} />
        </div>
        <ProposalsPanel />
        <UndoCard onOpenUpdates={onOpenUpdates} />

        {/* 3. models */}
        <h3 className="evolve-h">3 · Models (LLMs)</h3>
        <ModelHealthCard onReplace={() => onOpenModels()} />
        <div className="save-actions">
          <button className="ghost-btn" disabled={!!busy}
            onClick={() => act('llm', async () => {
              const r = await api<{ name: string }>('/api/evolve/request', { method: 'POST',
                body: JSON.stringify({ action: 'optimise', target_kind: 'llm', target: '', goal: 'Review which model each agent uses and recommend better choices (fewer failures, faster answers, enough context).', agent: form.agent }) });
              return `Started “${r.name}”. The recommendation will be in its report on the Jobs page.`;
            })}>Ask the AI to review the model setup</button>
        </div>

        {/* 4. agents: levels and fusion */}
        <div className="settings-card">
          <h3>4 · Agents: levels and fusion</h3>
          <p className="muted small">Every agent has a level from 1 to 9 (new agents start at 1). For now the level is a label.</p>
          <table className="cost-table evolve-agents">
            <thead><tr><th>Agent</th><th>Level</th><th>Model</th><th>What it does</th></tr></thead>
            <tbody>
              {(ov?.agents || []).map(a => (
                <tr key={a.name}>
                  <td><b>{a.name}</b></td>
                  <td>
                    <select className="field-input" value={a.level} disabled={!!busy}
                      onChange={e => {
                        const level = Number(e.target.value);   // read now: the select re-renders before the save ends
                        act(`lvl-${a.name}`, async () => {
                          await api(`/api/evolve/agents/${encodeURIComponent(a.name)}/level`, { method: 'PUT', body: JSON.stringify({ level }) });
                          return `${a.name} is now level ${level}.`;
                        });
                      }}>
                      {[1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </td>
                  <td className="muted small">{a.provider}/{a.model}</td>
                  <td className="muted small">{a.description}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <CreateAgent onCreated={text => { setNotice({ ok: true, text }); load(); }} />

          <h4>Fuse two agents into one</h4>
          {(ov?.pending_fusions || []).map(operation => (
            <div className="notice error" key={operation.id}>
              Fusion of {operation.sources.join(' and ')} into {operation.destination} needs recovery.{' '}
              <button className="ghost-btn small-btn" disabled={!!busy}
                onClick={() => act(`resume-${operation.id}`, async () => {
                  const result = await api<{ state: string }>('/api/evolve/fuse/resume', {
                    method: 'POST', body: JSON.stringify({ id: operation.id }),
                  });
                  return `Fusion recovery: ${result.state}.`;
                })}>Resume recovery</button>
            </div>
          ))}
          <p className="muted small">
            The AI writes one prompt that keeps the duties and rules of both; you review and edit it first. Skills are combined, the model comes from the agent you pick,
            and the level is the lower of the two. Channels, direct messages and jobs move to the new agent. Both originals go to the Graveyard (disabled, no LLM).
          </p>
          <div className="save-actions">
            <button className="ghost-btn" disabled={!!busy}
              onClick={() => act('fuse-eval', async () => {
                setFusionEval(await api<FusionCandidateResponse>('/api/evolve/fuse/evaluate', { method: 'POST',
                  body: JSON.stringify({ limit: 10, configured_model: configuredFusionModel }) }));
              })}>
              {busy === 'fuse-eval' ? 'Evaluating…' : '🤖 Evaluate fusion candidates'}</button>
          </div>
          <label className="muted small"><input type="checkbox" checked={configuredFusionModel}
            onChange={e => setConfiguredFusionModel(e.target.checked)} /> Use my configured main / fallback models instead of the loopback model.
            This sends the sanitized candidate evidence to those providers when you click Evaluate.</label>
          <p className="muted small">If no valid model is available, heuristic candidates still appear with the reason.
            Review the estimates and evidence; they do not authorize a fusion.</p>
          {fusionEval && (
            <div className="evolve-suggest">
              <div className="muted small">Assessed {fusionEval.assessed_pairs} of {fusionEval.checked_pairs} candidate pairs at {fusionEval.at}.
                Selection: {fusionEval.selection_method === 'model' ? `${fusionEval.model_scope || 'local'} model from a deterministic heuristic pool` : 'deterministic heuristic fallback'}.
                No tests, benchmarks or security scans were performed.</div>
              {fusionEval.selection_error && <p className="notice error">{fusionEval.selection_error}</p>}
              <div className="muted small"><b>Selection rationale:</b> {fusionEval.rationale}{' '}
                {fusionEval.evidence_ids.length > 0 && <>Evidence: {fusionEval.evidence_ids.join(', ')}.</>}</div>
              <div className="muted small">Top heuristic pool considered: {fusionEval.heuristic_pool.length} of {fusionEval.checked_pairs} pairs.</div>
              {fusionEval.candidates.map((c, i) => (
                <div key={`${c.a}:${c.b}:${i}`} className="ai-item">
                  <div><b>{c.a} + {c.b}</b></div>
                  {c.error && <div className="notice error">{c.error}</div>}
                  {c.blocked_reason && <div className="notice error">{c.blocked_reason}</div>}
                  {c.probability !== undefined && <>
                    <div className="muted small">Heuristic probability: {(c.probability * 100).toFixed(0)}% · compatibility: {((c.compatibility || 0) * 100).toFixed(0)}% · progress: {((c.progress || 0) * 100).toFixed(0)}%</div>
                    {!c.can_fuse && <div className="muted small">The heuristic marked this pair as needing review before fusion.</div>}
                  </>}
                  {c.assessment && <>
                    <div className="muted small">Estimated compatibility: {(c.assessment.estimates.compatibility.value * 100).toFixed(0)}% — {c.assessment.estimates.compatibility.rationale}</div>
                    <div className="muted small">Estimated fusion success: {(c.assessment.estimates.fusion_success_probability.value * 100).toFixed(0)}% — {c.assessment.estimates.fusion_success_probability.rationale}</div>
                    {(['quality_assurance', 'performance_optimization', 'security'] as const).map(section => (
                      <div key={section} className="muted small"><b>{section.replace(/_/g, ' ')}:</b>{' '}
                        {c.assessment?.[section].findings.map(f => `${f.claim} (${f.evidence_ids.join(', ')})`).join(' ') || 'No findings.'}{' '}
                        {c.assessment?.[section].limitations.join(' ')}
                      </div>
                    ))}
                    {Object.entries(c.job_progress || {}).map(([key, p]) => (
                      <div key={key} className="muted small">{key === 'job_history.a' ? c.a : c.b} progress: {p.state === 'unknown'
                        ? p.reason : `${p.runs_observed} runs observed, ${p.successful_runs} successful, ${p.failed_runs} failed. ${p.reason}`}</div>
                    ))}
                  </>}
                  <div className="probe-row">
                    <button className="ghost-btn small-btn" disabled={!['selected', 'assessed'].includes(c.status) || !!c.blocked_reason} onClick={() => { setPair({ a: c.a, b: c.b }); setPreview(null); }}>
                      Use this pair</button>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="evolve-form">
            {(['a', 'b'] as const).map(k => (
              <label key={k} className="field"><span className="field-label">{k === 'a' ? 'First agent' : 'Second agent'}</span>
                <select className="field-input" value={pair[k]} onChange={e => { setPair({ ...pair, [k]: e.target.value }); setPreview(null); }}>
                  <option value="">Choose…</option>
                  {named.map(a => <option key={a.name} value={a.name} disabled={a.name === pair[k === 'a' ? 'b' : 'a']}>{a.name} (level {a.level})</option>)}
                </select></label>
            ))}
          </div>
          <div className="save-actions">
            <button className="ghost-btn" disabled={!!busy || !pair.a || !pair.b || pair.a === pair.b}
              onClick={() => act('preview', async () => { setPreview(await api<Preview>('/api/evolve/fuse/preview', { method: 'POST', body: JSON.stringify(pair) })); })}>
              {busy === 'preview' ? 'The AI is drafting…' : 'Draft the merged agent'}</button>
          </div>
          {preview && (
            <div className="settings-card evolve-preview">
              {preview.note && <div className="muted small">⚠ {preview.note}</div>}
              <div className="evolve-form">
                <label className="field"><span className="field-label">Name</span>
                  <input className="field-input" value={preview.name} onChange={e => setPreview({ ...preview, name: e.target.value })} /></label>
                <label className="field"><span className="field-label">Model</span>
                  <select className="field-input" value={preview.model_from} onChange={e => setPreview({ ...preview, model_from: e.target.value })}>
                    {[preview.a, preview.b].map(n => <option key={n} value={n}>{preview.models[n]} (from {n})</option>)}
                  </select></label>
              </div>
              <label className="field"><span className="field-label">Description</span>
                <input className="field-input" value={preview.description} onChange={e => setPreview({ ...preview, description: e.target.value })} /></label>
              <label className="field"><span className="field-label">System prompt</span>
                <textarea className="field-input" rows={10} value={preview.system_prompt} onChange={e => setPreview({ ...preview, system_prompt: e.target.value })} /></label>
              <div className="muted small">Level <b>{preview.level}</b> (the lower one) · skills: {preview.skills.join(', ') || 'none'}</div>
              <div className="save-actions">
                <button className="ghost-btn" onClick={() => setPreview(null)}>Cancel</button>
                <button className="primary-btn" disabled={!!busy || !preview.name.trim() || !preview.system_prompt.trim()}
                  onClick={() => act('fuse', async () => {
                    const r = await api<{ agent: Agent }>('/api/evolve/fuse', { method: 'POST', body: JSON.stringify({
                      a: preview.a, b: preview.b, name: preview.name, description: preview.description,
                      system_prompt: preview.system_prompt, model_from: preview.model_from }) });
                    setPreview(null); setPair({ a: '', b: '' });
                    setFusionEval(null);
                    return `${preview.a} and ${preview.b} are now ${r.agent.name} (level ${r.agent.level}). The originals are in the Graveyard below.`;
                  })}>{busy === 'fuse' ? 'Fusing…' : `Replace ${preview.a} and ${preview.b} by ${preview.name || '…'}`}</button>
              </div>
            </div>
          )}

          {ov && ov.trash.length > 0 && (
            <>
              <h4>Graveyard (disabled agents)</h4>
              <p className="muted small">Graveyard agents are removed from the active configuration. Revival is handled by the Medic service.</p>
              <ul className="plain-list">
                {ov.trash.map(t => (
                  <li key={t.id}>
                    <b>{t.agent.name}</b> <span className="muted small">level {t.agent.level} · fused into {t.fused_into} · {t.at}</span>{' '}
                    <button className="ghost-btn small-btn" disabled={!!busy}
                      onClick={() => act(`revive-active-${t.id}`, async () => {
                        await api('/api/evolve/graveyard/revive', { method: 'POST', body: JSON.stringify({ name: t.agent.name, entry_id: t.id, mode: 'active' }) });
                        return `Medic revived ${t.agent.name} to active agents.`;
                      })}>Revive (Medic) to active</button>
                    <button className="ghost-btn small-btn" disabled={!!busy}
                      onClick={() => act(`revive-fusion-${t.id}`, async () => {
                        await api('/api/evolve/graveyard/revive', { method: 'POST', body: JSON.stringify({ name: t.agent.name, entry_id: t.id, mode: 'fusion' }) });
                        return `Medic revived ${t.agent.name} for fusion planning.`;
                      })}>Revive (Medic) for fusion</button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default EvolveView;
