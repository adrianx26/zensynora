// Helpers for the infographic chart kit (components/viz.tsx), 2026-11-01.
import { useLayoutEffect, useRef, useState } from 'react';
import type React from 'react';

export const fmt = (n: number | null | undefined): string => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const a = Math.abs(n);
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (a >= 10_000) return `${(n / 1000).toFixed(1)}K`;
  return n.toLocaleString();
};

export const pct = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`;

/** Width of an element, kept up to date (charts draw at the width shown). */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => setW(Math.max(160, Math.floor(el.getBoundingClientRect().width)));
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Save a panel as a PNG (what is on screen, in the current theme). */
export async function downloadPanel(node: HTMLElement | null, name: string): Promise<void> {
  if (!node) return;
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg-color').trim() || '#0d1117';
  const { toPng } = await import('html-to-image'); // loaded only when someone downloads
  // The image copy does not resolve CSS variables inside SVG (bars came out black):
  // write the resolved colours onto the SVG elements for the export, then restore.
  const touched: [SVGElement, string | null][] = [];
  node.querySelectorAll<SVGElement>('svg *').forEach(el => {
    const cs = getComputedStyle(el);
    touched.push([el, el.getAttribute('style')]);
    el.style.fill = cs.fill;
    el.style.stroke = cs.stroke;
  });
  let url: string;
  try {
    url = await toPng(node, { backgroundColor: bg, pixelRatio: 2, cacheBust: true,
      filter: n => !(n instanceof HTMLElement && n.dataset.noExport === '1') });
  } finally {
    touched.forEach(([el, style]) => (style === null ? el.removeAttribute('style') : el.setAttribute('style', style)));
  }
  const a = document.createElement('a');
  a.href = url;
  a.download = `${name}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.png`;
  a.click();
}

