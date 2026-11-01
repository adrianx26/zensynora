## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)

## Rules learned from incidents

Real problems from running installations, with root cause and fix, are in
[docs/incidents_and_lessons.md](docs/incidents_and_lessons.md). Follow these
rules when changing ZenSynora:

- Inside `myclaw/tools/`, import other `myclaw` modules with `..`; never import lazily registered tools from `myclaw.tools` (import the defining module). Keep `test_every_relative_import_points_at_a_real_module_and_name` green.
- Anything keyed on the version (baselines, caches, markers) must also key on the install root / release folder — updates keep the same version number.
- One process per Telegram bot token; only one ZenSynora component owns the bot.
- After adding or changing a mermaid block in the docs, run `python diagrams/generate.py` and commit the result (Docs CI checks it).
- Any change in how updates work bumps `UPDATER_PROTOCOL` in `updater/__init__.py`.
- Installer and updater must check/install every requirement (Python venv+pip, Node.js 20.19+); never assume a tool is on PATH.
- Errors caused by agent/user input are logged as INFO/WARNING; ERROR/CRITICAL only for faults in ZenSynora. De-duplicate repeating warnings at the source.
- Every tool returns an actionable message for every input (what is wrong, what to do), never a bare exception.
- Limits are settings with defaults (`agents.max_tool_rounds`, `timeouts.task_seconds`, job timeouts); nested limits must agree; at a limit return the partial result.
- Security checks validate structure (each command in a chain, each path) and read allow-lists at call time.
- Model-server problems (rate limit, timeout, bad key, unknown model) are not crashes: no traceback, no medic run.
- Agents must verify with live diagnostics before acting on a report file or knowledge note.
- A write/save function must store every argument it accepts; test the round trip (write → read → compare). Knowledge notes always keep text, tags, facts and links (`write_note`).
- Never auto-save snapshots of system state (health, errors, listings, recovery progress) to the knowledge base — they go stale and get repeated as facts.
- When agents build their own tool for something, add it as a built-in.
- After fixing an error, run the feature end to end — the next bug often hides behind it.
- Knowledge base: the Markdown files are the source of truth; a full rebuild (`sync --force`) must give the same index as step-by-step updates. Links resolve permalink > slug > name > title > alias, never fuzzy.
- Never overwrite or delete a user's note without keeping the previous version in `<base>/.trash/` when anything would be lost.
- Changes to stored user data get an independent review (someone who did not write them, with real experiments) before merge.
- Run heuristics (conflicts, duplicates, persona) on a realistic corpus and count false positives before shipping.
- Text derived from notes/imports/web pages that reaches a prompt is quoted and labelled as information, never instructions.
- Security checks must hold for the next step (connect-time address, symlink target) and apply on every entry path.
- Features that change agent context or run in the background are opt-in; heavy optional libraries are extras; no thread while off. A library a core feature needs is a core dependency (platform markers where wheels are missing).
- Per-run state on a shared object uses a context variable; singletons holding asyncio objects are per event loop.
- Every model call has a fallback and a time limit (nothing waits > 180 s); only permanent failures change settings, logged with the old value.
- Validate settings that only work together (provider + model) when saved and in the Doctor.
- What agents create (tools, skills, notes) the user can see, edit, remove to a trash and export/import.
- Every router in `myclaw/web/` is included in the app (`tests/test_web_routes_registered.py`); test new endpoints through the app.

