# Knowledge base guide

ZenSynora's knowledge base is a folder of Markdown notes that agents read and
write, plus a search index built from those files. The design comes from
[memopad](https://github.com/adrianx26/memopad): **the files are the source
of truth**, and the index can always be rebuilt from them.

| What | Where |
|---|---|
| Notes of the shared knowledge base | `~/.myclaw/knowledge/default/*.md` (sub-folders allowed) |
| Other knowledge bases (e.g. a WhatsApp user) | `~/.myclaw/knowledge/<base>/` |
| Deleted notes | `~/.myclaw/knowledge/<base>/.trash/` (never erased) |
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
* **Facts** (observations): `- [category] text #tag`.
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
| `write_to_knowledge(title, content, tags, observations, relations)` | Save a note. Writing the same title again **replaces** the note's text, facts and links (aliases and metadata are kept) |
| `get_knowledge_context(permalink, depth)` | The note with its text plus the notes around it (snippets, *Linked from*) |
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

## Plans

What comes next (more of memopad's features: note editing tools, conflict
detection, semantic search, distillation into facts and a persona, importing
repositories and documents) is in
[docs/dev/plans/memopad_port_plan.md](dev/plans/memopad_port_plan.md).
