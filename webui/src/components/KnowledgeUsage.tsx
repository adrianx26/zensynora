// Knowledge usage log and benchmarks (2026-10-27): which agent used which notes,
// and for what; numbers and charts about the knowledge base (inline SVG, no chart
// library — like the Costs page). Data: /api/knowledge/usage, /usage/stats,
// /benchmarks.
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

interface DayRow { day: string; context: number; tool: number; write: number }
interface AgentRow { agent: string; context: number; tool: number; write: number; total: number }
interface TopNote { permalink: string; title: string; count: number; last: number; last_agent: string; exists: boolean }
interface TopQuery { query: string; count: number; avg_results?: number; last: number; agent?: string }
interface Snapshot { day: string; notes: number; facts: number; links: number; tags: number }
interface Stats {
  enabled: boolean; days: number;
  accesses_7d: number; accesses_30d: number; writes_7d: number; writes_30d: number;
  per_day: DayRow[]; per_agent: AgentRow[]; top_notes: TopNote[]; top_queries: TopQuery[]; empty_queries: TopQuery[];
  searches: number; hits: number; hit_rate: number | null;
  latency: { avg: number | null; p95: number | null; n: number };
  latency_per_day: { day: string; avg: number | null; p95: number | null; n: number }[];
  notes_used: number; notes_total: number | null; never_used: number | null;
  never_used_sample: { permalink: string; title: string }[];
  snapshots: Snapshot[];
}
interface Bench {
  notes: number; facts: number; links: number; tags: number; categories: number; orphans: number; waiting_links: number;
  links_per_note: number; density: number; components: number; largest_component: number;
  hubs: { permalink: string; title: string; links: number }[];
  notes_by_month: { month: string; count: number }[];
  facts_by_category: { name: string; count: number }[];
  conflicts: number; index_bytes: number; files_bytes: number; files: number; took_ms: number;
  embeddings: { notes: number; embedded: number; waiting: number } | null;
  distill: { facts: number; scenarios: number; persona_versions: number } | null;
}
interface LogRow {
  id: number; ts: number; agent: string; channel: string | null; job: string | null; source: string | null; via: string | null;
  kind: string; action: string; query: string | null; notes: string[]; results: number | null;
  latency_ms: number | null; mode: string | null; ok: boolean; reason: string | null;
}
interface Log { rows: LogRow[]; total: number; agents: string[]; enabled: boolean }

const KIND_COLOR: Record<string, string> = { context: '#58a6ff', tool: '#3fb950', write: '#d29922' };
const KIND_LABEL: Record<string, string> = { context: 'automatic context', tool: 'tools', write: 'writes' };
const SERIES = ['#58a6ff', '#a371f7', '#3fb950', '#d29922'];
const PAGE = 25;

const fmt = (n: number | null | undefined) => new Intl.NumberFormat().format(n || 0);

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function ago(ts: number | null | undefined): string {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

const Tile: React.FC<{ label: string; value: React.ReactNode; sub?: React.ReactNode; title?: string }> = ({ label, value, sub, title }) => (
  <div className="cost-card" title={title}>
    <div className="cost-label">{label}</div>
    <div className="cost-value">{value}</div>
    {sub && <div className="muted small" style={{ marginTop: 4 }}>{sub}</div>}
  </div>
);

const W = 560, H = 150, PAD_L = 34, PAD_B = 18, PAD_T = 8;

function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const step = Math.pow(10, Math.floor(Math.log10(max)));
  const s = max / step > 5 ? step * 2 : max / step > 2 ? step : step / 2;
  const out: number[] = [];
  for (let v = 0; v <= max + 1e-9; v += s) out.push(Math.round(v * 100) / 100);
  return out;
}

