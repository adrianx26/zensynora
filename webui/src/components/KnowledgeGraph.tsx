// Force-directed graph of the knowledge base, drawn on a <canvas> (no extra
// libraries). Drag the background to pan, the wheel to zoom, drag a note to
// move it (it stays pinned; double-click it to release), click to select.
import React, { useCallback, useEffect, useImperativeHandle, useRef } from 'react';

export interface GNode {
  id: string;
  title: string;
  tags: string[];
  observations: number;
  relations: number;
  degree: number;
  missing: boolean;
  preview?: string;
}
export interface GEdge { source: string; target: string; type: string }

interface SimNode { id: string; x: number; y: number; vx: number; vy: number; pinned: boolean; r: number }

export interface GraphHandle { fit: () => void; reheat: () => void; focus: (id: string) => void }

interface Props {
  nodes: GNode[];
  edges: GEdge[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  colorOf: (n: GNode) => string;
  showEdgeLabels: boolean;
  paused: boolean;
}

const LINK_LEN = 110;

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const KnowledgeGraph = React.forwardRef<GraphHandle, Props>(function KnowledgeGraph(
  { nodes, edges, selected, onSelect, colorOf, showEdgeLabels, paused }, ref,
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
  const props = useRef({ nodes, edges, selected, colorOf, showEdgeLabels, paused });
  props.current = { nodes, edges, selected, colorOf, showEdgeLabels, paused };
  const didFit = useRef(false);
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
    const ids = new Set(nodes.map(n => n.id));
    for (const id of [...m.keys()]) if (!ids.has(id)) m.delete(id);
    const placed = [...m.values()];
    const cx = placed.length ? placed.reduce((s, n) => s + n.x, 0) / placed.length : 0;
    const cy = placed.length ? placed.reduce((s, n) => s + n.y, 0) / placed.length : 0;
    let i = 0;
    for (const n of nodes) {
      const r = n.missing ? 5 : 6 + Math.min(14, Math.sqrt(n.degree) * 3 + n.observations * 0.3);
      const old = m.get(n.id);
      if (old) { old.r = r; continue; }
      // Start new notes near a neighbour that already has a place, else on a spiral.
      const nb = edges.find(e => (e.source === n.id && m.has(e.target)) || (e.target === n.id && m.has(e.source)));
      const anchor = nb ? m.get(nb.source === n.id ? nb.target : nb.source) : null;
      const a = i * 2.39996;
      const d = 30 * Math.sqrt(i + 1);
      m.set(n.id, {
        id: n.id, r, pinned: false, vx: 0, vy: 0,
        x: anchor ? anchor.x + (Math.random() - 0.5) * 60 : cx + Math.cos(a) * d,
        y: anchor ? anchor.y + (Math.random() - 0.5) * 60 : cy + Math.sin(a) * d,
      });
      i++;
    }
    alpha.current = Math.max(alpha.current, i > 0 ? 1 : 0.3);
    if (i > 0 && placed.length === 0) didFit.current = false;
    start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

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
        const f = rep / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
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
    for (const p of list) {
      p.vx -= p.x * 0.004 * a;
      p.vy -= p.y * 0.004 * a;
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
    const { w, h } = size.current;
    const dpr = window.devicePixelRatio || 1;
    const { x: tx, y: ty, k } = view.current;
    const th = theme.current;
    const { nodes: ns, edges: es, selected: sel, colorOf: col, showEdgeLabels: labels } = props.current;
    const focus = sel || hover.current;
    const near = neighbours(focus);
    const m = sim.current;

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
      ctx.strokeStyle = on ? th.accent : th.edge;
      ctx.globalAlpha = dim ? 0.15 : 1;
      ctx.lineWidth = (on ? 1.8 : 1) / Math.sqrt(k);
      const dx = t.x - s.x, dy = t.y - s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / d, uy = dy / d;
      const ex = t.x - ux * (t.r + 2), ey = t.y - uy * (t.r + 2);
      ctx.beginPath();
      ctx.moveTo(s.x + ux * s.r, s.y + uy * s.r);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      // arrow head
      const ah = 6 / Math.sqrt(k);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(ex, ey);
      ctx.lineTo(ex - ux * ah - uy * ah * 0.55, ey - uy * ah + ux * ah * 0.55);
      ctx.lineTo(ex - ux * ah + uy * ah * 0.55, ey - uy * ah - ux * ah * 0.55);
      ctx.closePath();
      ctx.fill();
      if ((labels && k > 0.8) || on) {
        ctx.font = `${10 / Math.sqrt(k)}px Inter, sans-serif`;
        ctx.fillStyle = on ? th.accent : th.muted;
        ctx.textAlign = 'center';
        // offset to the side of the line, so A→B and B→A labels do not overlap
        const off = 7 / Math.sqrt(k);
        ctx.fillText(e.type.replace(/_/g, ' '), (s.x + t.x) / 2 - uy * off, (s.y + t.y) / 2 + ux * off + 3 / Math.sqrt(k));
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
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
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
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      if (p.id === sel) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = th.accent;
        ctx.lineWidth = 2.5;
        ctx.stroke();
      }
      if (p.pinned) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
        ctx.fillStyle = th.bg;
        ctx.fill();
      }
      const showLabel = near.has(p.id) || k > 1.1 || (k > 0.6 && n.degree >= 3) || ns.length <= 40;
      if (showLabel && !(dim && k < 1.5)) {
        const fs = 11 / Math.sqrt(k);
        ctx.font = `${p.id === sel ? 600 : 400} ${fs}px Inter, sans-serif`;
        ctx.textAlign = 'center';
        ctx.lineWidth = 3 / k;
        ctx.strokeStyle = th.bg;
        const label = n.title.length > 28 ? n.title.slice(0, 27) + '…' : n.title;
        ctx.strokeText(label, p.x, p.y + p.r + fs + 1);
        ctx.fillStyle = n.missing ? th.muted : th.text;
        ctx.fillText(label, p.x, p.y + p.r + fs + 1);
      }
    }
    ctx.globalAlpha = 1;
  };

