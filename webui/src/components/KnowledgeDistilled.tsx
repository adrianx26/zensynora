// Distilled knowledge (KB phase 4, 2026-10-24): the persona (L3), scenarios (L2)
// and facts (L1) worked out from the notes, each with its confidence and a
// drill-down to the notes and lines it comes from. Data: /api/knowledge/facts,
// /scenarios, /persona, /drill/{kind}/{id}, /distill(/status). Derived data:
// nothing here changes a note.
import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api';

interface Fact {
  id: number; text: string; category: string; kind: string; tags: string[];
  confidence: number; support: number; sources: number; conflicted: boolean; about_user: boolean;
  display?: string; instruction?: boolean;
}
interface Scenario { id: number; title: string; summary: string; confidence: number; size: number; notes: number; grouped_by: string; facts: Fact[] }
interface PersonaItem { id: number; text: string; confidence: number; support: number }
interface Persona { version: number; created_at: string; summary: string; sections: Record<string, PersonaItem[]>; titles: Record<string, string>; confidence: number; versions: number[] }
interface Evidence { permalink: string; title: string; line: string; source?: string | null; exists: boolean; merged: boolean }
interface Drill { kind: string; facts: (Fact & { evidence: Evidence[] })[]; notes: string[] }
interface Status {
  enabled: boolean; facts: number; scenarios: number; persona_version: number; notes: number; pending: number;
  last_run: string | null; running: boolean; last_error?: string | null;
  options: { distill_every: number; idle_minutes: number };
  job?: { status: string; phase: string; done: number; total: number; error?: string | null; result?: { message?: string } | null } | null;
}

const q = (base: string) => `base=${encodeURIComponent(base)}`;

function confColor(c: number): string {
  if (c >= 0.8) return '#3fb950';
  if (c >= 0.6) return '#58a6ff';
  if (c >= 0.4) return '#d29922';
  return '#8b949e';
}

const Conf: React.FC<{ value: number }> = ({ value }) => (
  <span className="kb-conf" title={`Confidence ${value.toFixed(2)}: more supporting notes raise it; old facts and conflicts lower it`}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 4, minWidth: 64 }}>
    <span style={{ display: 'inline-block', width: 36, height: 6, borderRadius: 3, background: 'var(--border, #30363d)', overflow: 'hidden' }}>
      <span style={{ display: 'block', width: `${Math.round(value * 100)}%`, height: '100%', background: confColor(value) }} />
    </span>
    <span className="muted small">{value.toFixed(2)}</span>
  </span>
);

const DrillView: React.FC<{ base: string; kind: string; id: number | 'latest'; onOpenNote: (p: string) => void }> = ({ base, kind, id, onOpenNote }) => {
  const [data, setData] = useState<Drill | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api<Drill>(`/api/knowledge/drill/${kind}/${id}?${q(base)}`)
      .then(d => { if (live) setData(d); })
      .catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [base, kind, id]);
  if (err) return <div className="notice error">{err}</div>;
  if (!data) return <div className="muted small">Loading…</div>;
  return (
    <div className="kb-drill" style={{ borderLeft: '2px solid var(--border, #30363d)', paddingLeft: 10, margin: '6px 0' }}>
      {data.facts.map(f => (
        <div key={f.id} style={{ marginBottom: 6 }}>
          {kind !== 'fact' && <div className="small">f{f.id} [{f.category}] {f.display || f.text}</div>}
          {f.evidence.map((e, i) => (
            <div key={i} className="small muted" style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
              <span>↳</span>
              {e.exists
                ? <button type="button" className="chip small-chip" onClick={() => onOpenNote(e.permalink)} title="Open this note">{e.title}</button>
                : <span>{e.title} (gone)</span>}
              <code style={{ whiteSpace: 'pre-wrap' }}>{e.line}</code>
              {e.source && <span>· source: {e.source}</span>}
              {e.merged && <span title="A near-duplicate by meaning, merged into this fact">· similar</span>}
            </div>
          ))}
        </div>
      ))}
      {data.facts.length === 0 && <div className="muted small">No supporting facts.</div>}
    </div>
  );
};

