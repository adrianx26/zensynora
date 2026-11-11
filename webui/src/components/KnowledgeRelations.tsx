// Knowledge → Tune → Relations (2026-11-11): every link with its strength (1–5) and how
// often agents used both notes together; strengthen by hand or from use; the AI proposes
// precise types for vague links and new explicit links (you choose what is written).
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { useAutoClear } from '../notice';

interface Link {
  from: string; from_title: string; type: string; target: string; to: string | null; to_title: string;
  strength: number; together: number; vague: boolean; suggested: number;
}
interface Links { links: Link[]; shown: number; matching: number; total: number; vague: number; strong: number; max: number }
interface Proposal { kind: 'type' | 'link'; from: string; from_title: string; type?: string; new_type: string; target: string; to_title: string; why: string }
interface UsagePreview { count: number; changed: { from_title: string; type: string; to_title: string; strength: number; suggested: number; together: number }[] }

const q = (base: string) => `base=${encodeURIComponent(base)}`;

const KnowledgeRelations: React.FC<{ base: string; onOpenNote: (p: string) => void; onChanged: () => void }> = ({ base, onOpenNote, onChanged }) => {
  const [data, setData] = useState<Links | null>(null);
  const [filter, setFilter] = useState('');
  const [only, setOnly] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useAutoClear(msg, setMsg);
  const [busy, setBusy] = useState('');
  const [props, setProps] = useState<Proposal[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [usage, setUsage] = useState<UsagePreview | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<Links>(`/api/knowledge/relations?${q(base)}&q=${encodeURIComponent(filter)}&only=${only}`));
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  }, [base, filter, only]);
  useEffect(() => { const t = window.setTimeout(() => void load(), filter ? 250 : 0); return () => window.clearTimeout(t); }, [load, filter]);

  const run = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key); setMsg(null);
    try { const text = await fn(); if (text) setMsg({ ok: true, text }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(''); }
  };
  const setStrength = (l: Link, strength: number) => run(`s:${l.from}:${l.type}:${l.target}`, async () => {
    await api(`/api/knowledge/relations/strength?${q(base)}`, { method: 'POST', body: JSON.stringify({ permalink: l.from, type: l.type, target: l.target, strength }) });
    await load(); onChanged();
  });
  const fromUsage = (dry: boolean) => run('usage', async () => {
    const r = await api<UsagePreview>(`/api/knowledge/relations/from-usage?${q(base)}`, { method: 'POST', body: JSON.stringify({ dry_run: dry }) });
    if (dry) { setUsage(r); return r.count ? undefined : 'No link to strengthen: no linked notes were used together at least 3 times.'; }
    setUsage(null); await load(); onChanged();
    return `Strengthened ${r.count} link(s).`;
  });
  const askAi = (kind: 'types' | 'links') => run(kind, async () => {
    const r = await api<{ proposals: Proposal[] }>(`/api/knowledge/relations/ai?${q(base)}`, { method: 'POST', body: JSON.stringify({ kind }) });
    setProps(r.proposals); setPicked(new Set(r.proposals.map((_, i) => i)));
    return r.proposals.length ? undefined : kind === 'types' ? 'No vague link type to improve.' : 'The AI found no new link worth adding in this batch.';
  });
  const applyProps = () => run('apply', async () => {
    const chosen = (props || []).filter((_, i) => picked.has(i));
    const r = await api<{ applied: number; failed: { error: string }[] }>(`/api/knowledge/relations/apply?${q(base)}`, { method: 'POST', body: JSON.stringify({ proposals: chosen }) });
    setProps(null); await load(); onChanged();
    if (r.failed.length) throw new Error(`${r.applied} written; ${r.failed.length} not: ${r.failed[0].error}`);
    return `Wrote ${r.applied} change(s) to the notes.`;
  });

  return (
    <section className="settings-card kt-relations">
      <div className="card-head">
        <h3>Relations {data && <span className="muted small">{data.total} links · {data.strong} strengthened · {data.vague} with a vague type</span>}</h3>
        <div className="row-actions">
          <button type="button" className="ghost-btn" disabled={!!busy} onClick={() => fromUsage(true)}
            title="Links whose two notes agents used together often get stronger (never weaker)">{busy === 'usage' ? 'Looking…' : '↗ Strengthen from use'}</button>
          <button type="button" className="ghost-btn" disabled={!!busy || !data?.vague} onClick={() => askAi('types')}
            title="The default model proposes exact types (depends_on, runs_on …) for links like relates_to">{busy === 'types' ? 'Thinking…' : '🤖 Precise types'}</button>
          <button type="button" className="ghost-btn" disabled={!!busy} onClick={() => askAi('links')}
            title="The default model proposes explicit links between notes about the same thing that are not linked yet">{busy === 'links' ? 'Thinking…' : '🤖 Suggest links'}</button>
        </div>
      </div>
      <p className="muted small">A link is a line <code>- uses [[Other note]]</code>; its strength is written after it: <code>(x3)</code>. Stronger links come first when an agent reads a note’s related notes and are drawn thicker in the graph.</p>
      {msg && <div className={`notice ${msg.ok ? '' : 'error'}`} role="status">{msg.text}</div>}

      {usage && usage.count > 0 && (
        <div className="notice">
          <b>{usage.count} link(s) would get stronger</b>
          <ul className="plain-list">{usage.changed.slice(0, 12).map((c, i) => <li key={i}>{c.from_title} <span className="muted">{c.type.replace(/_/g, ' ')}</span> {c.to_title}: {c.strength} → <b>{c.suggested}</b> <span className="muted small">(used together {c.together}×)</span></li>)}</ul>
          {usage.count > 12 && <div className="muted small">…and {usage.count - 12} more.</div>}
          <div className="row-actions"><button type="button" className="primary-btn" disabled={!!busy} onClick={() => fromUsage(false)}>Strengthen</button>
            <button type="button" className="ghost-btn" onClick={() => setUsage(null)}>Cancel</button></div>
        </div>
      )}
      {props && props.length > 0 && (
        <div className="notice">
          <b>🤖 {props.length} proposal(s)</b> <span className="muted small">— nothing is written until you click Apply.</span>
          <ul className="plain-list">
            {props.map((p, i) => (
              <li key={i}><label className="kt-pick"><input type="checkbox" checked={picked.has(i)} onChange={() => { const n = new Set(picked); if (n.has(i)) n.delete(i); else n.add(i); setPicked(n); }} />
                <span>{p.from_title} {p.kind === 'type' ? <><s className="muted">{p.type?.replace(/_/g, ' ')}</s> → </> : <span className="badge">new</span>} <b>{p.new_type.replace(/_/g, ' ')}</b> {p.to_title}
                  {p.why && <span className="muted small"> — {p.why}</span>}</span></label></li>
            ))}
          </ul>
          <div className="row-actions"><button type="button" className="primary-btn" disabled={!!busy || picked.size === 0} onClick={applyProps}>{busy === 'apply' ? 'Writing…' : `Apply ${picked.size}`}</button>
            <button type="button" className="ghost-btn" onClick={() => setProps(null)}>Cancel</button></div>
        </div>
      )}

      <div className="field-row">
        <input className="field-input" placeholder="Filter by note or type…" value={filter} onChange={e => setFilter(e.target.value)} aria-label="Filter links" />
        <select className="field-input narrow" value={only} onChange={e => setOnly(e.target.value)} aria-label="Which links">
          <option value="">All links</option><option value="used">Used together</option><option value="strong">Strengthened</option><option value="vague">Vague type</option>
        </select>
      </div>
      {data && data.links.length === 0 && <div className="kb-empty">{data.total ? 'No link matches.' : 'No links yet — “🤖 Suggest links” proposes some.'}</div>}
      {data && data.links.length > 0 && (
        <div className="kt-scroll">
          <table className="data-table kt-table">
            <thead><tr><th>From</th><th>Type</th><th>To</th><th title="Agent turns that used both notes (last 90 days)">Used together</th><th>Strength</th></tr></thead>
            <tbody>
              {data.links.map(l => (
                <tr key={`${l.from}|${l.type}|${l.target}`}>
                  <td><button type="button" className="kb-sort" onClick={() => onOpenNote(l.from)}>{l.from_title}</button></td>
                  <td>{l.type.replace(/_/g, ' ')}{l.vague && <span className="badge kt-warn" title="Says little about how the notes relate">vague</span>}</td>
                  <td>{l.to ? <button type="button" className="kb-sort" onClick={() => onOpenNote(l.to as string)}>{l.to_title}</button> : <span className="muted" title="No note with this name yet">{l.target}</span>}</td>
                  <td>{l.together || '—'}</td>
                  <td>
                    <span className="kt-strength" role="group" aria-label={`Strength of ${l.from_title} ${l.type} ${l.to_title}: ${l.strength} of ${data.max}`}>
                      {Array.from({ length: data.max }, (_, i) => (
                        <button type="button" key={i} className={`kt-dot ${i < l.strength ? 'on' : ''}`} disabled={!!busy} aria-label={`Set strength ${i + 1}`} aria-pressed={i + 1 === l.strength}
                          onClick={() => setStrength(l, i + 1)} />
                      ))}
                    </span>
                    {l.suggested > l.strength && <button type="button" className="link-btn" disabled={!!busy} title={`Used together ${l.together}×`} onClick={() => setStrength(l, l.suggested)}>→ {l.suggested}</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.matching > data.shown && <div className="muted small">Showing {data.shown} of {data.matching}; filter to see others.</div>}
        </div>
      )}
    </section>
  );
};

export default KnowledgeRelations;
