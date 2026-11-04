// Force-directed graph of the knowledge base, drawn on a <canvas> (no extra
// libraries). Drag the background to pan, the wheel to zoom, drag a note to
// move it (it stays pinned; double-click it to release), click to select.
// 2026-10-31: easier to read — notes do not overlap, unlinked notes sit on an
// outer ring, links are coloured by type, thicker, curved when two notes link
// both ways and keep their width when zooming; labels never overlap.
// 2026-11-04: "Neurons" style (the default): every group of linked notes is a
// radial burst around its hub, groups spaced apart (../neuronLayout.ts); thin
// curved branches, glowing dots. "Classic" is the force layout above.
import React, { useCallback, useEffect, useImperativeHandle, useRef } from 'react';
import { neuronLayout, type NeuronGroup, type NeuronPos } from '../neuronLayout';

export interface GNode {
  id: string;
  title: string;
  tags: string[];
  observations: number;
  relations: number;
  degree: number;
  missing: boolean;
  preview?: string;
  conflicts?: number; // 2026-10-24 (KB phase 2): open conflicts (contradicting facts)
}
export interface GEdge { source: string; target: string; type: string }

interface SimNode { id: string; x: number; y: number; vx: number; vy: number; pinned: boolean; r: number; linked: boolean }

export type LabelMode = 'auto' | 'all' | 'none';
export type GraphStyle = 'neurons' | 'classic';

export interface GraphHandle { fit: () => void; reheat: () => void; focus: (id: string) => void }

