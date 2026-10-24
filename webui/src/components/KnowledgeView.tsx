// Knowledge base explorer (Web UI): a live graph of the notes and their links,
// a sortable list of notes, tags / fact categories / link types, and a side
// panel to read, edit, create or trash a note. Data: /api/knowledge/*.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import KnowledgeGraph, { type GEdge, type GNode, type GraphHandle } from './KnowledgeGraph';
import KnowledgeImport from './KnowledgeImport';
import { CategoriesCard, CategoryMergePanel, ConflictsSection, DuplicatesPanel, type Conflict } from './KnowledgeQuality';
import { SearchModePicker, SemanticCard } from './KnowledgeSemantic';
import { useSemanticStatus, type SearchMode } from '../knowledgeSemantic';

interface Count { name: string; count: number }
interface GraphNode extends GNode { updated: string | null; size: number; categories?: string[] }
interface GraphData {
  base: string;
  nodes: GraphNode[];
  edges: GEdge[];
  stats: { notes: number; observations: number; relations: number; missing: number; orphans: number; tags: number };
  tags: Count[];
  categories: Count[];
  relation_types: Count[];
  truncated: boolean;
}
interface NoteDetail {
  permalink: string;
  title: string;
  tags: string[];
  content: string;
  body: string;
  observations: { category: string; content: string; tags: string[]; source?: string | null }[];
  links: { type: string; target: string; label: string; exists: boolean }[];
  backlinks: { type: string; source: string; label: string }[];
  file: string;
  size: number;
  modified: string;
  conflicts?: Conflict[]; // 2026-10-24 (KB phase 2)
}

type Tab = 'graph' | 'notes' | 'tags';
type ColorBy = 'tag' | 'links' | 'none';

// Categorical palette (works on the dark and light themes).
const PALETTE = ['#58a6ff', '#3fb950', '#d29922', '#f778ba', '#a371f7', '#39c5cf', '#ff7b72', '#e3b341', '#7ee787', '#79c0ff'];
const BASE_KEY = 'zensynora_kb_base';
const MODE_KEY = 'zensynora_kb_search_mode'; // KB phase 3: words / meaning / both

function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function linkColor(degree: number): string {
  if (degree === 0) return '#8b949e';
  if (degree <= 2) return '#39c5cf';
  if (degree <= 5) return '#58a6ff';
  return '#a371f7';
}

function store(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* blocked */ } }
function stored(key: string): string | null { try { return localStorage.getItem(key); } catch { return null; } }