  const fit = useCallback(() => {
    const list = [...sim.current.values()];
    if (!list.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of list) { x0 = Math.min(x0, p.x - p.r); y0 = Math.min(y0, p.y - p.r); x1 = Math.max(x1, p.x + p.r); y1 = Math.max(y1, p.y + p.r); }
    const { w, h } = size.current;
    const k = Math.max(0.15, Math.min(2.2, Math.min((w - 60) / (x1 - x0 || 1), (h - 60) / (y1 - y0 || 1))));
    view.current = { k, x: -((x0 + x1) / 2) * k, y: -((y0 + y1) / 2) * k };
    draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loop = () => {
    raf.current = null;
    if (!props.current.paused && alpha.current > 0.004) {
      tick();
      // fit once while the first layout settles
      if (!didFit.current && alpha.current < 0.25) { didFit.current = true; fit(); }
    }
    draw();
    if (!props.current.paused && alpha.current > 0.004) raf.current = requestAnimationFrame(loop);
  };
  function start() {
    if (raf.current == null) raf.current = requestAnimationFrame(loop);
  }

  useImperativeHandle(ref, () => ({
    fit,
    reheat: () => {
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
      if (d < n.r + 4 / view.current.k && d < bd) { best = n; bd = d; }
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
    drag.current = { mode: n ? 'node' : 'pan', id: n?.id, sx: cx, sy: cy, vx: view.current.x, vy: view.current.y, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const { cx, cy } = local(e);
    const d = drag.current;
    if (d) {
      if (Math.hypot(cx - d.sx, cy - d.sy) > 3) d.moved = true;
      if (!d.moved) return;
      if (d.mode === 'pan') {
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
          : `${gn.observations} facts · ${gn.degree} links${gn.tags.length ? ' · #' + gn.tags.slice(0, 4).join(' #') : ''}`;
        tip.appendChild(meta);
        if (gn.preview) { const pv = document.createElement('div'); pv.className = 'kg-tip-preview'; pv.textContent = gn.preview.slice(0, 160); tip.appendChild(pv); }
      } else tip.style.display = 'none';
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    canvasRef.current?.releasePointerCapture(e.pointerId);
    if (d && !d.moved) onSelect(d.mode === 'node' ? d.id || null : null);
  };
  const onDoubleClick = (e: React.MouseEvent) => {
    const { cx, cy } = local(e);
    const n = hit(cx, cy);
    if (n) { n.pinned = false; alpha.current = Math.max(alpha.current, 0.3); start(); }
  };
  const onWheel = (e: WheelEvent) => {
    e.preventDefault(); // zoom the graph, not the page
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
