"""Generate dark-themed HTML pages for every mermaid diagram in the repo.

Walks the repo root for *.md files containing ```mermaid blocks, extracts each
block with the nearest preceding heading as its title, and emits one HTML page
per source file plus an index.html. The styling matches the dark grid theme
defined in assets/theme.css.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent

MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
SKIP_DIRS = {".git", ".claude", "node_modules", "archive", "graphify-out", ".kilo", ".opencode", "Scrapling", "webui", "tests", "eval"}


@dataclass
class Diagram:
    title: str
    description: str
    code: str


@dataclass
class SourceDoc:
    path: Path
    rel: str
    slug: str
    title: str
    diagrams: list[Diagram]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "diagram"


def find_heading_before(text: str, pos: int) -> tuple[str, str]:
    """Return (heading_text, paragraph_after_heading) for the nearest heading before pos."""
    last_heading = ""
    last_para = ""
    for m in HEADING_RE.finditer(text, 0, pos):
        last_heading = m.group(2).strip()
        # capture the line(s) right after the heading until next heading or code fence
        after_start = m.end()
        snippet = text[after_start:pos]
        # take only the first non-empty paragraph that isn't a code fence
        paras = [p.strip() for p in snippet.split("\n\n") if p.strip()]
        last_para = ""
        for p in paras:
            if p.startswith("```"):
                break
            if not p.startswith("#"):
                last_para = re.sub(r"\s+", " ", p)[:240]
                break
    return last_heading, last_para


def extract_diagrams(md_path: Path) -> list[Diagram]:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    diagrams: list[Diagram] = []
    for i, m in enumerate(MERMAID_RE.finditer(text), 1):
        code = m.group(1).rstrip()
        title, desc = find_heading_before(text, m.start())
        if not title:
            title = f"Diagram {i}"
        diagrams.append(Diagram(title=title, description=desc, code=code))
    return diagrams


def doc_title(md_path: Path, fallback: str) -> str:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def collect_sources() -> list[SourceDoc]:
    sources: list[SourceDoc] = []
    for md in ROOT.rglob("*.md"):
        # skip excluded directories
        if any(part in SKIP_DIRS for part in md.relative_to(ROOT).parts[:-1]):
            continue
        if md.is_relative_to(OUT):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "```mermaid" not in text:
            continue
        diagrams = extract_diagrams(md)
        if not diagrams:
            continue
        rel = md.relative_to(ROOT).as_posix()
        slug = slugify(rel.replace("/", "-").removesuffix(".md"))
        sources.append(
            SourceDoc(
                path=md,
                rel=rel,
                slug=slug,
                title=doc_title(md, md.stem.replace("_", " ").title()),
                diagrams=diagrams,
            )
        )
    sources.sort(key=lambda s: (-len(s.diagrams), s.rel))
    return sources


PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="{css}" />
</head>
<body>
<div class="page">
"""

PAGE_TAIL = """
<div class="footer">
  ZenSynora (MyClaw) <span class="sep">•</span> Architecture Diagrams <span class="sep">•</span> Generated from {rel}
</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
mermaid.initialize({{
  startOnLoad: true,
  theme: 'base',
  fontFamily: "'JetBrains Mono', 'Consolas', monospace",
  securityLevel: 'loose',
  themeVariables: {{
    background: '#0a0e14',
    primaryColor: '#131924',
    primaryTextColor: '#cbd5e1',
    primaryBorderColor: '#5eead4',
    lineColor: '#475569',
    secondaryColor: '#0f141d',
    tertiaryColor: '#0a0e14',
    clusterBkg: 'rgba(15, 20, 29, 0.5)',
    clusterBorder: '#2a3543',
    edgeLabelBackground: '#0f141d',
    nodeBorder: '#5eead4',
    mainBkg: '#131924',
    titleColor: '#2dd4bf',
    actorBorder: '#7dd3fc',
    actorBkg: '#131924',
    actorTextColor: '#cbd5e1',
    actorLineColor: '#2a3543',
    signalColor: '#cbd5e1',
    signalTextColor: '#cbd5e1',
    labelBoxBkgColor: '#131924',
    labelBoxBorderColor: '#5eead4',
    labelTextColor: '#cbd5e1',
    loopTextColor: '#cbd5e1',
    noteBorderColor: '#c084fc',
    noteBkgColor: '#131924',
    noteTextColor: '#cbd5e1',
    activationBkgColor: '#0f141d',
    activationBorderColor: '#86efac'
  }},
  flowchart: {{ curve: 'basis', padding: 16, htmlLabels: true }},
  sequence: {{ useMaxWidth: true, mirrorActors: false }}
}});
</script>
</body>
</html>
"""


