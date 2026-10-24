# Knowledge base guide

ZenSynora's knowledge base is a folder of Markdown notes that agents read and
write, plus a search index built from those files. The design comes from
[memopad](https://github.com/adrianx26/memopad): **the files are the source
of truth**, and the index can always be rebuilt from them.

| What | Where |
|---|---|
| Notes of the shared knowledge base | `~/.myclaw/knowledge/default/*.md` (sub-folders allowed) |
| Other knowledge bases (e.g. a WhatsApp user) | `~/.myclaw/knowledge/<base>/` |
| Deleted notes, and earlier versions of overwritten notes | `~/.myclaw/knowledge/<base>/.trash/` (never erased) |
| Search index (rebuildable) | `~/.myclaw/knowledge_<base>.db` |
| Another folder for the notes | `knowledge.knowledge_dir` in `~/.myclaw/config.json` |

## A note

```markdown
---
title: "Home server"
permalink: home-server
tags: ["homelab", "linux"]
aliases: ["mindy01", "the PC"]
type: device
status: active
created: 2026-10-23T10:00:00
updated: 2026-10-23T10:00:00
---

# Home server

Runs Ubuntu 24.04 and hosts ZenSynora. Free text, as long as you like.

- [ ] replace the fan            ← a task, stays text (not a fact)

## Observations

- [spec] 32 GB RAM #hardware
- [setting] Ollama listens on port 11434

## Relations

- runs [[ZenSynora]]
- part_of [[Homelab]]
```

* **Frontmatter** — `title`, `permalink` (the note's id), `tags`, `aliases`,
  `created`, `updated`. Tags and aliases may be written as `[a, b]`,
  `a, b`, a single word, or a YAML list (`- a`). Every other key (`type`,
  `status`, `source`, …) is kept as **metadata** and is searchable.
* **Facts** (observations): `- [category] text #tag` — under any heading
  (the generated *Observations* section is just the usual place), never inside
  a code block. Task checkboxes `- [ ]` / `- [x]` are text, not facts.
* **Links** (relations): `- relation [[Other note]]`. The target can be the
  other note's title, permalink, file name or one of its aliases (case does
  not matter; `[[Note|label]]` and `[[Note#Section]]` work too).
* A link to a note that **does not exist yet** is remembered and connects by
  itself as soon as that note is written. In the Web UI graph it is a dashed
  dot (*Create this note*). Deleting a note turns links to it back into such
  waiting links, so restoring it from `.trash` reconnects them.
* Editing a note never removes the links *other* notes have to it.

## Searching

Agents, the Web UI and the CLI search titles, note text, tags and facts.

| You type | Finds |
|---|---|
| `ollama port` | notes with both words (free text; a long sentence falls back to "any word") |
| `ollama OR lmstudio` | either word |
| `ollama NOT docker` | `ollama`, but not notes that mention `docker` |
| `tag:homelab NOT nas` (agent tool) | notes tagged `homelab` that do not mention `nas`; `NOT nas` alone = every note without `nas` |
| `"home server"` | the exact phrase |
| `pyth*` | words starting with `pyth` |
| `tag:homelab` (agent tool) | notes with that tag (frontmatter or fact tags); `tag:homelab ollama` combines both |

AND / OR / NOT must be written in capitals; in lowercase they are ordinary
words. Tag search matches whole tags (`py` does not find `python`).

## What agents see

* **Before answering**, an agent searches the knowledge base with the user's
  message and gets the best notes as *Relevant Knowledge*: the beginning of
  each note's text (about 500 characters) and up to 3 facts, at most about
  2000 characters in all. A `memory://<permalink>` in a message always adds
  that note.
* **Tools:**

| Tool | What it does |
|---|---|
| `search_knowledge(query, limit)` | Search (syntax above); one-line snippet per result |
| `read_knowledge(permalink)` | The whole note: text (up to 6000 characters), facts, links, waiting links, aliases, metadata, *Linked from* |
| `write_to_knowledge(title, content, tags, observations, relations)` | Save a note. Writing the same title again **replaces** the note's text, facts and links (aliases and metadata are kept); if anything would be lost, the previous version is first kept in `.trash/` |
| `get_knowledge_context(permalink, depth)` | The note with its text plus the notes around it (snippets, *Linked from*); hub notes are not walked through |
| `list_knowledge`, `get_related_knowledge`, `list_knowledge_tags`, `sync_knowledge_base` | List, follow links, tags, re-index |

* Agents never store passwords, API keys, tokens or other secrets in the
  knowledge base, and do not save snapshots of system state (health, errors,
  listings) — they go stale.
* **Session summaries:** in long conversations a summary is added to one
  note per day, `session-summary-YYYYMMDD`, as a `### HH:MM UTC` section
  (before 2026-10-23 every turn made a new note).
* `knowledge.enabled: false` in the config turns off everything automatic
  (context search, auto-extraction, saving tool results, session summaries,
  knowledge-gap hints); the tools above still work when an agent calls them.

## Command line

```bash
zensynora knowledge search "ollama OR lmstudio"      # --base/-b NAME for another base
zensynora knowledge read home-server                 # text, facts and links
zensynora knowledge write                            # asks for title, text and tags
zensynora knowledge list
zensynora knowledge tags
zensynora knowledge sync            # re-read files you edited by hand
zensynora knowledge sync --force    # rebuild the whole index from the files
```

## Editing by hand, folders, `.bmignore`

Edit the files with any editor (Obsidian works). Run *↻ Re-index* on the
Knowledge page or `zensynora knowledge sync` afterwards. Moving or renaming a
file keeps its links. Folders whose name starts with `.` (`.trash`, `.git`,
`.obsidian`) are never indexed. A `.bmignore` file in the base folder can
exclude more, one pattern per line: `drafts/`, `*.tmp`, `archive/**`.

## MCP

`zensynora mcp-server` offers the agent tools — the knowledge tools included —
to other MCP clients. Parameters have real types (numbers, true/false), and
the tools always use the shared `default` knowledge base there.

## Quality: fact sources, categories, conflicts, duplicates

Added 2026-10-24 (KB phase 2). Everything below is worked out from the files
and kept in the index; a note file only changes when you (or an agent
resolving one conflict) choose so, and the previous version is kept in
`.trash/`.

**Where a fact comes from.** A fact line may end with its source:

```markdown
- [spec] 32 GB RAM #hardware (source: tool shell free -g)
- [url] Ollama docs (source: tool browse https://ollama.com/docs)
- [preference] Answers in English (source: chat 2026-10-24)
```

Only this exact `(source: …)` form is special; any other `(…)` at the end
of a fact stays part of the fact. Facts saved automatically get their source
(`chat <date>` from a conversation, `tool <name> <URL or path>` from a tool
result). Agents can give one as a third field: `spec | 32 GB RAM | tool shell
free -g`. `read_knowledge` and the note panel on the Knowledge page show it.

**Categories.** `[Specs]`, `[spec]` and `[specification]` are counted as one
category, `spec`: lower case, singular, and common synonyms (`config` /
`settings` → `setting`, `prefs` → `preference`, `link` → `url`, `howto` →
`how-to`). The file keeps what you wrote. *Tags & types* lists the
categories with their counts (click one to filter) and suggests merging a
rare spelling (used 1–2 times) into a common one (3+), e.g. `Specs → spec`
or the typo `preferance → preference`. *Preview…* shows every fact that
would change; *Apply* rewrites those notes.

**Conflicts.** When a note is saved or re-indexed, facts of the same category
on that note are compared. Only clear contradictions are flagged ⚠:

| Flagged | Not flagged |
|---|---|
| `32 GB RAM` / `64 GB RAM` | the same fact twice, `32GB RAM` / `32 GB RAM` |
| `listens on port 11434` / `listens on port 8080` | `32 GB RAM` / `32 GB RAM DDR4` (one contains the other) |
| `port: 80` / `port: 8080`, `OS = Debian` / `OS = Ubuntu` | `Server 1 has …` / `Server 2 has …` (two things) |
| `Default model is llama3` / `Default model is qwen2.5` | dates and times (`backup ran on 2026-10-01` / `… 10-02`), `Step 1` / `Step 2` |
|  | differently worded or unrelated facts, `Python is installed` / `Python is fast` |

Open the note (notes with conflicts have an amber dot in the graph):
**Keep A** or **Keep B** removes the other fact from the note file; **Both
are fine** dismisses the pair for good. A conflict closes by itself when you
edit one of the two facts away.

**Duplicate notes.** *⧉ Find duplicates* lists pairs with the same title, a
title that is the other note's alias, the same text, or nearly the same
short text. Nothing is merged automatically: pick a pair, check the preview
(tags, aliases, facts and links of both; the other note's text under
`## Merged from <title>`), then *Merge*. The merged note's title, aliases and
file name become aliases of the kept note, so `[[links]]` to it keep working;
its file goes to `.trash/`.

**Hub notes.** A note with more than 25 links (a "hub" such as *Homelab*)
matches many questions but says little about each one. In the automatic
*Relevant Knowledge* it is ranked lower (never left out on purpose), and
`get_knowledge_context` with depth 2 or more lists it without following its
links.

**Agent tools:** `knowledge_conflicts(permalink)`,
`resolve_knowledge_conflict(conflict_id, keep)` (`"a"`, `"b"` or `"both"`),
`knowledge_categories()`, `find_duplicate_notes()` (a list only). Merging
notes or categories is left to you on the Knowledge page.

**Settings → Features** (`~/.myclaw/config.json`):

| Setting | Default | |
|---|---|---|
| `knowledge.detect_conflicts` | `true` | flag contradicting facts (only flags) |
| `knowledge.hub_penalty` | `true` | rank hub notes lower, do not walk through them |
| `knowledge.hub_degree` | `25` | links above which a note is a hub |

## Plans

What comes next (more of memopad's features: note editing tools, semantic
search, distillation into facts and a persona, importing repositories and
documents) is in
[docs/dev/plans/memopad_port_plan.md](dev/plans/memopad_port_plan.md).
