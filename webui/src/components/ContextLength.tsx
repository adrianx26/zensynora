// Context length of one agent (Settings → Agents): slider + number box.
//
// The slider is logarithmic (1k … the model's maximum). Markers show the
// estimated minimum (prompt + tools + one exchange + a short reply) and the
// recommended size (plus knowledge results, recent conversation and a longer
// reply) — re-estimated whenever the prompt, skills or model change
// (POST /api/agents/context-estimate). 0 = use the model's default.
import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { contextLevel, fmtTokens, snapTokens, CONTEXT_FLOOR, OLLAMA_DEFAULT_CTX, type ContextEstimate } from '../contextLength';

const STEPS = 1000;

export const ContextLength: React.FC<{
  agentName: string;
  systemPrompt: string;
  skills: string[];
  provider: string;
  model: string;
  keyId?: string;
  value: number;
  onChange: (v: number) => void;
}> = ({ agentName, systemPrompt, skills, provider, model, keyId, value, onChange }) => {
  const [est, setEst] = useState<ContextEstimate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const skillKey = skills.join(',');

  // Re-estimate after the prompt / skills / model change (debounced).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const r = await api<ContextEstimate>('/api/agents/context-estimate', {
          method: 'POST',
          body: JSON.stringify({ name: agentName, system_prompt: systemPrompt, skills: skillKey ? skillKey.split(',') : [], provider, model, key: keyId || '' }),
        });
        if (!cancelled) { setEst(r); setError(null); }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 700);
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [agentName, systemPrompt, skillKey, provider, model, keyId]);

  const max = Math.max(CONTEXT_FLOOR * 2, est?.slider_max || 131072);
  const lo = Math.log2(CONTEXT_FLOOR), hi = Math.log2(max);
  const toPos = (v: number) => Math.round(((Math.log2(Math.min(max, Math.max(CONTEXT_FLOOR, v))) - lo) / (hi - lo)) * STEPS);
  const fromPos = (p: number) => snapTokens(2 ** (lo + (p / STEPS) * (hi - lo)), max);
  const pct = (v: number) => `${(toPos(v) / STEPS) * 100}%`;

  const auto = !value;
  // "model default": Ollama uses a small context unless told otherwise; cloud
  // providers use the model's full size.
  const assumed = auto
    ? (provider === 'ollama' ? Math.min(OLLAMA_DEFAULT_CTX, est?.model_max || OLLAMA_DEFAULT_CTX) : est?.model_max || null)
    : value;
  const shown = assumed || max;
  const level = est && assumed ? contextLevel(assumed, est) : null;

  const track = useMemo(() => {
    if (!est) return undefined;
    const a = pct(est.min), b = pct(est.recommended);
    return { background: `linear-gradient(to right, var(--ctx-low) 0 ${a}, var(--ctx-mid) ${a} ${b}, var(--ctx-ok) ${b} 100%)` };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [est, max]);

  const commitDraft = () => {
    if (draft == null) return;
    const n = Math.round(Number(draft.replace(/[^\d.]/g, '')) * (/k$/i.test(draft.trim()) ? 1024 : 1));
    setDraft(null);
    if (!Number.isFinite(n) || n <= 0) return;
    onChange(Math.min(n, est?.model_max || 10_000_000));
  };

  const p = est?.parts;
  return (
    <div className="ctx-field">
      <div className="field-label-row">
        <label className="field-label">Context length</label>
        <span className="muted small">
          {loading ? 'estimating…' : est ? (
            <>max {est.model_max ? fmtTokens(est.model_max) : 'unknown'}{est.model_max_source === 'library' ? ' (model library)' : ''}</>
          ) : null}
        </span>
      </div>
      <div className="ctx-row">
        <label className="ctx-auto">
          <input type="checkbox" checked={auto} onChange={e => onChange(e.target.checked ? 0 : snapTokens(est?.recommended || 8192, max))} />
          model default
        </label>
        <div className={`ctx-slider ${auto ? 'disabled' : ''}`}>
          <input type="range" min={0} max={STEPS} step={1} value={toPos(shown)} disabled={auto} style={track}
            aria-label="Context length" aria-valuetext={fmtTokens(shown)}
            onChange={e => onChange(fromPos(Number(e.target.value)))} />
          {est && (
            <div className="ctx-marks" aria-hidden>
              <span className="ctx-mark min" style={{ left: pct(est.min) }} title="Estimated minimum">
                <i />min {fmtTokens(est.min)}
              </span>
              <span className="ctx-mark rec" style={{ left: pct(est.recommended) }} title="Recommended">
                <i />rec. {fmtTokens(est.recommended)}
              </span>
              {est.model_max && <span className="ctx-mark max" style={{ left: '100%' }} title="The model's maximum"><i />{fmtTokens(est.model_max)}</span>}
            </div>
          )}
        </div>
        <input className="field-input ctx-box" inputMode="numeric" aria-label="Context length in tokens"
          value={draft ?? (auto ? '' : String(value))} placeholder={auto ? 'auto' : ''}
          onChange={e => setDraft(e.target.value)} onBlur={commitDraft}
          onKeyDown={e => { if (e.key === 'Enter') commitDraft(); if (e.key === 'Escape') setDraft(null); }} />
        <span className="muted small">tokens</span>
      </div>
      {error && <div className="probe-err small">Could not estimate: {error}</div>}
      {est && auto && !level && (
        <div className="ctx-status muted">Uses the model's own default (its size is not known — set a value to be sure).</div>
      )}
      {est && level && (
        <div className={`ctx-status ${level.kind}`}>
          <span className={`latency-dot ${level.kind === 'ok' ? 'low' : level.kind === 'mid' ? 'med' : 'fail'}`} aria-hidden />
          <span>
            {auto && provider === 'ollama' ? <>Ollama's own default is usually {fmtTokens(OLLAMA_DEFAULT_CTX)}. </> : null}
            {auto && provider !== 'ollama' ? <>Uses the model's full size ({fmtTokens(assumed || 0)}). </> : null}
            {level.text}
          </span>
          {(auto ? level.kind !== 'ok' : value !== est.recommended) && (
            <button type="button" className="link-btn" onClick={() => onChange(snapTokens(est.recommended, est.model_max || max))}>use recommended</button>
          )}
        </div>
      )}
      {p && (
        <div className="muted small ctx-breakdown" title={`Estimated at ~${est!.chars_per_token} characters per token; real tokenizers differ by about ±20 %.`}>
          Every turn sends ≈ <b>{fmtTokens(est!.fixed)}</b>: prompt {fmtTokens(p.prompt)} · tools {fmtTokens(p.tools)} ({est!.tool_count})
          {p.skills ? <> · skills {fmtTokens(p.skills)}</> : null}{p.team ? <> · team {fmtTokens(p.team)}</> : null}.
          {' '}Recommended adds knowledge {fmtTokens(est!.knowledge)}, recent chat {fmtTokens(est!.history)} and the reply {fmtTokens(est!.reply_recommended)}.
          {' '}Ollama gets this as <code>num_ctx</code>; for every model the oldest messages are left out when a turn would not fit.
        </div>
      )}
    </div>
  );
};