const KnowledgeView: React.FC = () => {
  const [bases, setBases] = useState<{ name: string; notes: number }[]>([]);
  const [base, setBase] = useState<string>(() => stored(BASE_KEY) || 'default');
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>('graph');
  const [selected, setSelected] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);  // KB phase 5: import dialog
  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState<{ name: string; tags: string; content: string } | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  // 2026-10-24 (KB phase 2): quality tools in the side panel
  const [side, setSide] = useState<{ type: 'dups' } | { type: 'catmerge'; from: string; to: string } | null>(null);
  const [catFilter, setCatFilter] = useState<string[]>([]);
  // filters
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Set<string> | null>(null);
  // 2026-10-24 (KB phase 3): semantic search mode + status
  const [mode, setMode] = useState<SearchMode>(() => (stored(MODE_KEY) as SearchMode) || 'auto');
  const [modeNote, setModeNote] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [relFilter, setRelFilter] = useState<string[]>([]);
  const [hideMissing, setHideMissing] = useState(false);
  const [hideOrphans, setHideOrphans] = useState(false);
  const [depth, setDepth] = useState(0); // 0 = whole graph, else hops around the selected note
  const [colorBy, setColorBy] = useState<ColorBy>('tag');
  const [edgeLabels, setEdgeLabels] = useState(false);
  const [paused, setPaused] = useState(false);
  const [sort, setSort] = useState<{ key: 'title' | 'observations' | 'degree' | 'updated'; dir: 1 | -1 }>({ key: 'degree', dir: -1 });
  const graphRef = useRef<GraphHandle>(null);

  const loadBases = useCallback(async () => {
    try {
      const r = await api<{ bases: { name: string; notes: number }[] }>('/api/knowledge/bases');
      setBases(r.bases);
      if (!r.bases.some(b => b.name === base)) setBase('default');
    } catch { /* shown by the graph load */ }
  }, [base]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<GraphData>(`/api/knowledge/graph?base=${encodeURIComponent(base)}`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [base]);

  useEffect(() => { loadBases(); }, [loadBases]);
  const semantic = useSemanticStatus(base, data);
  const searchMode: SearchMode = semantic.st?.enabled ? mode : 'fts';
  useEffect(() => { store(BASE_KEY, base); setSelected(null); setTagFilter([]); setRelFilter([]); setCatFilter([]); setSide(null); load(); }, [base, load]);

  const loadDetail = useCallback(async (id: string) => {
    setDetailErr(null);
    try {
      setDetail(await api<NoteDetail>(`/api/knowledge/note/${encodeURIComponent(id)}?base=${encodeURIComponent(base)}`));
    } catch (e) {
      setDetail(null);
      setDetailErr((e as Error).message);
    }
  }, [base]);

  useEffect(() => {
    setEditing(null);
    if (!selected || selected.startsWith('missing:')) { setDetail(null); setDetailErr(null); return; }
    loadDetail(selected);
  }, [selected, loadDetail]);

  // full-text search (debounced)
  useEffect(() => {
    const q = query.trim();
    if (!q) { setHits(null); setModeNote(null); return; }
    const t = window.setTimeout(async () => {
      try {
        const r = await api<{ results: string[]; note?: string | null }>(`/api/knowledge/search?base=${encodeURIComponent(base)}&q=${encodeURIComponent(q)}&mode=${searchMode}`);
        setModeNote(searchMode === 'fts' ? null : r.note || null);
        const ql = q.toLowerCase();
        const s = new Set(r.results);
        data?.nodes.forEach(n => { if (n.title.toLowerCase().includes(ql) || n.id.includes(ql)) s.add(n.id); });
        setHits(s);
      } catch { setHits(new Set()); }
    }, 250);
    return () => window.clearTimeout(t);
  }, [query, base, data, searchMode]);

  // ── Filtered graph ──
  const view = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], edges: [] as GEdge[] };
    let edges = data.edges.filter(e => !relFilter.length || relFilter.includes(e.type));
    let keep = new Set(data.nodes.map(n => n.id));
    if (tagFilter.length || catFilter.length) {
      const tagged = new Set(data.nodes.filter(n => (!tagFilter.length || n.tags.some(t => tagFilter.includes(t)))
        && (!catFilter.length || (n.categories || []).some(c => catFilter.includes(c)))).map(n => n.id));
      // keep tagged notes and what they link to directly
      keep = new Set(tagged);
      edges.forEach(e => { if (tagged.has(e.source)) keep.add(e.target); if (tagged.has(e.target)) keep.add(e.source); });
    }
    if (hits) {
      const h = new Set(hits);
      edges.forEach(e => { if (hits.has(e.source)) h.add(e.target); if (hits.has(e.target)) h.add(e.source); });
      keep = new Set([...keep].filter(id => h.has(id)));
    }
    if (depth > 0 && selected && keep.has(selected)) {
      let frontier = new Set([selected]);
      const near = new Set([selected]);
      for (let d = 0; d < depth; d++) {
        const next = new Set<string>();
        edges.forEach(e => {
          if (frontier.has(e.source) && !near.has(e.target)) next.add(e.target);
          if (frontier.has(e.target) && !near.has(e.source)) next.add(e.source);
        });
        next.forEach(x => near.add(x));
        frontier = next;
      }
      keep = new Set([...keep].filter(id => near.has(id)));
    }
    let nodes = data.nodes.filter(n => keep.has(n.id) && !(hideMissing && n.missing));
    const ids = new Set(nodes.map(n => n.id));
    edges = edges.filter(e => ids.has(e.source) && ids.has(e.target));
    if (hideOrphans) {
      const linked = new Set<string>();
      edges.forEach(e => { linked.add(e.source); linked.add(e.target); });
      nodes = nodes.filter(n => linked.has(n.id) || n.id === selected);
    }
    return { nodes, edges };
  }, [data, tagFilter, catFilter, relFilter, hits, depth, selected, hideMissing, hideOrphans]);

  const tagColor = useMemo(() => {
    const m = new Map<string, string>();
    (data?.tags || []).forEach((t, i) => m.set(t.name, i < PALETTE.length ? PALETTE[i] : hashColor(t.name)));
    return m;
  }, [data]);

  const tagRank = useMemo(() => new Map((data?.tags || []).map((t, i) => [t.name, i])), [data]);

  const colorOf = useCallback((n: GNode) => {
    if (colorBy === 'links') return linkColor(n.degree);
    if (colorBy === 'none') return '#58a6ff';
    // the note's most used tag (the legend lists the most used tags first)
    const pool = tagFilter.length ? n.tags.filter(x => tagFilter.includes(x)) : n.tags;
    const t = [...(pool.length ? pool : n.tags)].sort((x, y) => (tagRank.get(x) ?? 1e9) - (tagRank.get(y) ?? 1e9))[0];
    return t ? tagColor.get(t) || hashColor(t) : '#8b949e';
  }, [colorBy, tagColor, tagRank, tagFilter]);

  const toggle = (list: string[], v: string, set: (x: string[]) => void) => set(list.includes(v) ? list.filter(x => x !== v) : [...list, v]);

  const select = (id: string | null) => {
    setSelected(id);
    if (id) setSide(null);
    if (id && tab === 'graph') graphRef.current?.focus(id);
  };

  // ── Note actions ──
  const saveEdit = async () => {
    if (!detail || editing == null) return;
    setSaving(true);
    try {
      const d = await api<NoteDetail>(`/api/knowledge/note/${encodeURIComponent(detail.permalink)}?base=${encodeURIComponent(base)}`, {
        method: 'PUT', body: JSON.stringify({ content: editing }),
      });
      setDetail(d);
      setEditing(null);
      setNotice({ ok: true, text: `Saved “${d.title}”.` });
      load();
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const trash = async () => {
    if (!detail || !window.confirm(`Move “${detail.title}” to the trash folder? You can move the file back later.`)) return;
    try {
      const r = await api<{ moved_to: string }>(`/api/knowledge/note/${encodeURIComponent(detail.permalink)}?base=${encodeURIComponent(base)}`, { method: 'DELETE' });
      setNotice({ ok: true, text: `Moved to ${r.moved_to}` });
      setSelected(null);
      load();
      loadBases();
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    }
  };

  const create = async () => {
    if (!creating?.name.trim()) return;
    setSaving(true);
    try {
      const d = await api<NoteDetail>(`/api/knowledge/note?base=${encodeURIComponent(base)}`, {
        method: 'POST',
        body: JSON.stringify({ name: creating.name, tags: creating.tags.split(/[,\s]+/).filter(Boolean), content: creating.content }),
      });
      setCreating(null);
      setNotice({ ok: true, text: `Created “${d.title}”.` });
      await load();
      loadBases();
      setSelected(d.permalink);
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  // 2026-10-23: add tags, facts and links to plain notes with the default model.
  const [enriching, setEnriching] = useState(false);
  const enrich = async () => {
    setEnriching(true);
    setNotice(null);
    try {
      const r = await api<{ enriched: unknown[]; failed: unknown[]; remaining: number; stopped: string; facts: number; links: number }>(
        `/api/knowledge/enrich?base=${encodeURIComponent(base)}`, { method: 'POST', body: JSON.stringify({ limit: 10 }) });
      const parts = [`Added tags/facts/links to ${r.enriched.length} note(s) (${r.facts} facts, ${r.links} links).`];
      if (r.remaining > 0) parts.push(`${r.remaining} plain note(s) left — click again for the next batch.`);
      if (r.stopped) parts.push(`Stopped: ${r.stopped}`);
      setNotice({ ok: !r.stopped, text: parts.join(' ') });
      await load();
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setEnriching(false);
    }
  };

  const sync = async () => {
    setLoading(true);
    try {
      const r = await api<{ stats: Record<string, number> }>(`/api/knowledge/sync?base=${encodeURIComponent(base)}`, { method: 'POST' });
      const s = Object.entries(r.stats || {}).filter(([, v]) => typeof v === 'number' && v > 0).map(([k, v]) => `${v} ${k}`).join(', ');
      setNotice({ ok: true, text: `Search index rebuilt${s ? ` — ${s}` : ''}.` });
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    }
    await load();
  };

  // after a conflict / merge action: message, reload the graph, the open note and the categories
  const qualityDone = (text: string, ok: boolean) => {
    setNotice({ ok, text });
    if (!ok) return;
    load(); // new graph data also reloads the category card
    if (selected && !selected.startsWith('missing:')) loadDetail(selected);
  };

  const missingTitle = selected?.startsWith('missing:') ? data?.nodes.find(n => n.id === selected)?.title || selected.slice(8) : null;

  const sortedNotes = useMemo(() => {
    const list = (data?.nodes || []).filter(n => !n.missing && (!hits || hits.has(n.id)) && (!tagFilter.length || n.tags.some(t => tagFilter.includes(t)))
      && (!catFilter.length || (n.categories || []).some(c => catFilter.includes(c))));
    const { key, dir } = sort;
    return [...list].sort((a, b) => {
      const va = a[key] ?? '', vb = b[key] ?? '';
      return (typeof va === 'string' ? va.localeCompare(String(vb)) : (va as number) - (vb as number)) * dir;
    });
  }, [data, hits, tagFilter, catFilter, sort]);

  const sortBtn = (key: typeof sort.key, label: string) => (
    <button type="button" className="kb-sort" onClick={() => setSort(s => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : (key === 'title' ? 1 : -1) }))}>
      {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
    </button>
  );

  const maxTag = Math.max(1, ...(data?.tags || []).map(t => t.count));
  const maxRel = Math.max(1, ...(data?.relation_types || []).map(t => t.count));

  return (
    <div className="kb-view">
      <div className="kb-toolbar">
        <select className="field-input kb-base" value={base} onChange={e => setBase(e.target.value)} aria-label="Knowledge base">
          {bases.length === 0 && <option value={base}>{base}</option>}
          {bases.map(b => <option key={b.name} value={b.name}>{b.name === 'default' ? 'Shared (default)' : b.name} · {b.notes}</option>)}
        </select>
        <input className="field-input kb-search" placeholder="Search notes and facts…" value={query} onChange={e => setQuery(e.target.value)} />
        <SearchModePicker mode={mode} status={semantic.st} note={query.trim() ? modeNote : null}
          onChange={m => { setMode(m); store(MODE_KEY, m); }} />
        <div className="kb-tabs" role="tablist">
          {(['graph', 'notes', 'tags'] as Tab[]).map(t => (
            <button key={t} role="tab" aria-selected={tab === t} className={`settings-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
              {t === 'graph' ? 'Graph' : t === 'notes' ? 'Notes' : 'Tags & types'}
            </button>
          ))}
        </div>
        <div className="kb-actions">
          <button type="button" className="ghost-btn" onClick={() => setCreating({ name: '', tags: '', content: '' })}>+ New note</button>
          <button type="button" className="ghost-btn" disabled={loading} onClick={sync} title="Re-read every markdown file into the search index">↻ Re-index</button>
          <button type="button" className="ghost-btn" onClick={() => setImportOpen(true)}
            title="Read a web page, GitHub repository, folder or document into linked notes, or import ChatGPT / Claude / MCP memory / Obsidian exports">⇪ Import</button>
          {data && data.stats.notes > 1 && (
            <button type="button" className="ghost-btn" onClick={() => { setCreating(null); setSelected(null); setSide({ type: 'dups' }); }}
              title="List notes that look like duplicates; preview and merge them one pair at a time">⧉ Find duplicates</button>
          )}
          {data && data.stats.notes > 0 && (
            <button type="button" className="ghost-btn" disabled={enriching}
              title="Ask the default model to add tags, facts and links to notes that have none (10 per click; the text is not changed)"
              onClick={enrich}>{enriching ? 'Working…' : '✨ Add tags & links'}</button>
          )}
        </div>
      </div>

      {data && (
        <div className="kb-stats">
          <span><b>{data.stats.notes}</b> notes</span>
          <span><b>{data.stats.observations}</b> facts</span>
          <span><b>{data.stats.relations}</b> links</span>
          <span><b>{data.stats.tags}</b> tags</span>
          <span title="Linked to, but no note exists yet"><b>{data.stats.missing}</b> missing</span>
          <span title="Notes with no links"><b>{data.stats.orphans}</b> unlinked</span>
          {data.truncated && <span className="probe-err">Only the first 2000 notes are shown.</span>}
          {semantic.st?.enabled && (
            <button type="button" className="chip small-chip" onClick={() => setTab('tags')}
              title={semantic.st.reason || 'Notes with vectors for semantic search (details under Tags & types)'}>
              <b>{semantic.st.embedded}</b>/{semantic.st.notes} embedded{semantic.st.installed ? '' : ' · not installed'}
            </button>
          )}
          {data.nodes.some(n => n.conflicts) && (
            <span title="Notes with facts that contradict each other (open a note to resolve)" style={{ color: '#d29922' }}>
              <b>{data.nodes.filter(n => n.conflicts).length}</b> ⚠ with conflicts</span>
          )}
          {(tagFilter.length > 0 || catFilter.length > 0 || relFilter.length > 0 || hits) && (
            <button type="button" className="chip small-chip" onClick={() => { setTagFilter([]); setCatFilter([]); setRelFilter([]); setQuery(''); }}>✕ Clear filters</button>
          )}
        </div>
      )}
      {notice && <div className={`notice ${notice.ok ? '' : 'error'}`} onClick={() => setNotice(null)}>{notice.text}</div>}
      {error && <div className="notice error">Could not load the knowledge base: {error} <button className="ghost-btn" onClick={load}>Retry</button></div>}

      <div className={`kb-main ${selected || creating || side ? 'with-panel' : ''}`}>
        <div className="kb-content">
          {tab === 'graph' && (
            <div className="kb-graph-card">
              <div className="kb-graph-controls">
                <label>Colour
                  <select className="field-input" value={colorBy} onChange={e => setColorBy(e.target.value as ColorBy)}>
                    <option value="tag">by tag</option><option value="links">by number of links</option><option value="none">one colour</option>
                  </select>
                </label>
                <label title="Show only the notes this many links away from the selected one">Around selected
                  <select className="field-input" value={depth} onChange={e => setDepth(Number(e.target.value))}>
                    <option value={0}>whole graph</option><option value={1}>1 link</option><option value={2}>2 links</option><option value={3}>3 links</option>
                  </select>
                </label>
                <label className="kb-check"><input type="checkbox" checked={edgeLabels} onChange={e => setEdgeLabels(e.target.checked)} /> link names</label>
                <label className="kb-check"><input type="checkbox" checked={hideMissing} onChange={e => setHideMissing(e.target.checked)} /> hide missing</label>
                <label className="kb-check"><input type="checkbox" checked={hideOrphans} onChange={e => setHideOrphans(e.target.checked)} /> hide unlinked</label>
                <span className="kb-spacer" />
                <button type="button" className="ghost-btn" onClick={() => graphRef.current?.fit()} title="Fit everything in view">⤢ Fit</button>
                <button type="button" className="ghost-btn" onClick={() => setPaused(p => !p)}>{paused ? '▶ Resume' : '⏸ Freeze'}</button>
                <button type="button" className="ghost-btn" onClick={() => { setPaused(false); graphRef.current?.reheat(); }} title="Unpin every note and lay the graph out again">⟳ Re-layout</button>
              </div>
              {data && view.nodes.length === 0 ? (
                <div className="kb-empty">
                  {data.stats.notes === 0
                    ? <>This knowledge base is empty. Agents add notes when they learn something (the <code>write_to_knowledge</code> tool), or click <b>+ New note</b>.</>
                    : <>No notes match the filters.</>}
                </div>
              ) : (
                <KnowledgeGraph ref={graphRef} nodes={view.nodes} edges={view.edges} selected={selected} onSelect={select}
                  colorOf={colorOf} showEdgeLabels={edgeLabels} paused={paused} />
              )}
              <div className="kb-legend">
                {colorBy === 'tag' && (data?.tags || []).slice(0, 10).map(t => (
                  <button type="button" key={t.name} className={`kb-legend-item ${tagFilter.includes(t.name) ? 'active' : ''}`}
                    onClick={() => toggle(tagFilter, t.name, setTagFilter)} title="Show only notes with this tag">
                    <span className="kb-swatch" style={{ background: tagColor.get(t.name) }} />#{t.name}
                  </button>
                ))}
                {colorBy === 'links' && [['0', 0], ['1–2', 1], ['3–5', 3], ['6+', 6]].map(([l, d]) => (
                  <span key={l} className="kb-legend-item"><span className="kb-swatch" style={{ background: linkColor(d as number) }} />{l} links</span>
                ))}
                <span className="kb-legend-item"><span className="kb-swatch missing" />missing note</span>
                {data?.nodes.some(n => n.conflicts) && <span className="kb-legend-item"><span className="kb-swatch" style={{ background: '#d29922', width: 8, height: 8 }} />has conflicts</span>}
                <span className="muted small kb-hint">Drag to pan · wheel to zoom · drag a note to pin it · double-click to unpin</span>
              </div>
            </div>
          )}

          {tab === 'notes' && (
            <div className="kb-table-card">
              <div className="kb-table">
                <div className="kb-row kb-head">
                  {sortBtn('title', 'Note')}<span>Tags</span>{sortBtn('observations', 'Facts')}{sortBtn('degree', 'Links')}{sortBtn('updated', 'Updated')}
                </div>
                {sortedNotes.map(n => (
                  <button type="button" key={n.id} className={`kb-row ${selected === n.id ? 'active' : ''}`} onClick={() => select(n.id)}>
                    <span className="kb-title"><span className="kb-swatch" style={{ background: colorOf(n) }} />{n.title}
                      {n.preview && <span className="muted small kb-preview">{n.preview}</span>}</span>
                    <span className="kb-tags">{n.tags.slice(0, 4).map(t => <span key={t} className="kb-tag">#{t}</span>)}</span>
                    <span>{n.observations}</span>
                    <span>{n.degree}</span>
                    <span className="muted small">{n.updated ? n.updated.slice(0, 16).replace('T', ' ') : '—'}</span>
                  </button>
                ))}
                {sortedNotes.length === 0 && <div className="kb-empty">No notes{hits || tagFilter.length ? ' match the filters' : ' yet'}.</div>}
              </div>
            </div>
          )}

          {tab === 'tags' && data && (
            <div className="kb-tags-grid">
              <section className="settings-card">
                <h3>Tags</h3>
                <p className="muted small">Click a tag to filter the graph and the notes.</p>
                <div className="kb-cloud">
                  {data.tags.map(t => (
                    <button type="button" key={t.name} className={`kb-cloud-tag ${tagFilter.includes(t.name) ? 'active' : ''}`}
                      style={{ fontSize: `${0.8 + (t.count / maxTag) * 0.7}rem`, borderColor: tagColor.get(t.name) }}
                      onClick={() => toggle(tagFilter, t.name, setTagFilter)}>
                      #{t.name} <span className="muted">{t.count}</span>
                    </button>
                  ))}
                  {data.tags.length === 0 && <span className="muted">No tags yet.</span>}
                </div>
              </section>
              <SemanticCard base={base} status={semantic} />
              <CategoriesCard base={base} reloadKey={data} active={catFilter}
                onToggle={c => toggle(catFilter, c, setCatFilter)}
                onMerge={sg => { setCreating(null); setSelected(null); setSide({ type: 'catmerge', from: sg.from, to: sg.to }); }} />
              <section className="settings-card">
                <h3>Link types</h3>
                <p className="muted small">Links are lines like <code>- uses [[Other note]]</code>. Click to show only that type.</p>
                {data.relation_types.map(c => (
                  <button type="button" className={`kb-bar ${relFilter.includes(c.name) ? 'active' : ''}`} key={c.name} onClick={() => toggle(relFilter, c.name, setRelFilter)}>
                    <span>{c.name.replace(/_/g, ' ')}</span>
                    <span className="kb-bar-track"><span className="kb-bar-fill alt" style={{ width: `${(c.count / maxRel) * 100}%` }} /></span><b>{c.count}</b></button>
                ))}
                {data.relation_types.length === 0 && <span className="muted">No links yet.</span>}
              </section>
            </div>
          )}
        </div>

        {(selected || creating || side) && (
          <aside className="kb-panel">
            <button type="button" className="kb-close ghost-btn" onClick={() => { setSelected(null); setCreating(null); setSide(null); }} aria-label="Close">✕</button>

            {!creating && side?.type === 'dups' && (
              <DuplicatesPanel base={base} onDone={qualityDone} onCancel={() => setSide(null)}
                onOpen={id => { setSide(null); select(id); }} />
            )}
            {!creating && side?.type === 'catmerge' && (
              <CategoryMergePanel base={base} from={side.from} to={side.to} onCancel={() => setSide(null)}
                onDone={(t, ok) => { setSide(null); qualityDone(t, ok); }} />
            )}

            {creating && (
              <div className="kb-detail">
                <h3>New note</h3>
                <div className="field"><label className="field-label">Name</label>
                  <input className="field-input" autoFocus value={creating.name} onChange={e => setCreating({ ...creating, name: e.target.value })} placeholder="e.g. Home server" /></div>
                <div className="field"><label className="field-label">Tags</label>
                  <input className="field-input" value={creating.tags} onChange={e => setCreating({ ...creating, tags: e.target.value })} placeholder="homelab, linux" /></div>
                <div className="field"><label className="field-label">Text</label>
                  <textarea className="field-input kb-editor" rows={10} value={creating.content} onChange={e => setCreating({ ...creating, content: e.target.value })}
                    placeholder={'Free text, plus optional facts and links:\n- [fact] Runs Ubuntu 24.04 #os\n- uses [[Docker]]'} /></div>
                <div className="kb-buttons">
                  <button type="button" className="primary-btn" disabled={saving || !creating.name.trim()} onClick={create}>{saving ? 'Creating…' : 'Create note'}</button>
                  <button type="button" className="ghost-btn" onClick={() => setCreating(null)}>Cancel</button>
                </div>
              </div>
            )}

            {!creating && !side && missingTitle && (
              <div className="kb-detail">
                <h3>{missingTitle}</h3>
                <p className="muted">Other notes link to this, but it has no note yet.</p>
                <button type="button" className="primary-btn" onClick={() => setCreating({ name: missingTitle, tags: '', content: '' })}>Create this note</button>
              </div>
            )}

            {!creating && !side && detailErr && <div className="notice error">{detailErr}</div>}

            {!creating && !side && detail && (
              <div className="kb-detail">
                <h3>{detail.title}</h3>
                <div className="kb-tags">{detail.tags.map(t => <span key={t} className="kb-tag" style={{ borderColor: tagColor.get(t) }}>#{t}</span>)}</div>
                <div className="muted small kb-meta" title={detail.file}>{detail.permalink}.md · {detail.modified} · {(detail.size / 1024).toFixed(1)} KB</div>

                {editing != null ? (
                  <>
                    <textarea className="field-input kb-editor" rows={18} value={editing} onChange={e => setEditing(e.target.value)} spellCheck={false} />
                    <p className="muted small">Facts: <code>- [category] text #tag</code> · links: <code>- relation [[Note]]</code></p>
                    <div className="kb-buttons">
                      <button type="button" className="primary-btn" disabled={saving} onClick={saveEdit}>{saving ? 'Saving…' : 'Save'}</button>
                      <button type="button" className="ghost-btn" onClick={() => setEditing(null)}>Cancel</button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="kb-buttons">
                      <button type="button" className="ghost-btn" onClick={() => setEditing(detail.content)}>✎ Edit</button>
                      {tab !== 'graph' && <button type="button" className="ghost-btn" onClick={() => { setTab('graph'); window.setTimeout(() => graphRef.current?.focus(detail.permalink), 50); }}>◎ Show in graph</button>}
                      <button type="button" className="ghost-btn danger" onClick={trash}>Move to trash</button>
                    </div>
                    <ConflictsSection base={base} conflicts={detail.conflicts || []} onDone={qualityDone} />
                    {detail.body && <div className="kb-body">{detail.body}</div>}

                    {detail.observations.length > 0 && (
                      <section>
                        <h4>Facts <span className="muted">{detail.observations.length}</span></h4>
                        {Object.entries(detail.observations.reduce<Record<string, typeof detail.observations>>((acc, o) => {
                          (acc[o.category] ||= []).push(o); return acc;
                        }, {})).map(([cat, list]) => (
                          <div key={cat} className="kb-obs-group">
                            <div className="kb-obs-cat">{cat}</div>
                            <ul>{list.map((o, i) => <li key={i}>{o.content}{o.tags.map(t => <span key={t} className="kb-tag inline">#{t}</span>)}
                              {o.source && <span className="muted small kb-source" title={`Source: ${o.source}`}> · {o.source}</span>}</li>)}</ul>
                          </div>
                        ))}
                      </section>
                    )}

                    <section>
                      <h4>Links to <span className="muted">{detail.links.length}</span></h4>
                      {detail.links.length === 0 && <div className="muted small">None.</div>}
                      {detail.links.map((l, i) => (
                        <button type="button" key={i} className={`kb-link ${l.exists ? '' : 'missing'}`} onClick={() => select(l.target)}>
                          <span className="kb-rel">{l.type.replace(/_/g, ' ')}</span> → {l.label}{!l.exists && <span className="muted small"> (no note yet)</span>}
                        </button>
                      ))}
                    </section>
                    <section>
                      <h4>Linked from <span className="muted">{detail.backlinks.length}</span></h4>
                      {detail.backlinks.length === 0 && <div className="muted small">Nothing links here yet.</div>}
                      {detail.backlinks.map((l, i) => (
                        <button type="button" key={i} className="kb-link" onClick={() => select(l.source)}>
                          {l.label} <span className="kb-rel">{l.type.replace(/_/g, ' ')}</span> → here
                        </button>
                      ))}
                    </section>
                  </>
                )}
              </div>
            )}
          </aside>
        )}
      </div>
      <KnowledgeImport base={base} open={importOpen} onClose={() => setImportOpen(false)}
        onDone={load} onOpenNote={(p) => { setImportOpen(false); setSide(null); setCreating(null); setSelected(p); }} />
    </div>
  );
};

export default KnowledgeView;
