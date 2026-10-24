# Knowledge base — Phases 2–6 (memopad port): working rules

The plan is [memopad_port_plan.md](memopad_port_plan.md). Phase 0
([kb_phase0_contracts.md](kb_phase0_contracts.md)) is done; Phase 1 is not part of this round.
Phases are built one after another on branch `kb-phases-2-6` (based on
`kb-phase0-fixes`); Phase 5 (ingestion) is built in parallel with Phase 2
because it lives in its own files. After each phase an independent reviewer
checks it, then the coordinator commits.

## Rules for every agent
- Repo `/home/claude/zensynora` (package `myclaw`). Python: `python3.13`.
  Tests: `python3.13 -m pytest -q -p no:cacheprovider <files>`; the full suite
  (844 tests at the start) must stay green. Lint: `/root/.local/bin/ruff check .`.
  Types: `/tmp/claude-0/mypyenv/bin/mypy myclaw/ updater/` (that is how CI runs it).
  Web UI: `cd webui && npm run build` (and `npx tsc --noEmit` / `npm run lint`
  if available; two eslint errors in `TypewriterText.tsx` are old).
- **No git commands.** The coordinator commits after each phase (one commit per
  step you report). Edit only the files your task names; another agent may be
  working on other files at the same time.
- **Nothing that works may break.** Keep public function names, signatures (add
  optional keyword parameters only), return shapes, API routes and the note file
  format. Existing notes must keep working without a rebuild; schema changes are
  additive (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN` in try/except)
  and safe on a database created by older versions.
- **The Markdown files are the source of truth.** Anything derived (vectors,
  distilled facts, conflicts, persona) lives in the index database or its own
  table and can be rebuilt; deleting it must never lose user data.
- **Never lose user data:** no permanent deletion; replaced/merged notes go to
  `<base>/.trash/`; every merge/optimisation has a preview (dry run) first.
- **Heavy or optional dependencies** (fastembed/onnxruntime, PDF/DOCX/XLSX
  readers, watchfiles) are optional: import lazily, the feature reports "not
  installed — how to install" and everything else keeps working. Do not add
  them to the hard requirements; add them as optional extras in
  `pyproject.toml` (and say so in your report). Tests must pass without them
  (use fakes / `pytest.importorskip`). The workspace cannot reach huggingface.co.
- **New features that change agent behaviour or run in the background are
  opt-in** (config switch, default off) unless the plan says otherwise; switches
  appear in Settings → Features like the existing knowledge switches.
- Background work: never block the event loop (`asyncio.to_thread`), bounded
  CPU (1 thread for onnx), bounded memory, cancellable; respect
  `knowledge.enabled`.
- Python 3.11-compatible code. Comment non-obvious changes with
  `# <date> (KB phase N): <why>`.
- Every tool returns an actionable message for every input; errors caused by
  user/agent input are INFO/WARNING, never tracebacks.
- Tests in your own new test file `tests/test_kb_phaseN_<topic>.py`, real temp
  dirs (`monkeypatch.setenv("HOME", tmp)` as in `tests/test_knowledge_structure.py`).
- Docs: add/extend the section for your phase in `docs/knowledge_base_guide.md`
  (user-facing, short). The coordinator writes CHANGELOG / incidents.
- Final report: what changed per step (files), new config switches / API routes /
  tools / tables, optional dependencies, anything not done and why, test
  results, and a bullet list of user-visible changes.

## Lessons from memopad's history to respect
- Replace observations/relations in one transaction; `busy_timeout`; retry only
  on "database is locked" (already done in Phase 0).
- Embeddings: composite primary key (entity, chunk), updated on every write,
  removed on delete; onnx `threads=1`; inference off the event loop; a release
  must not drop the vector store.
- Conflict detection must not flag ordinary updates (memopad's was "too
  aggressive"): same note + same category + clearly contradicting values only.
- File watcher must ignore its own writes (logs, status files, `.trash`, the DB).
- Never fuzzy-match links on write.
