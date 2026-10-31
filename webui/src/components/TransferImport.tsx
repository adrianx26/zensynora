// Import tools and skills from a file (2026-10-31): a ZenSynora export (.json),
// one tool (.py) or one skill (SKILL.md). Shows what came in and what was
// skipped; a name clash can be retried as "replace" (the current version goes
// to the trash), and tool code that failed the safety check can be retried as
// "trusted" after the user has read it (it still runs in the tool sandbox).
import React, { useRef, useState } from 'react';
import { api, type ImportResult } from '../api';

/** `{ button, panel }`: put the button in the card header and the panel below it. */
export function useTransferImport(onImported: () => void, label = 'Import…'): { button: React.ReactNode; panel: React.ReactNode } {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async (f: { name: string; text: string }, overwrite = false, trust = false) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<ImportResult>('/api/transfer/import', {
        method: 'POST', body: JSON.stringify({ text: f.text, filename: f.name, overwrite, trust }),
      });
      setResult(r);
      onImported();
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const pick = async (f: File | undefined) => {
    if (fileRef.current) fileRef.current.value = '';
    if (!f) return;
    const loaded = { name: f.name, text: await f.text() };
    setFile(loaded);
    send(loaded);
  };

  const clashes = result?.skipped.filter(s => s.reason.includes('already exists')) || [];
  const unchecked = result?.imported.filter(i => i.kind === 'tool' && i.problem) || [];

  const button = (
    <>
      <input ref={fileRef} type="file" accept=".json,.py,.md,application/json,text/x-python,text/markdown,text/plain"
        style={{ display: 'none' }} onChange={e => pick(e.target.files?.[0])} />
      <button className="ghost-btn" disabled={busy} onClick={() => fileRef.current?.click()}
        title="A ZenSynora export (.json), one tool (.py) or one skill (SKILL.md)">{busy ? 'Importing…' : label}</button>
    </>
  );
  const panel = (result || error) ? (
        <div className="import-result settings-card">
          <div className="card-head">
            <h3>Import of {file?.name}</h3>
            <button className="ghost-btn" onClick={() => { setResult(null); setError(null); }}>Close</button>
          </div>
          {error && <div className="notice error">{error}</div>}
          {result && result.imported.length > 0 && (
            <ul className="plain-list">
              {result.imported.map(i => (
                <li key={`${i.kind}-${i.name}`}>
                  ✓ {i.kind} <code>{i.name}</code>{i.replaced ? ' (replaced — previous version in the trash)' : ''}
                  {i.kind === 'tool' && i.enabled === false && <span className="badge">off</span>}
                  {i.problem && <div className="muted small">Not turned on: {i.problem}</div>}
                </li>
              ))}
            </ul>
          )}
          {result && result.skipped.length > 0 && (
            <ul className="plain-list">
              {result.skipped.map(s => <li key={`${s.kind}-${s.name}`}>✗ {s.kind} <code>{s.name}</code> — {s.reason}</li>)}
            </ul>
          )}
          <div className="row-actions">
            {file && clashes.length > 0 && (
              <button className="ghost-btn" disabled={busy}
                onClick={() => window.confirm(`Replace ${clashes.map(c => c.name).join(', ')}? The current versions are kept in the trash.`)
                  && send(file, true, false)}>Replace existing</button>
            )}
            {file && unchecked.length > 0 && (
              <button className="ghost-btn danger" disabled={busy}
                onClick={() => window.confirm(`Turn on ${unchecked.map(u => u.name).join(', ')} although the safety check failed? `
                  + 'Only do this if you have read the code and trust it. It still runs in the tool sandbox.')
                  && send(file, true, true)}>I checked the code — trust it</button>
            )}
          </div>
        </div>
  ) : null;
  return { button, panel };
}
