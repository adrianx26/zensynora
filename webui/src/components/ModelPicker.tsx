// Model picker + latency test used in Settings → Agents (and the Model tab).
//
// ModelPicker: a dropdown with the models the provider offers (with the agent's
// key), "Other…" to type a name the list does not show, and ↻ to reload.
// LatencyTest: sends one tiny real request (POST /api/models/test) and shows
// the round-trip time as a number plus a traffic-light dot.
import React, { useEffect, useState } from 'react';
import { api, type ProbeResult } from '../api';
import { formatMs, LATENCY_HIGH_MS, LATENCY_LOW_MS, LEVEL_TEXT, latencyLevel } from '../latency';

export type ProbeState = ProbeResult & { loading?: boolean };

const OTHER = '__other__';

export const ModelPicker: React.FC<{
  value: string;
  onChange: (model: string) => void;
  probe?: ProbeState;
  onRefresh: () => void;
  placeholder?: string;
}> = ({ value, onChange, probe, onRefresh, placeholder }) => {
  const models = probe?.ok ? probe.models : [];
  const listed = !value || models.includes(value);
  const [typing, setTyping] = useState(false);
  const showSelect = models.length > 0 && !typing;
  return (
    <div className="model-picker">
      <div className="field-row">
        {showSelect ? (
          <select className="field-input" value={value} aria-label="Model"
            onChange={e => (e.target.value === OTHER ? setTyping(true) : onChange(e.target.value))}>
            {!value && <option value="">{placeholder || 'Pick a model…'}</option>}
            {!listed && <option value={value}>{value} (not offered now)</option>}
            {models.map(m => <option key={m} value={m}>{m}</option>)}
            <option value={OTHER}>Other — type a name…</option>
          </select>
        ) : (
          <input className="field-input" value={value} placeholder={placeholder || 'model name'} aria-label="Model"
            onChange={e => onChange(e.target.value)} />
        )}
        {typing && models.length > 0 && (
          <button type="button" className="ghost-btn" title="Back to the list" onClick={() => setTyping(false)}>☰</button>
        )}
        <button type="button" className="ghost-btn" title="Load the models this provider offers (with this key)"
          disabled={probe?.loading} onClick={onRefresh}>{probe?.loading ? '…' : '↻'}</button>
      </div>
      {probe && !probe.loading && probe.ok && (
        <div className="muted small">{models.length} model{models.length === 1 ? '' : 's'} available</div>
      )}
      {probe && !probe.loading && !probe.ok && <div className="probe-err small">{probe.error || 'Could not list the models.'}</div>}
    </div>
  );
};

interface TestResult { ok: boolean; ms: number; reply?: string; error?: string }

export const LatencyTest: React.FC<{ provider: string; model: string; keyId?: string; compact?: boolean }> = ({
  provider, model, keyId, compact,
}) => {
  const [res, setRes] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState(false);
  // A different model/provider/key means the old result no longer applies.
  useEffect(() => { setRes(null); }, [provider, model, keyId]);

  const run = async () => {
    setBusy(true);
    try {
      setRes(await api<TestResult>('/api/models/test', {
        method: 'POST', body: JSON.stringify({ provider, model, key: keyId || '' }),
      }));
    } catch (e) {
      setRes({ ok: false, ms: 0, error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const level = res ? latencyLevel(res.ok, res.ms) : null;
  const legend = `Green under ${formatMs(LATENCY_LOW_MS)}, amber under ${formatMs(LATENCY_HIGH_MS)}, red above. ` +
    'The first request to a local model also loads it — test again for the warm time.';
  return (
    <div className={`latency-test ${compact ? 'compact' : ''}`}>
      <button type="button" className="ghost-btn" disabled={busy || !model.trim()} onClick={run}
        title={model.trim() ? `Send one short request to ${provider}/${model}` : 'Pick a model first'}>
        {busy ? 'Testing…' : '⚡ Test'}
      </button>
      {busy && <span className="latency-dot pending" aria-hidden />}
      {res && level && (
        <span className={`latency-badge ${level}`} title={res.ok ? `${legend}\nReply: ${res.reply || ''}` : res.error}>
          <span className={`latency-dot ${level}`} aria-hidden />
          {res.ok ? <><b>{formatMs(res.ms)}</b> · {LEVEL_TEXT[level]}</> : <>✕ {LEVEL_TEXT.fail}{res.ms ? ` after ${formatMs(res.ms)}` : ''}</>}
        </span>
      )}
      {res && !res.ok && !compact && <div className="probe-err small latency-error">{res.error}</div>}
    </div>
  );
};