const Axes: React.FC<{ max: number; labels: string[]; unit?: string }> = ({ max, labels, unit }) => {
  const iw = W - PAD_L, ih = H - PAD_B - PAD_T;
  const every = Math.max(1, Math.ceil(labels.length / 8));
  return (
    <g fontSize="9" fill="var(--text-secondary)">
      {ticks(max).map(t => {
        const y = PAD_T + ih - (max ? (t / max) * ih : 0);
        return (
          <g key={t}>
            <line x1={PAD_L} x2={W} y1={y} y2={y} stroke="var(--panel-border)" strokeWidth="1" />
            <text x={PAD_L - 4} y={y + 3} textAnchor="end">{t}{unit || ''}</text>
          </g>
        );
      })}
      {labels.map((l, i) => i % every === 0 && (
        <text key={i} x={PAD_L + (labels.length > 1 ? (i / (labels.length - 1)) : 0.5) * iw} y={H - 4} textAnchor="middle">{l}</text>
      ))}
    </g>
  );
};

const StackedBars: React.FC<{ rows: DayRow[] }> = ({ rows }) => {
  const kinds = ['context', 'tool', 'write'] as const;
  const max = Math.max(1, ...rows.map(r => r.context + r.tool + r.write));
  const top = ticks(max).slice(-1)[0] || max;
  const iw = W - PAD_L, ih = H - PAD_B - PAD_T;
  const bw = iw / Math.max(1, rows.length);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Knowledge accesses per day">
      <Axes max={top} labels={rows.map(r => r.day.slice(5))} />
      {rows.map((r, i) => {
        let y = PAD_T + ih;
        return (
          <g key={r.day}>
            <title>{`${r.day}: ${r.context} automatic, ${r.tool} tools, ${r.write} writes`}</title>
            {kinds.map(k => {
              const h = (r[k] / top) * ih;
              y -= h;
              return h > 0 ? <rect key={k} x={PAD_L + i * bw + bw * 0.15} y={y} width={bw * 0.7} height={h} fill={KIND_COLOR[k]} rx="1" /> : null;
            })}
          </g>
        );
      })}
    </svg>
  );
};

interface Series { name: string; values: (number | null)[]; color: string }