interface Props {
  nodes: GNode[];
  edges: GEdge[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  colorOf: (n: GNode) => string;
  showEdgeLabels: boolean;
  paused: boolean;
  edgeColorOf?: (type: string) => string;  // colour of a link type
  linkWeight?: number;                      // 1 normal, 2 bold
  labelMode?: LabelMode;
  graphStyle?: GraphStyle;
}

const LINK_LEN = 110;

/** Drawn radius: grows with the zoom only up to a point, so notes do not cover each other. */
function radius(p: { r: number }, k: number): number {
  return k > 1 ? p.r / Math.sqrt(k) : p.r;
}

/** Neurons: dot size on screen grows slowly with the zoom, never below ~1.5 px. */
function nradius(p: { r: number }, k: number): number {
  return (p.r * Math.sqrt(Math.min(1.6, Math.max(0.5, k)))) / k;
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const KnowledgeGraph = React.forwardRef<GraphHandle, Props>(function KnowledgeGraph(
  { nodes, edges, selected, onSelect, colorOf, showEdgeLabels, paused, edgeColorOf, linkWeight = 1, labelMode = 'auto', graphStyle = 'neurons' }, ref,
) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const sim = useRef<Map<string, SimNode>>(new Map());
  const view = useRef({ x: 0, y: 0, k: 1 });
  const alpha = useRef(1);
  const raf = useRef<number | null>(null);
  const hover = useRef<string | null>(null);
  const size = useRef({ w: 600, h: 400 });
  const theme = useRef({ text: '#c9d1d9', muted: '#8b949e', edge: 'rgba(139,148,158,0.35)', accent: '#58a6ff', bg: '#0d1117' });
  const props = useRef({ nodes, edges, selected, colorOf, showEdgeLabels, paused, edgeColorOf, linkWeight, labelMode, graphStyle });
  props.current = { nodes, edges, selected, colorOf, showEdgeLabels, paused, edgeColorOf, linkWeight, labelMode, graphStyle };
  const neuron = useRef<{ pos: Map<string, NeuronPos>; groups: NeuronGroup[] } | null>(null);
  const didFit = useRef(false);
  const settledFit = useRef(false);  // fit again when the layout has settled, unless the user moved the view
  const userMoved = useRef(false);
  const wheelRef = useRef<(e: WheelEvent) => void>(() => {});

  const readTheme = () => {
    theme.current = {
      text: cssVar('--text-primary', '#c9d1d9'),
      muted: cssVar('--text-secondary', '#8b949e'),
      edge: document.documentElement.getAttribute('data-theme') === 'light' ? 'rgba(87,96,106,0.35)' : 'rgba(139,148,158,0.32)',
      accent: cssVar('--accent-color', '#58a6ff'),
      bg: cssVar('--bg-color', '#0d1117'),
    };
  };

  // ── Sync simulation nodes with the data (keep positions of known notes) ──
  useEffect(() => {
    const m = sim.current;
    if (graphStyle === 'neurons') {
      const L = neuronLayout(nodes.map(n => n.id), edges);
      neuron.current = L;
      const size = new Map(L.groups.map((g, i) => [i, g.size]));
      m.clear();
      for (const n of nodes) {
        const q = L.pos.get(n.id)!;
        const r = q.hub ? 5 + Math.min(7, Math.sqrt(size.get(q.group) || 1) * 0.6) : 2.4 + Math.min(3.2, Math.sqrt(n.degree) * 0.8);
        m.set(n.id, { id: n.id, x: q.x, y: q.y, vx: 0, vy: 0, pinned: true, r, linked: q.group >= 0 });
      }
      alpha.current = 0;
      if (!userMoved.current) window.setTimeout(() => fit(), 0);
      start();
      return;
    }
    neuron.current = null;
    if (m.size && [...m.values()].every(p => p.pinned)) for (const p of m.values()) p.pinned = false;   // coming from Neurons
    const ids = new Set(nodes.map(n => n.id));
    for (const id of [...m.keys()]) if (!ids.has(id)) m.delete(id);
    const placed = [...m.values()];
    const cx = placed.length ? placed.reduce((s, n) => s + n.x, 0) / placed.length : 0;
    const cy = placed.length ? placed.reduce((s, n) => s + n.y, 0) / placed.length : 0;
    let i = 0;
    const linkedIds = new Set<string>();
    for (const e of edges) { linkedIds.add(e.source); linkedIds.add(e.target); }
    for (const n of nodes) {
      const r = n.missing ? 5 : 5 + Math.min(11, Math.sqrt(n.degree) * 2.6 + n.observations * 0.2);
      const linked = linkedIds.has(n.id);
      const old = m.get(n.id);
      if (old) { old.r = r; old.linked = linked; continue; }
      // Start new notes near a neighbour that already has a place, else on a spiral.
      const nb = edges.find(e => (e.source === n.id && m.has(e.target)) || (e.target === n.id && m.has(e.source)));
      const anchor = nb ? m.get(nb.source === n.id ? nb.target : nb.source) : null;
      const a = i * 2.39996;
      const d = 30 * Math.sqrt(i + 1);
      m.set(n.id, {
        id: n.id, r, linked, pinned: false, vx: 0, vy: 0,
        x: anchor ? anchor.x + (Math.random() - 0.5) * 60 : cx + Math.cos(a) * d,
        y: anchor ? anchor.y + (Math.random() - 0.5) * 60 : cy + Math.sin(a) * d,
      });
      i++;
    }
    alpha.current = Math.max(alpha.current, i > 0 ? 1 : 0.3);
    if (i > 0 && placed.length === 0) { didFit.current = false; settledFit.current = false; userMoved.current = false; }
    start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, graphStyle]);

  const neighbours = useCallback((id: string | null) => {
    const s = new Set<string>();
    if (!id) return s;
    s.add(id);
    for (const e of props.current.edges) {
      if (e.source === id) s.add(e.target);
      if (e.target === id) s.add(e.source);
    }
    return s;
  }, []);

  // ── Physics ──
  const tick = () => {
    const m = sim.current;
    const list = [...m.values()];
    const a = alpha.current;
    const n = list.length;
    const rep = 3800 * a;
    for (let i = 0; i < n; i++) {
      const p = list[i];
      for (let j = i + 1; j < n; j++) {
        const q = list[j];
        let dx = q.x - p.x, dy = q.y - p.y;
        let d2 = dx * dx + dy * dy;
        if (d2 > 250000) continue; // far apart: ignore (keeps big graphs fast)
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        // unlinked notes push each other less: they only need not to overlap
        const f = (p.linked && q.linked ? rep : rep * 0.35) / d2;
        const d = Math.sqrt(d2);
        let fx = (dx / d) * f, fy = (dy / d) * f;
        const minD = p.r + q.r + 6;
        if (d < minD) { // collision: never draw notes on top of each other
          const push = (minD - d) * 0.5;
          fx += (dx / d) * push; fy += (dy / d) * push;
        }
        p.vx -= fx; p.vy -= fy; q.vx += fx; q.vy += fy;
      }
    }
    for (const e of props.current.edges) {
      const s = m.get(e.source), t = m.get(e.target);
      if (!s || !t) continue;
      const dx = t.x - s.x, dy = t.y - s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = ((d - LINK_LEN) / d) * 0.08 * a;
      s.vx += dx * f; s.vy += dy * f; t.vx -= dx * f; t.vy -= dy * f;
    }
    // unlinked notes go to a ring around the linked ones
    const linkedCount = list.filter(p => p.linked).length;
    const ring = 140 + 34 * Math.sqrt(Math.max(1, linkedCount));
    for (const p of list) {
      if (p.linked || linkedCount === 0) {
        p.vx -= p.x * 0.005 * a;
        p.vy -= p.y * 0.005 * a;
      } else {
        const d = Math.hypot(p.x, p.y) || 1;
        const f = ((ring - d) / d) * 0.03 * a;
        p.vx += p.x * f; p.vy += p.y * f;
      }
      if (p.pinned) { p.vx = 0; p.vy = 0; continue; }
      p.vx *= 0.55; p.vy *= 0.55;
      p.x += Math.max(-40, Math.min(40, p.vx));
      p.y += Math.max(-40, Math.min(40, p.vy));
    }
    alpha.current *= 0.982;
  };

  // ── Drawing ──
  const draw = () => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    if (neuron.current) { drawNeurons(ctx, neuron.current); return; }
    const { w, h } = size.current;
    const dpr = window.devicePixelRatio || 1;
    const { x: tx, y: ty, k } = view.current;
    const th = theme.current;
    const { nodes: ns, edges: es, selected: sel, colorOf: col, showEdgeLabels: labels } = props.current;
    const focus = sel || hover.current;
    const near = neighbours(focus);
    const m = sim.current;
    const { edgeColorOf: ecol, linkWeight: lw = 1, labelMode: lmode = 'auto' } = props.current;
    const rr = (p: SimNode) => radius(p, k);
    const pairs = new Set(es.map(e => `${e.source}\u0000${e.target}`));

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.translate(w / 2 + tx, h / 2 + ty);
    ctx.scale(k, k);

    // edges
    for (const e of es) {
      const s = m.get(e.source), t = m.get(e.target);
      if (!s || !t) continue;
      const on = focus ? (e.source === focus || e.target === focus) : false;
      const dim = focus && !on;
      const colour = ecol ? ecol(e.type) : th.edge;
      ctx.strokeStyle = on ? th.accent : colour;
      ctx.globalAlpha = dim ? 0.08 : on ? 1 : 0.72;
      // same width on screen at every zoom level
      ctx.lineWidth = (on ? 2.6 : 1.3 * lw) / k;
      const dx = t.x - s.x, dy = t.y - s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / d, uy = dy / d;
      // A→B and B→A both exist: bend each to its own side so both stay visible
      const bend = pairs.has(`${e.target}\u0000${e.source}`) ? Math.min(40, d * 0.18) : 0;
      const mx = (s.x + t.x) / 2 - uy * bend, my = (s.y + t.y) / 2 + ux * bend;
      // end directions follow the curve
      const ex0 = t.x - mx, ey0 = t.y - my, el = Math.hypot(ex0, ey0) || 1;
      const sx0 = mx - s.x, sy0 = my - s.y, sl = Math.hypot(sx0, sy0) || 1;
      const ex = t.x - (ex0 / el) * (rr(t) + 2 / k), ey = t.y - (ey0 / el) * (rr(t) + 2 / k);
      const bx = s.x + (sx0 / sl) * rr(s), by = s.y + (sy0 / sl) * rr(s);
      ctx.beginPath();
      ctx.moveTo(bx, by);
      if (bend) ctx.quadraticCurveTo(mx, my, ex, ey); else ctx.lineTo(ex, ey);
      ctx.stroke();
      // arrow head (constant size on screen)
      const ah = (on ? 9 : 7 + lw) / k;
      const ax = ex0 / el, ay = ey0 / el;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(ex, ey);
      ctx.lineTo(ex - ax * ah - ay * ah * 0.5, ey - ay * ah + ax * ah * 0.5);
      ctx.lineTo(ex - ax * ah + ay * ah * 0.5, ey - ay * ah - ax * ah * 0.5);
      ctx.closePath();
      ctx.fill();
      if ((labels && k > 0.6) || on) {
        const fs = 11 / k;
        ctx.font = `${on ? 600 : 400} ${fs}px Inter, sans-serif`;
        ctx.textAlign = 'center';
        const lx = bend ? (s.x + t.x) / 4 + mx / 2 : (s.x + t.x) / 2 - uy * (7 / k);
        const ly = bend ? (s.y + t.y) / 4 + my / 2 : (s.y + t.y) / 2 + ux * (7 / k);
        const text = e.type.replace(/_/g, ' ');
        ctx.globalAlpha = dim ? 0.15 : 1;
        ctx.lineWidth = 3 / k;
        ctx.strokeStyle = th.bg;
        ctx.strokeText(text, lx, ly + 3 / k);
        ctx.fillStyle = on ? th.accent : colour;
        ctx.fillText(text, lx, ly + 3 / k);
      }
    }
    ctx.globalAlpha = 1;

    // nodes
    const byId = new Map(ns.map(n => [n.id, n]));
    const order = [...m.values()].sort((a, b) => (near.has(a.id) ? 1 : 0) - (near.has(b.id) ? 1 : 0));
    for (const p of order) {
      const n = byId.get(p.id);
      if (!n) continue;
      const dim = focus && !near.has(p.id);
      ctx.globalAlpha = dim ? 0.2 : 1;
      const pr = rr(p);
      ctx.beginPath();
      ctx.arc(p.x, p.y, pr, 0, Math.PI * 2);
      if (n.missing) {
        ctx.fillStyle = th.bg;
        ctx.fill();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = th.muted;
        ctx.lineWidth = 1.2;
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.fillStyle = col(n);
        ctx.fill();
        ctx.strokeStyle = th.bg;
        ctx.lineWidth = 1.5 / Math.max(1, k);
        ctx.stroke();
      }
      if (p.id === sel) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, pr + 4 / k, 0, Math.PI * 2);
        ctx.strokeStyle = th.accent;
        ctx.lineWidth = 2.5 / k;
        ctx.stroke();
      }
      if (p.pinned) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
        ctx.fillStyle = th.bg;
        ctx.fill();
      }
      if (n.conflicts) {
        // small amber badge at the top right: this note has contradicting facts
        const bx = p.x + pr * 0.75, by = p.y - pr * 0.75, br = Math.max(3.5 / k, 4.5 / Math.sqrt(k));
        ctx.beginPath();
        ctx.arc(bx, by, br, 0, Math.PI * 2);
        ctx.fillStyle = '#d29922';
        ctx.fill();
        ctx.strokeStyle = th.bg;
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // labels last, most important first, never on top of each other
    if (lmode === 'none' && !focus) return;
    const want = (p: SimNode, n: GNode) => {
      if (near.has(p.id)) return true;
      if (lmode === 'none') return false;
      if (lmode === 'all') return true;
      if (ns.length <= 40) return true;
      if (!p.linked) return k > 1.6;
      return k > 1.3 || (k > 0.75 && n.degree >= 2) || n.degree >= 5;
    };
    const cand = order.filter(p => { const n = byId.get(p.id); return n && want(p, n); })
      .sort((a, b) => {
        const pa = (a.id === sel ? 3 : 0) + (near.has(a.id) ? 2 : 0), pb = (b.id === sel ? 3 : 0) + (near.has(b.id) ? 2 : 0);
        return pb - pa || (byId.get(b.id)!.degree - byId.get(a.id)!.degree);
      });
    const boxes: [number, number, number, number][] = [];
    const fs = 12 / k;            // 12 px on screen
    ctx.textAlign = 'center';
    for (const p of cand) {
      const n = byId.get(p.id)!;
      const dim = focus && !near.has(p.id);
      const label = n.title.length > 30 ? n.title.slice(0, 29) + '…' : n.title;
      ctx.font = `${p.id === sel ? 600 : 400} ${fs}px Inter, sans-serif`;
      const tw = ctx.measureText(label).width;
      const lx = p.x, ly = p.y + rr(p) + fs + 1 / k;
      const box: [number, number, number, number] = [lx - tw / 2 - 2 / k, ly - fs, lx + tw / 2 + 2 / k, ly + 3 / k];
      if (boxes.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      boxes.push(box);
      ctx.globalAlpha = dim ? 0.35 : 1;
      ctx.lineWidth = 3.5 / k;
      ctx.strokeStyle = th.bg;
      ctx.strokeText(label, lx, ly);
      ctx.fillStyle = n.missing ? th.muted : th.text;
      ctx.fillText(label, lx, ly);
    }
    ctx.globalAlpha = 1;
  };

  // ── Drawing: Neurons ──
  const drawNeurons = (ctx: CanvasRenderingContext2D, L: { pos: Map<string, NeuronPos>; groups: NeuronGroup[] }) => {
    const { w, h } = size.current;
    const dpr = window.devicePixelRatio || 1;
    const { x: tx, y: ty, k } = view.current;
    const th = theme.current;
    const dark = document.documentElement.getAttribute('data-theme') !== 'light';
    const { nodes: ns, edges: es, selected: sel, colorOf: col, labelMode: lmode = 'auto', showEdgeLabels: elabels, linkWeight: lw = 1 } = props.current;
    const focus = sel || hover.current;
    const near = neighbours(focus);
    const m = sim.current;
    const byId = new Map(ns.map(n => [n.id, n]));
    const colour = (id: string) => { const n = byId.get(id); return n ? (n.missing ? th.muted : col(n)) : th.muted; };
    const rr = (p: SimNode) => nradius(p, k);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.fillStyle = dark ? '#04060b' : '#fbfcfe';
    ctx.fillRect(0, 0, w, h);
    ctx.translate(w / 2 + tx, h / 2 + ty);
    ctx.scale(k, k);
    const glow = dark ? 'lighter' : 'source-over';

    // branches: parent → child, a smooth curve that leaves the parent along its own direction
    const tree = new Set<string>();
    ctx.globalCompositeOperation = glow;
    for (const [id, q] of L.pos) {
      if (!q.parent) continue;
      const a = m.get(q.parent), b = m.get(id);
      if (!a || !b) continue;
      tree.add(`${q.parent}\u0000${id}`); tree.add(`${id}\u0000${q.parent}`);
      const on = !!focus && (id === focus || q.parent === focus);
      const dim = !!focus && !on;
      const g = L.groups[q.group];
      // an organic curve: it leaves the parent heading outwards and curls a little
      // (the same way every time) before reaching the child, like a neuron's branch
      const ox = g ? g.cx : 0, oy = g ? g.cy : 0;
      const pa = L.pos.get(q.parent)!;
      const ra = Math.hypot(a.x - ox, a.y - oy), rb = Math.hypot(b.x - ox, b.y - oy);
      const aa = pa.depth === 0 ? q.angle : pa.angle;
      let da = q.angle - aa;
      if (da > Math.PI) da -= 2 * Math.PI; else if (da < -Math.PI) da += 2 * Math.PI;
      const curl = q.bend * 0.22;
      const r1 = ra + (rb - ra) * 0.4, a1 = aa + da * 0.15 + curl;
      const r2 = ra + (rb - ra) * 0.8, a2 = q.angle - curl * 0.6;
      ctx.strokeStyle = on ? th.accent : colour(id);
      ctx.globalAlpha = dim ? 0.06 : on ? 1 : dark ? 0.55 : 0.6;
      ctx.lineWidth = (on ? 2 : 0.9 * lw) / k;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.bezierCurveTo(ox + Math.cos(a1) * r1, oy + Math.sin(a1) * r1, ox + Math.cos(a2) * r2, oy + Math.sin(a2) * r2, b.x, b.y);
      ctx.stroke();
    }
    // other links (across branches or groups): faint, highlighted when a note is focused
    for (const e of es) {
      if (tree.has(`${e.source}\u0000${e.target}`)) continue;
      const a = m.get(e.source), b = m.get(e.target);
      if (!a || !b) continue;
      const on = !!focus && (e.source === focus || e.target === focus);
      if (focus && !on) continue;
      ctx.strokeStyle = on ? th.accent : colour(e.target);
      ctx.globalAlpha = on ? 0.9 : dark ? 0.09 : 0.12;
      ctx.lineWidth = (on ? 1.6 : 0.7) / k;
      const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.15, my = (a.y + b.y) / 2 - (b.x - a.x) * 0.15;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.quadraticCurveTo(mx, my, b.x, b.y);
      ctx.stroke();
    }

    // dots with a soft glow
    for (const p of m.values()) {
      const n = byId.get(p.id);
      if (!n) continue;
      const dim = !!focus && !near.has(p.id);
      const pr = rr(p);
      const c0 = colour(p.id);
      if (dark && !n.missing) {
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = dim ? 0.02 : 0.13;
        ctx.fillStyle = c0;
        ctx.beginPath(); ctx.arc(p.x, p.y, pr * 2.1, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = dim ? 0.18 : 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, pr, 0, Math.PI * 2);
      if (n.missing) {
        ctx.setLineDash([2 / k, 2 / k]); ctx.strokeStyle = th.muted; ctx.lineWidth = 1 / k; ctx.stroke(); ctx.setLineDash([]);
      } else { ctx.fillStyle = c0; ctx.fill(); }
      if (p.id === sel) {
        ctx.beginPath(); ctx.arc(p.x, p.y, pr + 4 / k, 0, Math.PI * 2);
        ctx.strokeStyle = th.accent; ctx.lineWidth = 2 / k; ctx.stroke();
      }
      if (n.conflicts) {
        ctx.beginPath(); ctx.arc(p.x + pr, p.y - pr, Math.max(2.5 / k, pr * 0.5), 0, Math.PI * 2);
        ctx.fillStyle = '#d29922'; ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';

    // link type on the focused note's links
    if (focus && elabels) {
      ctx.font = `400 ${11 / k}px Inter, sans-serif`;
      ctx.textAlign = 'center';
      for (const e of es) {
        if (e.source !== focus && e.target !== focus) continue;
        const a = m.get(e.source), b = m.get(e.target);
        if (!a || !b) continue;
        const t = e.type.replace(/_/g, ' ');
        ctx.lineWidth = 3 / k; ctx.strokeStyle = dark ? '#04060b' : '#fbfcfe';
        ctx.strokeText(t, (a.x + b.x) / 2, (a.y + b.y) / 2);
        ctx.fillStyle = th.accent; ctx.fillText(t, (a.x + b.x) / 2, (a.y + b.y) / 2);
      }
    }

    // labels: hubs of the bigger groups first, then by links; never on top of each other
    if (lmode === 'none' && !focus) return;
    const want = (p: SimNode, n: GNode, q: NeuronPos) => {
      if (near.has(p.id)) return true;
      if (lmode === 'none') return false;
      if (lmode === 'all') return k > 0.5 || q.hub;
      if (q.hub && (L.groups[q.group]?.size || 0) >= 4) return true;
      return k > 1.5 || (k > 0.9 && n.degree >= 4) || n.degree >= 12;
    };
    const cand = [...m.values()].filter(p => { const n = byId.get(p.id), q = L.pos.get(p.id); return n && q && want(p, n, q); })
      .sort((a, b) => {
        const qa = L.pos.get(a.id)!, qb = L.pos.get(b.id)!;
        const pa = (a.id === sel ? 9 : 0) + (near.has(a.id) ? 4 : 0) + (qa.hub ? 2 : 0);
        const pb = (b.id === sel ? 9 : 0) + (near.has(b.id) ? 4 : 0) + (qb.hub ? 2 : 0);
        return pb - pa || (byId.get(b.id)!.degree - byId.get(a.id)!.degree);
      });
    const boxes: [number, number, number, number][] = [];
    ctx.textAlign = 'center';
    for (const p of cand) {
      const n = byId.get(p.id)!, q = L.pos.get(p.id)!;
      const big = q.hub && !near.has(p.id);
      const fs = (big ? 13 : 11.5) / k;
      const label = n.title.length > 34 ? n.title.slice(0, 33) + '…' : n.title;
      ctx.font = `${big || p.id === sel ? 600 : 400} ${fs}px Inter, sans-serif`;
      const tw = ctx.measureText(label).width;
      const lx = p.x, ly = p.y + rr(p) + fs + 2 / k;
      const box: [number, number, number, number] = [lx - tw / 2 - 3 / k, ly - fs, lx + tw / 2 + 3 / k, ly + 3 / k];
      if (boxes.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      boxes.push(box);
      ctx.globalAlpha = focus && !near.has(p.id) ? 0.35 : 1;
      ctx.lineWidth = 3.5 / k;
      ctx.strokeStyle = dark ? '#04060b' : '#fbfcfe';
      ctx.strokeText(label, lx, ly);
      ctx.fillStyle = n.missing ? th.muted : th.text;
      ctx.fillText(label, lx, ly);
    }
    ctx.globalAlpha = 1;
  };

  const fit = useCallback(() => {
    const list = [...sim.current.values()];
    if (!list.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of list) { x0 = Math.min(x0, p.x - p.r); y0 = Math.min(y0, p.y - p.r); x1 = Math.max(x1, p.x + p.r); y1 = Math.max(y1, p.y + p.r); }
    const { w, h } = size.current;
    const pad = neuron.current ? 110 : 60;   // neurons: room for the names and glow at the edges
    const k = Math.max(0.05, Math.min(2.2, Math.min((w - pad) / (x1 - x0 || 1), (h - pad) / (y1 - y0 || 1))));
    view.current = { k, x: -((x0 + x1) / 2) * k, y: -((y0 + y1) / 2) * k };
    draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loop = () => {
    raf.current = null;
    if (!neuron.current && !props.current.paused && alpha.current > 0.004) {
      tick();
      // fit once while the first layout settles
      if (!didFit.current && alpha.current < 0.25) { didFit.current = true; fit(); }
      if (didFit.current && !settledFit.current && alpha.current < 0.03) {
        settledFit.current = true;
        if (!userMoved.current) fit();
      }
    }
    draw();
    if (!neuron.current && !props.current.paused && alpha.current > 0.004) raf.current = requestAnimationFrame(loop);
  };
  function start() {
    if (raf.current == null) raf.current = requestAnimationFrame(loop);
  }

  useImperativeHandle(ref, () => ({
    fit,
    reheat: () => {
      if (neuron.current) { userMoved.current = false; fit(); return; }
      for (const p of sim.current.values()) p.pinned = false;
      alpha.current = 1;
      start();
    },
    focus: (id: string) => {
      const p = sim.current.get(id);
      if (!p) return;
      const k = Math.max(view.current.k, 1.2);
      view.current = { k, x: -p.x * k, y: -p.y * k };
      start();
    },
  }));

  // redraw when selection / colours / labels change; resume when un-paused
  useEffect(() => { start(); });

  // ── Size + theme ──
  useEffect(() => {
    readTheme();
    const el = wrapRef.current, c = canvasRef.current;
    if (!el || !c) return;
    const resize = () => {
      const r = el.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      size.current = { w: r.width, h: r.height };
      c.width = Math.round(r.width * dpr);
      c.height = Math.round(r.height * dpr);
      c.style.width = `${r.width}px`;
      c.style.height = `${r.height}px`;
      draw();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    const wheel = (e: WheelEvent) => wheelRef.current(e);
    c.addEventListener('wheel', wheel, { passive: false });
    const mo = new MutationObserver(() => { readTheme(); draw(); });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => { ro.disconnect(); mo.disconnect(); c.removeEventListener('wheel', wheel); if (raf.current) cancelAnimationFrame(raf.current); raf.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Pointer interaction ──
  const toWorld = (cx: number, cy: number) => {
    const { w, h } = size.current;
    const { x, y, k } = view.current;
    return { x: (cx - w / 2 - x) / k, y: (cy - h / 2 - y) / k };
  };
  const hit = (cx: number, cy: number): SimNode | null => {
    const p = toWorld(cx, cy);
    let best: SimNode | null = null, bd = Infinity;
    for (const n of sim.current.values()) {
      const d = Math.hypot(n.x - p.x, n.y - p.y);
      const R = neuron.current ? nradius(n, view.current.k) + 6 / view.current.k : radius(n, view.current.k) + 4 / view.current.k;
      if (d < R && d < bd) { best = n; bd = d; }
    }
    return best;
  };
  const drag = useRef<{ mode: 'pan' | 'node'; id?: string; sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null);

  const local = (e: React.PointerEvent | React.MouseEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { cx: e.clientX - r.left, cy: e.clientY - r.top };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    const { cx, cy } = local(e);
    const n = hit(cx, cy);
    canvasRef.current?.setPointerCapture(e.pointerId);
    // Neurons: the layout is fixed, so dragging always pans (a click still selects the note)
    drag.current = { mode: n && !neuron.current ? 'node' : 'pan', id: n?.id, sx: cx, sy: cy, vx: view.current.x, vy: view.current.y, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const { cx, cy } = local(e);
    const d = drag.current;
    if (d) {
      if (Math.hypot(cx - d.sx, cy - d.sy) > 3) d.moved = true;
      if (!d.moved) return;
      if (d.mode === 'pan') {
        userMoved.current = true;
        view.current = { ...view.current, x: d.vx + cx - d.sx, y: d.vy + cy - d.sy };
        draw();
      } else if (d.id) {
        const p = sim.current.get(d.id)!;
        const w = toWorld(cx, cy);
        p.x = w.x; p.y = w.y; p.pinned = true;
        alpha.current = Math.max(alpha.current, 0.25);
        start();
      }
      return;
    }
    const n = hit(cx, cy);
    const id = n?.id || null;
    if (id !== hover.current) {
      hover.current = id;
      canvasRef.current!.style.cursor = id ? 'pointer' : 'grab';
      draw();
    }
    const tip = tipRef.current;
    if (tip) {
      const gn = id ? props.current.nodes.find(x => x.id === id) : null;
      if (gn) {
        tip.style.display = 'block';
        tip.style.left = `${Math.min(cx + 14, size.current.w - 260)}px`;
        tip.style.top = `${cy + 14}px`;
        tip.innerHTML = '';
        const t = document.createElement('b'); t.textContent = gn.title; tip.appendChild(t);
        const meta = document.createElement('div');
        meta.className = 'kg-tip-meta';
        meta.textContent = gn.missing
          ? 'Linked to, but no note yet — click to create it'
          : `${gn.observations} facts · ${gn.degree} links${gn.conflicts ? ` · ⚠ ${gn.conflicts} conflict${gn.conflicts > 1 ? 's' : ''}` : ''}${gn.tags.length ? ' · #' + gn.tags.slice(0, 4).join(' #') : ''}`;
        tip.appendChild(meta);
        if (gn.preview) { const pv = document.createElement('div'); pv.className = 'kg-tip-preview'; pv.textContent = gn.preview.slice(0, 160); tip.appendChild(pv); }
      } else tip.style.display = 'none';
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    canvasRef.current?.releasePointerCapture(e.pointerId);
    if (d && !d.moved) onSelect(d.id || null);
  };
  const onDoubleClick = (e: React.MouseEvent) => {
    const { cx, cy } = local(e);
    const n = hit(cx, cy);
    if (n && !neuron.current) { n.pinned = false; alpha.current = Math.max(alpha.current, 0.3); start(); }
  };
  const onWheel = (e: WheelEvent) => {
    e.preventDefault(); // zoom the graph, not the page
    userMoved.current = true;
    const r = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    const { w, h } = size.current;
    const v = view.current;
    const k = Math.max(0.1, Math.min(5, v.k * Math.exp(-e.deltaY * 0.0015)));
    // zoom around the cursor
    const px = cx - w / 2, py = cy - h / 2;
    view.current = { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    draw();
  };

  wheelRef.current = onWheel;

  return (
    <div className="kg-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="kg-canvas" role="img" aria-label="Knowledge graph"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
        onPointerLeave={() => { hover.current = null; if (tipRef.current) tipRef.current.style.display = 'none'; draw(); }}
        onDoubleClick={onDoubleClick} />
      <div className="kg-tip" ref={tipRef} />
    </div>
  );
});

export default KnowledgeGraph;