const KnowledgeDistilled: React.FC<{ base: string; onOpenNote: (permalink: string) => void }> = ({ base, onOpenNote }) => {
  const [st, setSt] = useState<Status | null>(null);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [factTotal, setFactTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [aboutOnly, setAboutOnly] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null); // "f12" | "s3" | "p"
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<number | null>(null);

  const [reload, setReload] = useState(0);       // bump → persona, scenarios, facts again
  const [poll, setPoll] = useState(0);           // bump → status again
  const wasRunning = useRef(false);

  // status (polled while a run is going; when it ends, the lists are reloaded)
  useEffect(() => {
    let live = true;
    api<Status>(`/api/knowledge/distill/status?${q(base)}`)
      .then(d => {
        if (!live) return;
        setSt(d);
        setErr(null);
        const now = d.job?.status === 'running' || !!d.running;
        if (wasRunning.current && !now) setReload(r => r + 1);
        wasRunning.current = now;
        if (now) timer.current = window.setTimeout(() => setPoll(n => n + 1), 1500);
      })
      .catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; if (timer.current) window.clearTimeout(timer.current); };
  }, [base, poll]);

  useEffect(() => {
    let live = true;
    Promise.all([
      api<{ persona: Persona | null }>(`/api/knowledge/persona?${q(base)}`),
      api<{ scenarios: Scenario[] }>(`/api/knowledge/scenarios?${q(base)}&limit=100`),
    ]).then(([p, s]) => {
      if (!live) return;
      setPersona(p.persona);
      setScenarios(s.scenarios || []);
      setErr(null);
    }).catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [base, reload]);

  useEffect(() => {
    let live = true;
    const t = window.setTimeout(() => {
      api<{ facts: Fact[]; total: number }>(`/api/knowledge/facts?${q(base)}&limit=200&q=${encodeURIComponent(query.trim())}${aboutOnly ? '&about_user=true' : ''}`)
        .then(r => { if (live) { setFacts(r.facts || []); setFactTotal(r.total || 0); setErr(null); } })
        .catch(e => { if (live) setErr((e as Error).message); });
    }, 250);
    return () => { live = false; window.clearTimeout(t); };
  }, [base, query, reload, aboutOnly]);

  const running = st?.job?.status === 'running' || !!st?.running;

  const stop = async () => {
    try { await api(`/api/knowledge/distill?${q(base)}`, { method: 'DELETE' }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setPoll(n => n + 1);
  };

  const run = async (full: boolean) => {
    setMsg(null);
    try {
      await api(`/api/knowledge/distill?${q(base)}${full ? '&full=true' : ''}`, { method: 'POST' });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    wasRunning.current = true;
    setPoll(n => n + 1);
  };

  const toggle = (key: string) => setOpen(o => (o === key ? null : key));
  const shown = facts;
  const job = st?.job;

  return (
    <div className="kb-tags-grid">
      <section className="settings-card">
        <h3>Distillation</h3>
        <p className="muted small">Facts from your notes, deduplicated (L1), grouped into scenarios (L2) and summed up as a persona (L3). Rebuilt from the notes at any time; nothing here changes a note.</p>
        {err && <div className="notice error">{err}</div>}
        {msg && <div className={`notice ${msg.ok ? '' : 'error'}`}>{msg.text}</div>}
        {st && (
          <>
            <div className="small" style={{ marginBottom: 6 }}>
              <b>{st.enabled ? 'Automatic' : 'Manual'}</b>
              <span className="muted">{st.enabled
                ? ` — after ${st.options.distill_every} changed notes or ${st.options.idle_minutes} min without changes; the agent sees the persona and matching facts first.`
                : ' — turn on in Settings → Features (“Distil facts, scenarios and a persona”) for background runs and the distilled agent context.'}</span>
            </div>
            <div className="kb-stats" style={{ padding: 0 }}>
              <span><b>{st.facts}</b> facts</span>
              <span><b>{st.scenarios}</b> scenarios</span>
              <span>persona <b>{st.persona_version ? `v${st.persona_version}` : '—'}</b></span>
              <span title="Notes changed since the last run"><b>{st.pending}</b> waiting</span>
              <span className="muted small">{st.last_run ? `last run ${st.last_run.replace('T', ' ')}` : 'never run'}</span>
            </div>
            {running && <p className="small">{job?.phase === 'distilling' && job.total ? `Distilling ${job.done}/${job.total} notes…` : 'Distilling…'}</p>}
            {job && job.status !== 'running' && (job.error || job.result?.message) && (
              <div className={`notice ${job.status === 'error' ? 'error' : ''}`} style={{ marginTop: 6 }}>{job.error || job.result?.message}</div>
            )}
            <div className="kb-buttons" style={{ marginTop: 8 }}>
              {running && <button type="button" className="ghost-btn" onClick={stop} title="Stop after the current batch; the next run continues">Stop</button>}
              <button type="button" className="primary-btn" disabled={running} onClick={() => run(false)} title="Read the notes changed since the last run">Distil now</button>
              <button type="button" className="ghost-btn" disabled={running} onClick={() => run(true)} title="Read every note again">Rebuild</button>
            </div>
          </>
        )}
      </section>

      <section className="settings-card">
        <h3>Persona {persona && <span className="muted small">v{persona.version} · <Conf value={persona.confidence} /></span>}</h3>
        {!persona || !persona.summary ? (
          <p className="muted small">No persona yet. It is built from facts about you: categories like <code>preference</code>, <code>person</code>, <code>decision</code>, <code>setting</code>, <code>habit</code>, <code>project</code>, or sentences such as “I prefer …”. Secrets are never included. Your own profile (<code>update_user_profile</code>) stays as it is.</p>
        ) : (
          <>
            <p className="small">{persona.summary}</p>
            {Object.entries(persona.sections).map(([sec, items]) => (
              <div key={sec} style={{ marginBottom: 6 }}>
                <div className="small"><b>{persona.titles[sec] || sec}</b></div>
                {items.map(it => (
                  <div key={it.id} className="small" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <Conf value={it.confidence} />
                    <button type="button" className="kb-link" style={{ background: 'none', border: 0, padding: 0, color: 'inherit', textAlign: 'left', cursor: 'pointer' }}
                      onClick={() => toggle(`f${it.id}`)} title="Show where this comes from">{it.text}</button>
                    {open === `f${it.id}` && <span className="muted small">▾</span>}
                  </div>
                ))}
                {items.map(it => open === `f${it.id}` && <DrillView key={`d${it.id}`} base={base} kind="fact" id={it.id} onOpenNote={onOpenNote} />)}
              </div>
            ))}
            <button type="button" className="chip small-chip" onClick={() => toggle('p')}>{open === 'p' ? 'Hide' : 'Show'} all sources</button>
            {open === 'p' && <DrillView base={base} kind="persona" id="latest" onOpenNote={onOpenNote} />}
          </>
        )}
      </section>

      <section className="settings-card">
        <h3>Scenarios <span className="muted small">{scenarios.length}</span></h3>
        <p className="muted small">Related facts: a note and the notes it links to, a tag, a name or a category.</p>
        {scenarios.length === 0 && <p className="muted small">No scenarios yet — they form when at least two facts are related.</p>}
        {scenarios.map(s => (
          <div key={s.id} style={{ marginBottom: 8 }}>
            <button type="button" className="kb-link" onClick={() => toggle(`s${s.id}`)}
              style={{ background: 'none', border: 0, padding: 0, color: 'inherit', textAlign: 'left', cursor: 'pointer', width: '100%' }}>
              <div className="small" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <b>{open === `s${s.id}` ? '▾' : '▸'} {s.title}</b>
                <span className="muted small">{s.size} facts · {s.notes} notes · by {s.grouped_by}</span>
                <span style={{ marginLeft: 'auto' }}><Conf value={s.confidence} /></span>
              </div>
              <div className="muted small">{s.summary}</div>
            </button>
            {open === `s${s.id}` && <DrillView base={base} kind="scenario" id={s.id} onOpenNote={onOpenNote} />}
          </div>
        ))}
      </section>

      <section className="settings-card" style={{ gridColumn: '1 / -1' }}>
        <h3>Facts <span className="muted small">{query.trim() ? `${shown.length} matching` : `${shown.length} of ${factTotal}`}</span></h3>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <input className="field-input" placeholder="Filter facts…" value={query} onChange={e => setQuery(e.target.value)} style={{ flex: 1, minWidth: 160 }} />
          <label className="kb-check small"><input type="checkbox" checked={aboutOnly} onChange={e => setAboutOnly(e.target.checked)} /> about you only</label>
        </div>
        {shown.length === 0 && <p className="muted small">{query.trim() ? 'No fact matches.' : 'No facts yet — press Distil now.'}</p>}
        {shown.map(f => (
          <div key={f.id}>
            <button type="button" onClick={() => toggle(`f${f.id}`)} title="Show the notes and lines this fact comes from"
              style={{ background: 'none', border: 0, padding: '3px 0', color: 'inherit', textAlign: 'left', cursor: 'pointer', width: '100%', display: 'flex', gap: 8, alignItems: 'center' }}>
              <Conf value={f.confidence} />
              <span className="kb-tag">{f.category}</span>
              <span className="small" style={{ flex: 1 }}>{f.display || f.text}</span>
              {f.instruction && <span className="muted small" title="Addressed to the assistant or a rule: never given to the agent as context">not used</span>}
              {f.conflicted && <span title="Contradicts another fact on its note (open the note to resolve)" style={{ color: '#d29922' }}>⚠</span>}
              <span className="muted small" title="Supporting notes">{f.support} note{f.support === 1 ? '' : 's'}</span>
            </button>
            {open === `f${f.id}` && <DrillView base={base} kind="fact" id={f.id} onOpenNote={onOpenNote} />}
          </div>
        ))}
      </section>
    </div>
  );
};

export default KnowledgeDistilled;
