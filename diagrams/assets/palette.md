# ZenSynora Diagram Palette

Canonical color palette for all architecture diagrams. Use these tokens in any
new diagram (HTML, SVG, mermaid theme override) to keep the visual language
consistent.

## Surfaces (dark theme)

| Token              | Hex        | Use                                  |
|--------------------|------------|--------------------------------------|
| `--bg`             | `#0a0e14`  | Page background                      |
| `--bg-elevated`    | `#0f141d`  | Card / cluster background            |
| `--bg-card`        | `#131924`  | Node interior                        |
| `--border`         | `#1e2733`  | Subtle separators                    |
| `--border-strong`  | `#2a3543`  | Cluster outlines                     |
| `--grid`           | `rgba(125,211,252,0.04)` | Page grid lines        |
| `--grid-strong`    | `rgba(125,211,252,0.08)` | Card grid lines        |

## Text

| Token              | Hex        | Use                                  |
|--------------------|------------|--------------------------------------|
| `--text`           | `#cbd5e1`  | Body / node label                    |
| `--text-muted`     | `#94a3b8`  | Subtitle / secondary                 |
| `--text-dim`       | `#64748b`  | Edge labels / footer                 |

## Semantic categories

These map directly onto the screenshot palette (Analysis · Evaluation ·
Decision · Risk · Recommendation). Each has a stroke color (border + label) and
a soft tint used for subtle node-fill highlights.

| Token            | Hex        | Soft tint               | Category in screenshot       |
|------------------|------------|-------------------------|------------------------------|
| `--analysis`     | `#10b981`  | `rgba(16,185,129,0.10)` | Analysis Phase (green)       |
| `--evaluation`   | `#22d3ee`  | `rgba(34,211,238,0.10)` | Evaluation (cyan)            |
| `--decision`     | `#a855f7`  | `rgba(168,85,247,0.10)` | Decision (purple)            |
| `--risk`         | `#f87171`  | `rgba(248,113,113,0.10)`| Risk (red)                   |
| `--recommend`    | `#fbbf24`  | `rgba(251,191,36,0.10)` | Recommendations (amber)      |
| `--neutral`      | `#64748b`  | `rgba(100,116,139,0.10)`| Outputs / neutral (slate)    |

## Backwards-compatible aliases

The earlier diagrams used `external / frontend / backend / database`. These
remain valid and now map onto the new semantic tokens:

| Legacy alias  | Maps to        | Hex        |
|---------------|----------------|------------|
| `--external`  | recommend      | `#fbbf24`  |
| `--frontend`  | evaluation     | `#22d3ee`  |
| `--backend`   | analysis       | `#10b981`  |
| `--database`  | decision       | `#a855f7`  |

## Accent

`--accent` (the cyan dot in the title) = `#22d3ee` (same as `--evaluation`).

## Mermaid themeVariables (for auto-rendered diagrams)

```js
{
  background: '#0a0e14',
  primaryColor: '#131924',
  primaryTextColor: '#cbd5e1',
  primaryBorderColor: '#22d3ee',
  lineColor: '#64748b',
  clusterBkg: 'rgba(15, 20, 29, 0.5)',
  clusterBorder: '#2a3543',
  edgeLabelBackground: '#0f141d',
  titleColor: '#22d3ee',
  actorBorder: '#22d3ee',
  actorBkg: '#131924',
  noteBorderColor: '#a855f7',
  noteBkgColor: '#131924'
}
```

## When to use which color

- **Analysis (green)** — processing steps, agents, transforms, "doing work"
- **Evaluation (cyan)** — gateways, interfaces, scoring, ranking, comparison
- **Decision (purple)** — storage, frameworks, weighted choices, persistence
- **Risk (red)** — critical paths, failures, security boundaries
- **Recommendation (amber)** — outputs, suggestions, external/user touch points
- **Neutral (slate)** — passive outputs, deliverables, metrics
