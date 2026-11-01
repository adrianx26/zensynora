// Knowledge → Overview (2026-11-01): the knowledge base on one screen — size and
// change, growth by day / week / month (by note date), hit / miss of knowledge
// searches and of the response cache, groups of linked notes (new and growing),
// and what needs attention. Data: GET /api/knowledge/overview.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { Columns, HBars, SegBar, StatTile, TableToggle, type Row } from './viz';
import { downloadPanel, fmt, pct } from '../vizUtils';

interface Growth { day: string; notes: number; facts: number; links: number; total_notes: number; total_facts: number; total_links: number }
interface Group { size: number; hub: string; hub_permalink: string; first: string; new_notes: number }
interface Attention { id: string; label: string; count: number; share: number; hint: string; examples: { permalink: string; title: string }[] }
interface Overview {
  totals: { notes: number; facts: number; links: number; tags: number; categories: number };
  change: Record<'7d' | '30d', { notes: number; facts: number; links: number }>;
  growth: Growth[];
  groups: { count: number; largest: number; sizes: number[]; top: Group[]; new: Group[]; growing: Group[]; new_count?: number; growing_count?: number };
  attention: Attention[];
  usage: { enabled?: boolean; searches?: number; hits?: number; misses?: number; hit_rate?: number | null; accesses_7d?: number; accesses_30d?: number; notes_used?: number };
  cache: { active: boolean; hits: number; misses: number; entries: number };
  error?: string | null;
}

type Span = 'day' | 'week' | 'month';

function weekKey(day: string): string {
  const d = new Date(`${day}T12:00:00`);
  const monday = new Date(d);
  monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return monday.toISOString().slice(0, 10);
}

function group(rows: Growth[], span: Span): Row[] {
  const last = span === 'day' ? rows.slice(-60) : rows;
  const out = new Map<string, Row>();
  for (const r of last) {
    const key = span === 'day' ? r.day : span === 'week' ? weekKey(r.day) : r.day.slice(0, 7);
    const label = span === 'day' ? r.day.slice(5) : span === 'week' ? `wk ${key.slice(5)}` : key;
    const o = out.get(key) || { label, notes: 0, facts: 0, links: 0 };
    o.notes = Number(o.notes) + r.notes;
    o.facts = Number(o.facts) + r.facts;
    o.links = Number(o.links) + r.links;
    out.set(key, o);
  }
  return [...out.values()];
}

const SERIES = {
  notes: { key: 'notes', label: 'Notes added', color: 'var(--viz-s1)' },
  facts: { key: 'facts', label: 'Facts added', color: 'var(--viz-s2)' },
  links: { key: 'links', label: 'Links added', color: 'var(--viz-s3)' },
};