def render_diagram_card(d: Diagram) -> str:
    desc = f'<p class="diagram-desc">{html.escape(d.description)}</p>' if d.description else ""
    return (
        '<div class="diagram-card">\n'
        f'  <h2 class="diagram-title">{html.escape(d.title)}</h2>\n'
        f'  {desc}\n'
        f'  <div class="mermaid">{html.escape(d.code)}</div>\n'
        '</div>\n'
    )


def render_legend() -> str:
    return (
        '<div class="legend">\n'
        '  <div class="legend-item"><span class="legend-swatch external"></span>External / User</div>\n'
        '  <div class="legend-item"><span class="legend-swatch frontend"></span>Frontend / Gateway</div>\n'
        '  <div class="legend-item"><span class="legend-swatch backend"></span>Backend Service</div>\n'
        '  <div class="legend-item"><span class="legend-swatch database"></span>Database / Storage</div>\n'
        '</div>\n'
    )


def render_source_page(src: SourceDoc) -> str:
    parts = [
        PAGE_HEAD.format(title=html.escape(src.title), css="assets/theme.css"),
        '<a class="back-link" href="index.html">← All diagrams</a>\n',
        '<div class="header">\n',
        '  <span class="dot"></span>\n',
        f'  <h1 class="title">{html.escape(src.title)}</h1>\n',
        '</div>\n',
        f'<p class="subtitle">{len(src.diagrams)} diagram(s) extracted from <code>{html.escape(src.rel)}</code></p>\n',
        render_legend(),
    ]
    for d in src.diagrams:
        parts.append(render_diagram_card(d))
    parts.append(PAGE_TAIL.format(rel=html.escape(src.rel)))
    return "".join(parts)


def render_index(sources: list[SourceDoc]) -> str:
    total = sum(len(s.diagrams) for s in sources)
    cards = []
    for s in sources:
        cards.append(
            f'<a class="index-card" href="{html.escape(s.slug)}.html">\n'
            f'  <h2>{html.escape(s.title)}</h2>\n'
            f'  <p>{html.escape(s.rel)}</p>\n'
            f'  <span class="count">{len(s.diagrams)} diagram(s)</span>\n'
            '</a>\n'
        )
    body = (
        PAGE_HEAD.format(title="ZenSynora Diagrams", css="assets/theme.css")
        + '<div class="header">\n'
        + '  <span class="dot"></span>\n'
        + '  <h1 class="title">ZenSynora — All Diagrams</h1>\n'
        + '</div>\n'
        + f'<p class="subtitle">Architecture, workflow, and sequence diagrams across the repository</p>\n'
        + f'<p class="meta">{len(sources)} source files <span class="sep">·</span> {total} diagrams total</p>\n'
        + render_legend()
        + '<div class="index-grid">\n'
        + "".join(cards)
        + '</div>\n'
        + '<div class="footer">ZenSynora (MyClaw) <span class="sep">•</span> Diagram Index <span class="sep">•</span> Auto-generated</div>\n'
        + '</div>\n</body>\n</html>\n'
    )
    return body


def main() -> None:
    sources = collect_sources()
    print(f"Found {len(sources)} markdown files with mermaid diagrams")
    for s in sources:
        out_path = OUT / f"{s.slug}.html"
        out_path.write_text(render_source_page(s), encoding="utf-8")
        print(f"  wrote {out_path.name}  ({len(s.diagrams)} diagrams)")
    (OUT / "index.html").write_text(render_index(sources), encoding="utf-8")
    print(f"  wrote index.html  ({sum(len(s.diagrams) for s in sources)} total diagrams)")


if __name__ == "__main__":
    main()
