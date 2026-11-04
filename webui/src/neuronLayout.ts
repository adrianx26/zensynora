// "Neuron" layout for the knowledge graph (2026-11-04).
//
// Every group of linked notes becomes one radial burst: its best-connected note
// (the hub) in the centre, the notes it links to on the first ring, theirs on the
// next ring, and so on (a breadth-first tree). Each branch gets a slice of the
// circle in proportion to how many notes hang from it, and rings grow until
// neighbouring notes are at least GAP apart. Groups are then placed side by
// side without overlapping, the biggest in the middle. Notes without links form
// one more cluster (a sunflower spiral). Deterministic: same data, same picture.

export interface NeuronPos {
  x: number; y: number;
  angle: number;           // direction from the group centre
  bend: number;            // -1..1: which way its branch curls (fixed per note)
  parent: string | null;   // tree parent (the branch it hangs from); null for hubs and unlinked notes
  depth: number;           // 0 = hub
  group: number;           // index of the group, biggest first; -1 = unlinked notes
  hub: boolean;
}
export interface NeuronGroup { cx: number; cy: number; r: number; hub: string | null; size: number }

const STEP = 72;      // distance between rings
const GAP = 16;       // smallest distance between two notes on one ring
const MARGIN = 50;    // space between groups

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 1000) / 1000;   // 0..1
}

export function neuronLayout(ids: string[], edges: { source: string; target: string }[]):
    { pos: Map<string, NeuronPos>; groups: NeuronGroup[] } {
  const adj = new Map<string, Set<string>>(ids.map(id => [id, new Set<string>()]));
  for (const e of edges) {
    if (e.source === e.target || !adj.has(e.source) || !adj.has(e.target)) continue;
    adj.get(e.source)!.add(e.target);
    adj.get(e.target)!.add(e.source);
  }
  const deg = (id: string) => adj.get(id)!.size;

  // groups of linked notes, biggest first
  const seen = new Set<string>();
  const comps: string[][] = [];
  const singles: string[] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    if (deg(id) === 0) { seen.add(id); singles.push(id); continue; }
    const comp: string[] = [];
    const queue = [id];
    seen.add(id);
    while (queue.length) {
      const v = queue.pop()!;
      comp.push(v);
      for (const w of adj.get(v)!) if (!seen.has(w)) { seen.add(w); queue.push(w); }
    }
    comps.push(comp);
  }
  comps.sort((a, b) => b.length - a.length);

  const pos = new Map<string, NeuronPos>();
  const local: { r: number; hub: string | null; size: number; members: string[] }[] = [];

  comps.forEach((comp, g) => {
    const hub = comp.reduce((best, v) => (deg(v) > deg(best) || (deg(v) === deg(best) && v < best) ? v : best), comp[0]);
    // breadth-first tree from the hub; children of a note: best connected first
    const parent = new Map<string, string | null>([[hub, null]]);
    const children = new Map<string, string[]>();
    const depth = new Map<string, number>([[hub, 0]]);
    const order = [hub];
    for (let i = 0; i < order.length; i++) {
      const v = order[i];
      const kids = [...adj.get(v)!].filter(w => !parent.has(w)).sort((a, b) => deg(b) - deg(a) || (a < b ? -1 : 1));
      for (const w of kids) { parent.set(w, v); depth.set(w, depth.get(v)! + 1); order.push(w); }
      children.set(v, kids);
    }
    // notes hanging from each note (itself included)
    const weight = new Map<string, number>();
    for (let i = order.length - 1; i >= 0; i--) {
      const v = order[i];
      weight.set(v, 1 + (children.get(v) || []).reduce((s, w) => s + weight.get(w)!, 0));
    }
    // ring radii: far enough apart, and wide enough for the notes on them
    const perRing: number[] = [];
    for (const v of order) { const d = depth.get(v)!; perRing[d] = (perRing[d] || 0) + 1; }
    const ringR: number[] = [0];
    for (let d = 1; d < perRing.length; d++) {
      ringR[d] = Math.max(ringR[d - 1] + STEP, (perRing[d] * GAP) / (2 * Math.PI));
    }
    // angles: each child gets a slice by the square root of its weight (single notes keep some
    // room next to big branches), big and small branches alternating around the parent
    const range = new Map<string, [number, number]>([[hub, [0, 2 * Math.PI]]]);
    const slot = new Map<string, number>();   // place among its siblings
    for (const v of order) {
      const [a0, a1] = range.get(v)!;
      const byWeight = [...(children.get(v) || [])].sort((x, y) => weight.get(y)! - weight.get(x)! || (x < y ? -1 : 1));
      const kids: string[] = [];
      for (let i = 0, j = byWeight.length - 1; i <= j; i++, j--) { kids.push(byWeight[i]); if (i !== j) kids.push(byWeight[j]); }
      const share = (w: string) => Math.sqrt(weight.get(w)!);
      const total = kids.reduce((s, w) => s + share(w), 0);
      let a = a0;
      kids.forEach((w, i) => slot.set(w, i));
      for (const w of kids) {
        const span = ((a1 - a0) * share(w)) / (total || 1);
        range.set(w, [a, a + span]);
        a += span;
      }
    }
    for (const v of order) {
      const d = depth.get(v)!;
      const [a0, a1] = range.get(v)!;
      const ang = (a0 + a1) / 2;
      // a little organic unevenness, the same every time; single notes (no branch of their
      // own) step outwards in three layers so a crowded ring becomes a burst
      const leaf = weight.get(v) === 1 ? ((slot.get(v) || 0) % 3) * STEP * 0.42 : 0;
      const r = d === 0 ? 0 : ringR[d] + leaf + (hash(v) - 0.5) * STEP * 0.35;
      pos.set(v, { x: Math.cos(ang) * r, y: Math.sin(ang) * r, angle: ang, bend: hash(v + '~') * 2 - 1,
                   parent: parent.get(v) ?? null, depth: d, group: g, hub: d === 0 });
    }
    local.push({ r: (ringR[ringR.length - 1] || 0) + STEP * 1.0, hub, size: comp.length, members: comp });
  });

  if (singles.length) {
    const c = GAP * 0.62;
    singles.sort();
    singles.forEach((v, i) => {
      const r = c * Math.sqrt(i + 0.5) * 1.6, a = i * 2.39996;
      pos.set(v, { x: Math.cos(a) * r, y: Math.sin(a) * r, angle: a, bend: 0, parent: null, depth: 0, group: -1, hub: false });
    });
    local.push({ r: c * Math.sqrt(singles.length) * 1.6 + STEP * 0.3, hub: null, size: singles.length, members: singles });
  }

  // place the groups: biggest in the middle, the others around it without overlapping
  const groups: NeuronGroup[] = [];
  for (const L of local) {
    let cx = 0, cy = 0;
    if (groups.length) {
      for (let t = 1; ; t++) {
        const dist = 12 * t, ang = t * 0.5;
        cx = Math.cos(ang) * dist; cy = Math.sin(ang) * dist;
        if (groups.every(G => Math.hypot(G.cx - cx, G.cy - cy) >= G.r + L.r + MARGIN)) break;
      }
    }
    groups.push({ cx, cy, r: L.r, hub: L.hub, size: L.size });
    for (const v of L.members) { const p = pos.get(v)!; p.x += cx; p.y += cy; }
  }
  return { pos, groups };
}