const KnowledgeOverview: React.FC<{ base: string; onOpenNote: (permalink: string) => void }> = ({ base, onOpenNote }) => {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [span, setSpan] = useState<Span>('day');
  const panel = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<Overview>(`/api/knowledge/overview?base=${encodeURIComponent(base)}`));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [base]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- data load
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => (data ? group(data.growth, span) : []), [data, span]);
  if (error) return <div className="notice error">{error}</div>;
  if (!data) return <div className="muted">Loading…</div>;
  const t = data.totals;
  const spark = (k: 'total_notes' | 'total_facts' | 'total_links') => data.growth.slice(-30).map(g => g[k]);
  const att = [...data.attention].sort((a, b) => b.count - a.count);
  const g = data.groups;
  const u = data.usage;

  return (
    <div className="viz settings-card" ref={panel}>
      <div className="viz-head">
        <h3>Knowledge base at a glance</h3>
        <span className="row-actions" data-no-export="1">
          <button className="ghost-btn small-btn" onClick={load}>↻ Refresh</button>
          <button className="ghost-btn small-btn" onClick={() => downloadPanel(panel.current, `knowledge-${base}`)}>⬇ Download as image</button>
        </span>
      </div>

      <div className="viz-tiles">
        <StatTile label="Notes" value={fmt(t.notes)} delta={data.change['7d'].notes} deltaLabel="this week" spark={spark('total_notes')} />
        <StatTile label="Facts" value={fmt(t.facts)} delta={data.change['7d'].facts} deltaLabel="this week" spark={spark('total_facts')} />
        <StatTile label="Links" value={fmt(t.links)} delta={data.change['7d'].links} deltaLabel="this week" spark={spark('total_links')} />
        <StatTile label="Tags · fact categories" value={`${fmt(t.tags)} · ${fmt(t.categories)}`} />
        <StatTile label="Groups of linked notes" value={fmt(g.count)}
          sub={<>largest {fmt(g.largest)} · {g.new_count || 0} new, {g.growing_count || 0} growing this week</>} />
        <StatTile label="Last 30 days" value={`+${fmt(data.change['30d'].notes)}`} sub={<>notes · +{fmt(data.change['30d'].facts)} facts · +{fmt(data.change['30d'].links)} links</>} />
      </div>

      <div className="viz-block" style={{ marginTop: 14 }}>
        <div className="viz-head">
          <h4>Growth <span className="muted small">by note date{span === 'day' ? ' · last 60 days' : ''}</span></h4>
          <span className="viz-toggle" data-no-export="1">
            {(['day', 'week', 'month'] as Span[]).map(s => (
              <button key={s} className={`chip ${span === s ? 'active' : ''}`} onClick={() => setSpan(s)}>{s === 'day' ? 'Days' : s === 'week' ? 'Weeks' : 'Months'}</button>
            ))}
          </span>
        </div>
        {rows.length === 0 ? <div className="muted small">No notes yet.</div> : (
          <div className="viz-grid2">
            {(['notes', 'facts', 'links'] as const).map(k => (
              <div key={k}>
                <div className="muted small">{SERIES[k].label} per {span}</div>
                <Columns rows={rows} series={[SERIES[k]]} ariaLabel={`${SERIES[k].label} per ${span}`} height={130} />
              </div>
            ))}
          </div>
        )}
        <TableToggle rows={rows} series={[SERIES.notes, SERIES.facts, SERIES.links]} first={span === 'day' ? 'Day' : span === 'week' ? 'Week of' : 'Month'} />
      </div>

      <div className="viz-grid2">
        <div className="viz-block">
          <h4>Knowledge searches · last 30 days</h4>
          <div className="muted small">
            {u.enabled === false ? 'The usage log is off (Settings → Features).' : `${fmt(u.searches)} searches by agents · hit rate ${pct(u.hit_rate)}`}
          </div>
          <SegBar empty="No searches logged yet."
            parts={[{ label: 'Found something', value: u.hits || 0, color: 'var(--viz-s1)' },
              { label: 'Found nothing', value: u.misses || 0, color: 'var(--viz-rest)' }]} />
          <div className="muted small" style={{ marginTop: 6 }}>
            {fmt(u.accesses_7d)} reads this week · {fmt(u.accesses_30d)} in 30 days · {fmt(u.notes_used)} notes used
          </div>
        </div>
        <div className="viz-block">
          <h4>Response cache · since ZenSynora started</h4>
          <div className="muted small">Answers reused for identical or similar questions ({fmt(data.cache.entries)} stored)</div>
          <SegBar empty={data.cache.active ? 'No lookups yet.' : 'The cache has not been used since the start.'}
            parts={[{ label: 'Hit', value: data.cache.hits, color: 'var(--viz-s1)' },
              { label: 'Miss', value: data.cache.misses, color: 'var(--viz-rest)' }]} />
        </div>
      </div>

      <div className="viz-grid2">
        <div className="viz-block">
          <h4>Groups of linked notes <span className="muted small">largest first · click to open the main note</span></h4>
          {g.count === 0 ? <div className="muted small">No linked notes yet.</div> : (
            <HBars stacked rows={(g.top || []).map(x => ({
              key: x.hub_permalink, label: x.hub, value: x.size, onClick: () => onOpenNote(x.hub_permalink),
              note: x.new_notes ? `+${x.new_notes} this week` : undefined,
            }))} valueText={v => `${fmt(v)} notes`} />
          )}
        </div>
        <div className="viz-block">
          <h4>New and growing this week</h4>
          {g.new.length === 0 && g.growing.length === 0 && <div className="muted small">No new notes in groups this week.</div>}
          <ul className="plain-list">
            {g.new.map(x => (
              <li key={`n-${x.hub_permalink}`}><span className="badge">new</span> <a href="#" onClick={e => { e.preventDefault(); onOpenNote(x.hub_permalink); }}>{x.hub}</a> <span className="muted small">{x.size} notes · since {x.first}</span></li>
            ))}
            {g.growing.map(x => (
              <li key={`g-${x.hub_permalink}`}><span className="badge">+{x.new_notes}</span> <a href="#" onClick={e => { e.preventDefault(); onOpenNote(x.hub_permalink); }}>{x.hub}</a> <span className="muted small">{x.size} notes</span></li>
            ))}
          </ul>
        </div>
      </div>

      <div className="viz-block" style={{ marginTop: 14 }}>
        <h4>Needs attention or more data <span className="muted small">bar = share of all notes</span></h4>
        <HBars max={Math.max(1, t.notes)}
          rows={att.map(a => ({
            key: a.id,
            label: <>{a.count > 0 ? '⚠ ' : '✓ '}{a.label}</>,
            value: a.count,
            color: a.count === 0 ? 'var(--viz-good)' : a.share >= 0.25 ? 'var(--viz-serious)' : 'var(--viz-warning)',
            note: a.count > 0 ? (
              <>{a.hint}{a.examples.length > 0 && <> e.g. {a.examples.map((x, i) => (
                <span key={i}>{i > 0 && ', '}{x.permalink
                  ? <a href="#" onClick={e => { e.preventDefault(); onOpenNote(x.permalink); }}>{x.title}</a>
                  : <i>{x.title}</i>}</span>))}</>}</>
            ) : undefined,
          }))}
          valueText={v => `${fmt(v)}${t.notes ? ` · ${pct(v / t.notes)}` : ''}`} />
      </div>
    </div>
  );
};

export default KnowledgeOverview;