const Lines: React.FC<{ labels: string[]; series: Series[]; unit?: string; label: string }> = ({ labels, series, unit, label }) => {
  const max = Math.max(1, ...series.flatMap(s => s.values.map(v => v ?? 0)));
  const top = ticks(max).slice(-1)[0] || max;
  const iw = W - PAD_L, ih = H - PAD_B - PAD_T;
  const x = (i: number) => PAD_L + (labels.length > 1 ? i / (labels.length - 1) : 0.5) * iw;
  const y = (v: number) => PAD_T + ih - (v / top) * ih;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}>
      <Axes max={top} labels={labels} unit={unit} />
      {series.map(s => {
        const pts = s.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(' ');
        return (
          <g key={s.name}>
            <polyline fill="none" stroke={s.color} strokeWidth="1.8" points={pts} />
            {s.values.map((v, i) => v != null && (
              <circle key={i} cx={x(i)} cy={y(v)} r={labels.length > 40 ? 1.2 : 2.4} fill={s.color}>
                <title>{`${labels[i]} · ${s.name}: ${v}${unit || ''}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
    </svg>
  );
};

const Legend: React.FC<{ items: { name: string; color: string }[] }> = ({ items }) => (
  <div className="kb-legend" style={{ borderTop: 'none', padding: '4px 0 0' }}>
    {items.map(i => <span key={i.name} className="kb-legend-item"><span className="kb-swatch" style={{ background: i.color }} />{i.name}</span>)}
  </div>
);

const Bars: React.FC<{ rows: { name: string; count: number; title?: string; onClick?: () => void }[]; alt?: boolean; empty: string }> = ({ rows, alt, empty }) => {
  const max = Math.max(1, ...rows.map(r => r.count));
  if (!rows.length) return <span className="muted small">{empty}</span>;
  return (
    <>
      {rows.map(r => {
        const inner = (
          <>
            <span title={r.title || r.name}>{r.name}</span>
            <span className="kb-bar-track"><span className={`kb-bar-fill ${alt ? 'alt' : ''}`} style={{ width: `${(r.count / max) * 100}%` }} /></span>
            <b>{fmt(r.count)}</b>
          </>
        );
        return r.onClick
          ? <button type="button" key={r.name} className="kb-bar" onClick={r.onClick}>{inner}</button>
          : <div key={r.name} className="kb-bar">{inner}</div>;
      })}
    </>
  );
};

const AgentBars: React.FC<{ rows: AgentRow[]; onPick: (a: string) => void }> = ({ rows, onPick }) => {
  const max = Math.max(1, ...rows.map(r => r.total));
  if (!rows.length) return <span className="muted small">No agent has used this knowledge base in this period.</span>;
  return (
    <>
      {rows.map(r => (
        <button type="button" key={r.agent} className="kb-bar" onClick={() => onPick(r.agent)}
          title={`${r.context} automatic, ${r.tool} tool calls, ${r.write} writes — click to filter the log`}>
          <span>@{r.agent || '(no agent)'}</span>
          <span className="kb-bar-track" style={{ display: 'flex' }}>
            {(['context', 'tool', 'write'] as const).map(k => r[k] > 0 &&
              <span key={k} style={{ width: `${(r[k] / max) * 100}%`, background: KIND_COLOR[k], height: '100%' }} />)}
          </span>
          <b>{fmt(r.total)}</b>
        </button>
      ))}
    </>
  );
};

const KIND_OPTIONS: [string, string][] = [
  ['', 'all kinds'], ['context', 'automatic context'], ['tool', 'tool calls'], ['write', 'writes'],
  ['search_knowledge', 'search_knowledge'], ['read_knowledge', 'read_knowledge'], ['extraction', 'extraction'],
  ['session_summary', 'session summary'], ['assimilate', 'assimilate'],
];

function what(r: LogRow): string {
  if (r.kind === 'context') return 'auto-context';
  return r.action;
}

const KnowledgeUsage: React.FC<{ base: string; onOpenNote: (permalink: string) => void; initialQuery?: string }> = ({ base, onOpenNote, initialQuery = '' }) => {
  const [days, setDays] = useState(30);
  const [stats, setStats] = useState<Stats | null>(null);
  const [bench, setBench] = useState<Bench | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [log, setLog] = useState<Log | null>(null);
  const [agent, setAgent] = useState('');
  const [kind, setKind] = useState('');
  const [text, setText] = useState(initialQuery);
  const [q, setQ] = useState(initialQuery);
  const [page, setPage] = useState(0);
  const [reload, setReload] = useState(0);

  const b = `base=${encodeURIComponent(base)}`;

  useEffect(() => {
    let live = true;
    Promise.all([api<Stats>(`/api/knowledge/usage/stats?${b}&days=${days}`), api<Bench>(`/api/knowledge/benchmarks?${b}`)])
      .then(([s, bm]) => { if (live) { setStats(s); setBench(bm); setErr(null); } })
      .catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [b, days, reload]);

  // debounce the text filter
  useEffect(() => {
    const t = window.setTimeout(() => { setQ(text.trim()); setPage(0); }, 300);
    return () => window.clearTimeout(t);
  }, [text]);

  useEffect(() => {
    let live = true;
    const p = new URLSearchParams({ base, limit: String(PAGE), offset: String(page * PAGE) });
    if (agent) p.set('agent', agent);
    if (kind) p.set('kind', kind);
    if (q) p.set('q', q);
    api<Log>(`/api/knowledge/usage?${p.toString()}`)
      .then(l => { if (live) setLog(l); })
      .catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [base, agent, kind, q, page, reload]);

  const pickAgent = useCallback((a: string) => { setAgent(a); setPage(0); }, []);
  const filterText = (t: string) => { setText(t); setQ(t); setPage(0); };

  if (err && !stats) return <div className="notice error">Could not load the usage log: {err}</div>;
  if (!stats || !bench) return <div className="kb-empty">Loading…</div>;

  const hit = stats.hit_rate == null ? '—' : `${Math.round(stats.hit_rate * 100)} %`;
  const snaps = stats.snapshots || [];
  // growth: daily snapshots, or (until there are two) notes created per month, cumulative
  let growthLabels: string[], growth: Series[];
  if (snaps.length >= 2) {
    growthLabels = snaps.map(s => s.day.slice(5));
    growth = [
      { name: 'notes', values: snaps.map(s => s.notes), color: SERIES[0] },
      { name: 'facts', values: snaps.map(s => s.facts), color: SERIES[1] },
      { name: 'links', values: snaps.map(s => s.links), color: SERIES[2] },
    ];
  } else {
    let sum = 0;
    growthLabels = bench.notes_by_month.map(m => m.month);
    growth = [{ name: 'notes (by creation month)', values: bench.notes_by_month.map(m => (sum += m.count)), color: SERIES[0] }];
  }
  const lat = stats.latency_per_day;
  const totalPages = log ? Math.max(1, Math.ceil(log.total / PAGE)) : 1;

  return (
    <div className="kb-tags-grid kb-usage" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>
      {!stats.enabled && (
        <div className="notice" style={{ gridColumn: '1 / -1' }}>
          The usage log is off (Settings → Features → <i>Knowledge usage log</i>, or the knowledge base is off) — nothing new is recorded.
        </div>
      )}
      <section className="cost-summary" style={{ gridColumn: '1 / -1' }}>
        <Tile label="Accesses · 7 days" value={fmt(stats.accesses_7d)} sub={`${fmt(stats.accesses_30d)} in 30 days · ${fmt(stats.writes_30d)} writes`}
          title="Automatic context searches and knowledge tool calls by agents" />
        <Tile label="Hit rate" value={hit} sub={`${fmt(stats.hits)} of ${fmt(stats.searches)} searches found notes`}
          title={`Searches (automatic + search_knowledge) with at least one note, last ${stats.days} days`} />
        <Tile label="Latency" value={stats.latency.avg == null ? '—' : `${stats.latency.avg} ms`}
          sub={stats.latency.p95 == null ? 'no searches yet' : `p95 ${stats.latency.p95} ms · ${fmt(stats.latency.n)} calls`} />
        <Tile label="Notes used" value={`${fmt(stats.notes_used)} / ${fmt(stats.notes_total ?? bench.notes)}`}
          sub={`${fmt(stats.never_used)} never used`} title={`Distinct notes agents read in the last ${stats.days} days / all notes`} />
        <Tile label="Knowledge base" value={`${fmt(bench.notes)} notes`}
          sub={`${fmt(bench.facts)} facts · ${fmt(bench.links)} links · ${bytes(bench.files_bytes)} + index ${bytes(bench.index_bytes)}`} />
      </section>

      <section className="settings-card" style={{ gridColumn: '1 / -1' }}>
        <h3>Accesses per day
          <select className="field-input" style={{ width: 'auto', marginLeft: 'auto', padding: '3px 8px', fontSize: '0.82rem' }}
            value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Period">
            <option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
          </select>
          <button type="button" className="ghost-btn" style={{ padding: '3px 10px', fontSize: '0.82rem' }} onClick={() => setReload(r => r + 1)}>↻</button>
        </h3>
        <StackedBars rows={stats.per_day} />
        <Legend items={Object.keys(KIND_COLOR).map(k => ({ name: KIND_LABEL[k], color: KIND_COLOR[k] }))} />
      </section>

      <section className="settings-card">
        <h3>By agent</h3>
        <p className="muted small">Click an agent to filter the log below.</p>
        <AgentBars rows={stats.per_agent} onPick={pickAgent} />
      </section>

      <section className="settings-card">
        <h3>Growth</h3>
        <p className="muted small">{snaps.length >= 2 ? 'Daily snapshots of the counts.' : 'Notes by the month they were created (daily snapshots start today).'}</p>
        {growthLabels.length ? <Lines labels={growthLabels} series={growth} label="Knowledge base growth" /> : <span className="muted small">No notes yet.</span>}
        <Legend items={growth.map(s => ({ name: s.name, color: s.color }))} />
      </section>

      <section className="settings-card">
        <h3>Search latency</h3>
        {stats.latency.n
          ? <Lines labels={lat.map(d => d.day.slice(5))} unit=" ms" label="Search latency per day"
              series={[{ name: 'average', values: lat.map(d => d.avg), color: SERIES[0] }, { name: 'p95', values: lat.map(d => d.p95), color: SERIES[3] }]} />
          : <span className="muted small">No searches in this period.</span>}
        <Legend items={[{ name: 'average', color: SERIES[0] }, { name: 'p95', color: SERIES[3] }]} />
      </section>

      <section className="settings-card">
        <h3>Facts by category <span className="muted small">{bench.categories} categories</span></h3>
        <Bars rows={bench.facts_by_category} alt empty="No facts yet." />
      </section>

      <section className="settings-card">
        <h3>Structure</h3>
        <div className="kb-bench" style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '3px 12px', fontSize: '0.86rem' }}>
          <span>Tags</span><b>{fmt(bench.tags)}</b>
          <span title="Notes without any link in or out">Unlinked notes</span><b>{fmt(bench.orphans)}</b>
          <span title="Links to notes that do not exist yet">Waiting links</span><b>{fmt(bench.waiting_links)}</b>
          <span title="Links in + out, per note">Links per note</span><b>{bench.links_per_note}</b>
          <span title="Linked pairs / possible pairs">Graph density</span><b>{(bench.density * 100).toFixed(2)} %</b>
          <span title="Groups of notes connected by links (an unlinked note is a group of one)">Connected groups</span><b>{fmt(bench.components)}</b>
          <span>Largest group</span><b>{fmt(bench.largest_component)} notes</b>
          <span>Open conflicts</span><b>{fmt(bench.conflicts)}</b>
          {bench.embeddings && <><span>Embedded (semantic search)</span><b>{fmt(bench.embeddings.embedded)} / {fmt(bench.embeddings.notes)}</b></>}
          {bench.distill && <><span>Distilled facts · scenarios</span><b>{fmt(bench.distill.facts)} · {fmt(bench.distill.scenarios)}</b></>}
        </div>
        <h4>Most linked notes</h4>
        <Bars rows={bench.hubs.map(h => ({ name: h.title, count: h.links, onClick: () => onOpenNote(h.permalink) }))} empty="No links yet." />
        <p className="muted small">Computed in {bench.took_ms} ms.</p>
      </section>

      <section className="settings-card">
        <h3>Notes used most</h3>
        <Bars rows={stats.top_notes.map(n => ({ name: n.title, count: n.count, title: `${n.title} — last @${n.last_agent || '?'} ${ago(n.last)}`,
          onClick: n.exists ? () => onOpenNote(n.permalink) : undefined }))} empty="No note was used in this period." />
        {stats.never_used ? (
          <>
            <h4>Never used <span className="muted">{fmt(stats.never_used)}</span></h4>
            <div className="kb-cloud">
              {stats.never_used_sample.map(n => (
                <button type="button" key={n.permalink} className="chip small-chip" onClick={() => onOpenNote(n.permalink)}>{n.title}</button>
              ))}
            </div>
          </>
        ) : null}
      </section>

      <section className="settings-card">
        <h3>Top queries</h3>
        <Bars rows={stats.top_queries.map(t => ({ name: t.query, count: t.count, title: `${t.query} — ${t.avg_results} results on average`,
          onClick: () => filterText(t.query) }))} empty="No searches in this period." />
        <h4 title="Searches that found no note — topics the knowledge base is missing">Found nothing <span className="muted">{stats.empty_queries.length}</span></h4>
        {stats.empty_queries.length === 0 && <span className="muted small">Every search found something.</span>}
        {stats.empty_queries.map(t => (
          <button type="button" key={t.query} className="kb-link" onClick={() => filterText(t.query)}
            title={`Last: @${t.agent || '?'} ${ago(t.last)}`}>
            {t.query} <span className="muted small">×{t.count} · {ago(t.last)}</span>
          </button>
        ))}
      </section>

      <section className="settings-card" style={{ gridColumn: '1 / -1' }}>
        <h3>Log <span className="muted small">{fmt(log?.total)} records</span></h3>
        <div className="kb-graph-controls" style={{ border: 'none', padding: 0 }}>
          <label>Agent
            <select className="field-input" value={agent} onChange={e => pickAgent(e.target.value)}>
              <option value="">all</option>
              {(log?.agents || []).map(a => <option key={a} value={a}>{a ? `@${a}` : '(no agent)'}</option>)}
            </select>
          </label>
          <label>Kind
            <select className="field-input" value={kind} onChange={e => { setKind(e.target.value); setPage(0); }}>
              {KIND_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <input className="field-input" style={{ width: 220, padding: '4px 8px', fontSize: '0.82rem' }} placeholder="Query, note or reason…"
            value={text} onChange={e => setText(e.target.value)} />
          {(agent || kind || q) && <button type="button" className="chip small-chip" onClick={() => { setAgent(''); setKind(''); filterText(''); }}>✕ Clear</button>}
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="cost-table kb-usage-log">
            <thead><tr><th>When</th><th>Agent</th><th>Kind</th><th>Query / note</th><th>Results</th><th>For</th></tr></thead>
            <tbody>
              {(log?.rows || []).map(r => (
                <tr key={r.id}>
                  <td className="muted small" title={new Date(r.ts * 1000).toLocaleString()} style={{ whiteSpace: 'nowrap' }}>{ago(r.ts)}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>@{r.agent || '?'}
                    {r.via && <div className="muted small">via @{r.via}</div>}
                    {r.job && <div className="muted small">job {r.job}</div>}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span className="kb-swatch" style={{ background: KIND_COLOR[r.kind] || '#8b949e', marginRight: 6 }} />{what(r)}
                    {r.mode && <div className="muted small">{r.mode}</div>}</td>
                  <td style={{ maxWidth: 320 }}>
                    {r.query && <div style={{ wordBreak: 'break-word' }}>{r.query}</div>}
                    <div className="kb-tags" style={{ marginTop: 2 }}>
                      {r.notes.slice(0, 6).map(p => <button type="button" key={p} className="kb-tag" style={{ cursor: 'pointer', background: 'none' }} onClick={() => onOpenNote(p)}>{p}</button>)}
                      {r.notes.length > 6 && <span className="muted small">+{r.notes.length - 6}</span>}
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{r.ok ? (r.results ?? '—') : <span style={{ color: 'var(--danger-color)' }}>error</span>}
                    {r.latency_ms != null && <div className="muted small">{r.latency_ms} ms</div>}</td>
                  <td className="small" style={{ maxWidth: 340, wordBreak: 'break-word' }}>
                    {r.reason ? <><span className="muted">{r.source === 'job' ? 'job: ' : r.source === 'delegation' ? 'task: ' : 'for: '}</span>{r.reason}</> : <span className="muted">—</span>}</td>
                </tr>
              ))}
              {log && log.rows.length === 0 && (
                <tr><td colSpan={6} className="muted">{log.total === 0 && !agent && !kind && !q
                  ? 'Nothing recorded yet — agents appear here when they search, read or write the knowledge base.'
                  : 'No records match the filters.'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {log && log.total > PAGE && (
          <div className="kb-buttons" style={{ alignItems: 'center', marginTop: 8 }}>
            <button type="button" className="ghost-btn" disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹ Newer</button>
            <span className="muted small">Page {page + 1} of {totalPages}</span>
            <button type="button" className="ghost-btn" disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)}>Older ›</button>
          </div>
        )}
      </section>
    </div>
  );
};

export default KnowledgeUsage;

// "Used by agents: N times (last: @agent, 2 h ago)" for the open note (side panel).
interface NoteUsage { count: number; writes: number; last: number | null; last_agent: string | null; last_action: string | null; enabled: boolean }

export const NoteUsageLine: React.FC<{ base: string; permalink: string; onShow?: () => void }> = ({ base, permalink, onShow }) => {
  const [u, setU] = useState<NoteUsage | null>(null);
  useEffect(() => {
    let live = true;
    api<NoteUsage>(`/api/knowledge/usage/note/${encodeURIComponent(permalink)}?base=${encodeURIComponent(base)}`)
      .then(d => { if (live) setU(d); })
      .catch(() => { if (live) setU(null); });
    return () => { live = false; };
  }, [base, permalink]);
  if (!u || (!u.enabled && !u.count)) return null;
  const text = u.count
    ? <>Used by agents: <b>{u.count}</b> time{u.count === 1 ? '' : 's'} (last: @{u.last_agent || '?'}{u.last_action ? ` · ${u.last_action}` : ''}, {ago(u.last)})</>
    : <>Not used by agents yet</>;
  return (
    <div className="muted small kb-note-usage">
      {onShow && u.count ? <button type="button" className="kb-sort" onClick={onShow} title="Show these accesses in the usage log">{text}</button> : text}
      {u.writes > 0 && <span> · written {u.writes}×</span>}
    </div>
  );
};
