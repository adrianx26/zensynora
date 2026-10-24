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
| `search_knowledge(query, limit, mode)` | Search (syntax above); one-line snippet per result. `mode`: `auto` (default), `fts`, `semantic`, `hybrid` — see *Semantic search* |
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
zensynora knowledge embed           # vectors for semantic search (--all: check every note)
zensynora knowledge search "my car" --mode hybrid   # fts (default) | semantic | hybrid | auto
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

Only a `(source: …)` that closes at the very end of the line (only `#tags`
may follow) is special; any other `(…)` stays part of the fact. The same fact
from two sources is kept twice. Facts saved automatically get their source
(`chat <date>` from a conversation, `tool <name> <URL or path>` from a tool
result; shell commands are not written, and URLs lose passwords, `?query`
and `#fragment`). Agents can give one as a third field marked as a source:
`spec | 32 GB RAM | source: output of free -g` (a URL, `tool …` or `chat …`
needs no `source:`; any other `|` stays part of the fact, e.g. `tip | ps aux
| grep python`). `read_knowledge` and the note panel show it.

**Categories.** `[Specs]`, `[spec]` and `[specification]` are counted as one
category, `spec`: lower case, singular, and common synonyms (`config` /
`settings` → `setting`, `prefs` → `preference`, `link` → `url`, `howto` →
`how-to`). The file keeps what you wrote. *Tags & types* lists the
categories with their counts (click one to filter) and suggests merging a
rare spelling (used 1–2 times) into a common one (3+), e.g. `Specs → spec`
or the typo `preferance → preference`. *Preview…* shows every fact that
would change; *Apply* rewrites those notes.

**Conflicts.** When a note is saved or re-indexed, facts of the categories
that hold one current value — `setting` (also `config`), `spec`,
`preference` — on that note are compared. Only clear contradictions are
flagged ⚠: the same words with another number, or the same "subject key" (two
words or more) with another value.

| Flagged | Not flagged |
|---|---|
| `mindy01 has 32 GB RAM` / `mindy01 has 64 GB RAM` | the same fact twice, `32GB RAM` / `32 GB RAM`, one fact containing the other |
| `Ollama listens on port 11434` / `… port 8080` | names with digits: `mindy01` / `mindy02`, `slot A1` / `A2`, `sda1`, `eth0`; `Server 1` / `Server 2` |
| `Ollama port: 11434` / `Ollama port: 8080` | a one-word key (`Port: 80` / `Port: 8080`, `Model: …`, `Sister: …`): usually a list |
| `Default model is llama3` / `Default model is qwen2.5` | readings, counters, statuses, versions, errors, people: temperature, usage, tests, status, version, error, exit code, owner … |
|  | dates, times, years; other categories (`fact`, `how-to`, `url`, `person` …) |

With `knowledge.detect_conflicts` off, nothing new is flagged and existing
flags are hidden (they come back when it is turned on again).
Open the note (notes with conflicts have an amber dot in the graph):
**Keep A** or **Keep B** removes the other fact from the note file; **Both
are fine** dismisses the pair for good. A conflict closes by itself when you
edit one of the two facts away.

**Duplicate notes.** *⧉ Find duplicates* lists pairs with the same title, a
title that is the other note's alias, the same text, or nearly the same
short text with similar titles and the same numbers (notes made from one
template — daily weather, session summaries — are not duplicates). On very
large bases the check stops after a while and says the list is not
complete. Nothing is merged automatically: pick a pair, check the preview
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

## Importing: web pages, repositories, documents, chat exports

**Assimilate** reads one source into linked notes: an *overview* note (summary,
contents, facts `source` / `kind` / `size` / `date`, tags `assimilated` + the
kind, and a Mermaid diagram of the structure) plus one note per part, each
with `part_of [[overview]]`. Links between parts are kept (Markdown links
between files, imports between code files).

