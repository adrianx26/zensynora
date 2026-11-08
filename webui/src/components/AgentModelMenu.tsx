// Chat window: change an agent's provider and model (2026-11-08).
//
// A small chip "provider · model ▾" per agent in the channel bar. It opens a popover
// with the configured providers and the models the chosen provider offers (the same
// ModelPicker as Settings → Agents). Saving goes through the normal settings save
// (PUT /api/settings/agent-model): same checks, the agents are rebuilt, the next
// message uses the new model.
import React, { useEffect, useRef, useState } from 'react';
import { api, type ProbeResult } from '../api';
import { ModelPicker, type ProbeState } from './ModelPicker';

interface Row { agent: string; provider: string; model: string; inherits: boolean }

const AgentModelMenu: React.FC<{ agent: string; provider?: string; model?: string; onSaved?: () => void }> = ({
  agent, provider, model, onSaved,
}) => {
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<string[]>([]);
  const [row, setRow] = useState<Row | null>(null);
  const [prov, setProv] = useState(provider || '');
  const [mdl, setMdl] = useState(model || '');
  const [probe, setProbe] = useState<Record<string, ProbeState>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const runProbe = async (p: string) => {
    if (!p) return;
    setProbe(s => ({ ...s, [p]: { ok: false, models: [], loading: true } }));
    try {
      const res = await api<ProbeResult>('/api/settings/probe', { method: 'POST', body: JSON.stringify({ provider: p }) });
      setProbe(s => ({ ...s, [p]: res }));
    } catch (e) {
      setProbe(s => ({ ...s, [p]: { ok: false, models: [], error: (e as Error).message } }));
    }
  };

  useEffect(() => {
    if (!open) return;
    setMsg(null);
    api<{ agents: Row[]; providers: string[] }>('/api/settings/agent-models').then(d => {
      setProviders(d.providers || []);
      const r = d.agents.find(a => a.agent === agent) || null;
      setRow(r);
      if (r) { setProv(r.provider); setMdl(r.model); if (!probe[r.provider]) void runProbe(r.provider); }
    }).catch(e => setMsg({ ok: false, text: (e as Error).message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, agent]);

  useEffect(() => {  // close on a click outside or Escape
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await api('/api/settings/agent-model', { method: 'PUT', body: JSON.stringify({ agent, provider: prov, model: mdl.trim() }) });
      setMsg({ ok: true, text: 'Saved — the next message uses it.' });
      onSaved?.();
      setTimeout(() => setOpen(false), 700);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const changed = !!row && (prov !== row.provider || mdl.trim() !== row.model);
  const label = model ? `${provider ? provider + ' · ' : ''}${model}` : 'model';

  return (
    <div className="agent-model" ref={ref}>
      <button type="button" className="agent-model-chip" aria-expanded={open} aria-haspopup="dialog"
        title={`@${agent} answers with ${label} — click to change`} onClick={() => setOpen(v => !v)}>
        <span className="agent-model-text">{label}</span> ▾
      </button>
      {open && (
        <div className="agent-model-pop" role="dialog" aria-label={`Model of @${agent}`}>
          <div className="field-label">Model of @{agent}</div>
          <div className="field">
            <label className="muted small" htmlFor={`prov-${agent}`}>Provider</label>
            <select id={`prov-${agent}`} className="field-input" value={prov}
              onChange={e => { const p = e.target.value; setProv(p); setMdl(''); if (!probe[p]) void runProbe(p); }}>
              {!providers.includes(prov) && prov && <option value={prov}>{prov}</option>}
              {providers.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div className="field">
            <span className="muted small">Model</span>
            <ModelPicker value={mdl} onChange={setMdl} probe={probe[prov]} onRefresh={() => runProbe(prov)} />
          </div>
          {row?.inherits && prov === row.provider && (
            <div className="muted small">The provider now comes from the default agent; saving gives @{agent} its own.</div>
          )}
          {msg && <div className={msg.ok ? 'muted small' : 'probe-err small'} role="status">{msg.text}</div>}
          <div className="save-actions">
            <button type="button" className="ghost-btn" onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className="primary-btn" disabled={busy || !changed || !prov || !mdl.trim()} onClick={save}>
              {busy ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentModelMenu;