| Source | What you get |
|---|---|
| Web page (`https://…`) | main text split at headings, the page's links; a PDF/Word/Excel URL is read as that document |
| GitHub repository (`https://github.com/owner/repo`, also `/tree/branch/folder`) | architecture overview: languages, folders, entry points, dependencies, a diagram of the packages and their imports; one note per file read: README, manifests, entry points and the most-imported source files first, docs at most a quarter of the notes, archives / plans / changelogs / tests last (the overview says what was left out). No git needed; set `GITHUB_TOKEN` for private repositories or a higher rate limit |
| Folder or file on this computer | the same analysis for code folders (`.gitignore` honoured); documents: PDF, Word (.docx), Excel (.xlsx: one table per sheet, column names as facts), text, Markdown, HTML, CSV, JSON; images: file details only (no OCR) |

**Import** reads exports: ChatGPT (`.zip` or `conversations.json`), Claude.ai
(`.zip`, `conversations.json`, `projects.json`), an MCP memory file
(`memory.json` / `.jsonl`: entities → notes with facts, relations → links) and
Markdown folders / Obsidian vaults (folder or `.zip`). Each conversation becomes
one note (date, short transcript, tags `imported` + `chatgpt`/`claude`), grouped
under one note per month; the note's name comes from the conversation's id,
so a renamed conversation updates the same note. Markdown notes are **copied**
into `<base>/imported/<folder-name>/` with their frontmatter and text (symbolic
links are not followed, attachments are not copied). Importing the folder
again updates ZenSynora's own copy of a changed note (the old version goes to
`.trash/`); if you edited that copy, the new version is saved beside it once
(`name-2.md`, updated by later imports). A note whose permalink is already
taken gets `permalink: <folder>-…`.

* **Run it again** on the same source: the same notes are updated (unchanged
  ones are not rewritten; a version that loses text goes to `.trash/` first).
  A note you **edited by hand** since it was imported is kept as it is ("kept
  your edits"). A note **you wrote** that happens to have the same name is
  never replaced — the import is saved as `…-imported` and says so.
* **Limits:** 40 notes per assimilate by default (`max_notes`, at most 500;
  one note per file of a repository), 12 000 characters per note (longer parts
  are split), a byte and time limit per run; an import takes up to 10 000
  notes (`--max-notes`). At a limit you get what was done so far; running the
  same call again **continues** with the next files or parts (the progress is
  kept in `<base>/.ingest/`). After *Stop* or a timeout nothing more is written.
  Two imports can run at a time; a stuck Web UI import is stopped after
  30 minutes.
* **Big files:** Markdown notes over 2 MB and Word/Excel files that unpack to
  more than 300 MB are skipped; an export's `conversations.json` over 150 MB
  needs the optional `ijson` package (`pip install ijson`), which reads it
  piece by piece.
* **Never imported:** hidden files and folders (`~/.ssh`, ZenSynora's
  settings, also inside the agent workspace), `.env`, key and credential
  files (`*.pem`, `id_rsa*`, `credentials*`, `*.tfvars`, `kubeconfig`,
  `wp-config.php`, `appsettings*.json`…) — the same rules for agents, the Web
  UI and the command line. Tokens, API keys and passwords in the text (also
  `NAME_PASSWORD: "…"`-style settings and `?token=` in URLs) become
  `[redacted]`; a URL's `user:password@` and secret query parameters are never
  stored or logged. Agents may only read files in your home folder and the
  agent workspace. Web addresses must be public: every address of the name is
  checked, and the connection goes to exactly the checked address. Behind an
  HTTP(S) proxy the proxy resolves the name itself, so what it connects to
  cannot be checked from here.
* PDF, Word and Excel need an optional package:
  `pip install 'zensynora[ingest]'` (or, in ZenSynora's virtual environment,
  `python -m pip install pypdf python-docx openpyxl`). Without it you get a
  message saying exactly that; everything else works.
* Every run is logged as one line in `~/.myclaw/logs/knowledge_ingest.jsonl`.

| Where | How |
|---|---|
| Agent | `assimilate(source, max_notes)` — e.g. "analyze https://github.com/owner/repo and make a diagram" (returns the overview note and the diagram); `import_knowledge(path, kind)` |
| Web UI | Knowledge page → *Import*: a link or folder, or upload a file (up to 200 MB) |
| Command line | `zensynora knowledge assimilate SOURCE [--max-notes 40] [-b BASE]`, `zensynora knowledge import FILE [--kind auto\|chatgpt\|claude\|mcp_memory\|markdown\|document] [-b BASE]` |

## Semantic search (finding notes by meaning)

Added 2026-10-24 (KB phase 3). Word search finds notes that contain the words
you typed. Semantic search also finds notes that *mean* the same: "my car"
finds a note about an *automobile*, "backup of the photos" finds *Picture
archive copies*. Both are combined: the two best word matches always come
first, in their order (a note with the exact error code or name is never
pushed out); then the rest of the word matches and the notes found by
meaning, by a weighted rank fusion in which words count more than meaning. A
note found only by meaning is added only when it is similar enough (the
floor below).

**Turn it on**

1. *Settings → Features → Semantic search* (`knowledge.semantic_search`,
   default off).
2. Knowledge page → *Tags & types* → *Semantic search* card → **Install**
   (installs the optional package `fastembed` into ZenSynora's virtual
   environment; with ONNX Runtime about 150 MB on disk). The packages
   ZenSynora already has keep their versions — the install only adds new
   ones. By hand: `python -m pip install "fastembed>=0.5,<1.0"` in that
   environment, or `pip install 'zensynora[semantic]'`. Updates keep it
   installed (with the version range of the new release).
3. **Backfill** on the same card (or `zensynora knowledge embed`) computes
   the vectors of the existing notes. The first time, the model is
   downloaded once from Hugging Face into `~/.myclaw/models`.

From then on every note that is written, edited, imported or re-indexed is
embedded in the background, a few seconds later; only the parts whose text
changed are computed again. Notes that could not be embedded at once — written
by a command-line command, or while the model could not be loaded — are
shown as *N notes waiting* on the card and are picked up by the Web UI (at
its start, every 10 minutes, when the switch is turned on or the model is
changed, and when the model loads again after an error; failed loads are
retried after 1, 2, 4 … up to 30 minutes). The model runs one job at a time
on one CPU thread; a search goes before the next background batch.

| Where | What changes when it is on |
|---|---|
| Automatic *Relevant Knowledge* | words + meaning (the hub penalty still applies). The search waits at most ~1.5 s for meaning; when the model is not loaded yet (first message after a start) that message uses word search and the model loads in the background. |
| `search_knowledge` | `mode="auto"` (default) = words + meaning; `fts`, `semantic`, `hybrid` on request; the result says when meaning was not used and why |
| Knowledge page | a *Auto / Words / Meaning / Words + meaning* picker next to the search box |

Queries with `AND` / `OR` / `NOT`, `"phrases"` or `prefix*` always use word
search only. With the switch off everything works exactly as before.

**Models** (`knowledge.embedding_model`)

| Model | Size | Licence | Languages | |
|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` (default) | 67 MB | MIT | English | best English quality at this size; reads ~1000 characters per piece |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 220 MB | Apache-2.0 | ~50 languages, Romanian included; a Romanian question finds an English note | reads only ~450 characters per piece (more pieces, slower backfill) |

Changing the model makes every note "not embedded" for the new model (the old
vectors are never mixed in): press *Backfill* again.
`knowledge.semantic_min_score` (default 0 = the model's own: 0.75 for
bge-small, 0.6 for the multilingual one) is the similarity below which a note
found only by meaning is left out (also from the agent's automatic context,
so "nothing found" still leads to the broader word search and the
knowledge-gap hint). bge models give high similarities even to unrelated
text, hence the high floor. These are estimates: **tune it on a real
install** — raise it if unrelated notes show up, lower it if too few do.

**Privacy and cost.** Everything runs on this computer (CPU, one thread);
no note text leaves it. Only the model file is downloaded once. The vectors
are kept in the index database (`knowledge_<base>.db`, about 2.3 KB per piece
of ~1000 characters); like the rest of the index they can be rebuilt at any
time and deleting them never loses a note. A note's vectors are removed with
it; after `sync --force` or when a note comes back from `.trash` its old
vectors are re-used (kept 30 days).

## Distillation: facts, scenarios, a persona

Added 2026-10-24 (KB phase 4). ZenSynora works out three levels from the
notes (level 0), by rules — no model is called:

| Level | What | Example |
|---|---|---|
| **Facts** (L1) | every `- [category] text` fact, and sentences in your own notes that say something about you (*"I prefer quiet fans"*, *"My NAS has four disks"*); the same fact in several notes is **one** fact (`32 GB RAM` = `32GB ram.`; with semantic search on, also *"The car is blue"* = *"The automobile is blue"* — never two facts with different numbers) | `[spec] 32 GB RAM` — confidence 0.86, 2 notes |
| **Scenarios** (L2) | related facts: a note and the notes it links to, a shared tag, a shared name, a category (*Preferences*, *People*, *Decisions*), or meaning | *Home server*: 5 facts from 2 notes |
| **Persona** (L3) | a short summary of you: preferences, setup, projects, people, habits, decisions | *Preferences: Answers in English; … People: Ana is my wife.* |

* **Confidence** (0–1) starts from the category (a decision or a setting
  counts more than a to-do; a sentence less than a written fact), rises with
  every note that says the same, a little with more `(source: …)`; facts older
  than 6 months and facts in an open ⚠ conflict count less. A session summary,
  an import or a tool result supports a fact less than a note of yours.
* **Where it comes from:** every fact, scenario and the persona keep the notes
  and the exact fact lines they come from — *drill down* to them.
* **Never included:** secrets (passwords, tokens, keys — the same rules as the
  importer; the persona leaves out anything that even mentions one),
  snapshots of system state (notes tagged `health`, `snapshot`, `diagnostics`…),
  and where an imported page came from (`source`, `size` …). Sentences are
  read from your own notes only (session summaries: only *"the user …"*
  sentences — in chats and summaries "I" is often the assistant).
* The persona does **not** replace your profile (`update_user_profile`,
  `user_dialectic.md`); it is kept next to it and changes only with the notes
  (each change is a new version; the last 20 are kept).
* Everything is kept in the index database (tables `distilled_facts`,
  `fact_sources`, `scenarios`, `scenario_facts`, `persona`, `distill_notes`,
  `distill_state`). Only notes whose file changed since the last run are read
  again; an edited or deleted note takes its support with it, and a fact with
  no note left disappears. `--full` (or *Rebuild*) reads every note again and
  gives the same result. Nothing here ever changes a note.

**Turn it on:** *Settings → Features → Distil facts, scenarios and a persona*
(`knowledge.distill`, default off). Then the Web UI distils in the background
after `knowledge.distill_every` (20) changed notes, or `knowledge.distill_idle_minutes`
(10) after the last change — one run at a time, never while semantic search is
embedding, at most about two minutes per run (the next run continues). And the
agent's automatic *Relevant Knowledge* starts with a short **What I know about
you** (the persona, about 400 characters, when the message is about you) and
**Relevant facts** (matching scenarios, then facts with their id and
confidence), then the note snippets as before — all in the same ~2000
characters. Off: the agent's context is exactly as before.

| Where | How |
|---|---|
| Web UI | Knowledge page → *Distilled*: status, *Distil now* / *Rebuild*, the persona, scenarios and facts (with confidence); click one to see its notes and lines, click a note to open it |
| Agent | `list_facts(query, limit)`, `list_scenarios(query)`, `get_persona()`, `drill_down(id)` (`f12`, `s3` or `persona`), `distill_knowledge()` (works with the switch off too) |
| Command line | `zensynora knowledge distill [--base NAME] [--full]` |
| API | `GET /api/knowledge/facts`, `/scenarios`, `/persona`, `/drill/{fact\|scenario\|persona}/{id}`, `/distill/status`; `POST /api/knowledge/distill` |

## Plans

What comes next (more of memopad's features: note editing tools, an optional
model pass for distillation) is in
[docs/dev/plans/memopad_port_plan.md](dev/plans/memopad_port_plan.md).
