# Knowledge base — Phase 0 (memopad port): shared rules and contracts

Phase 0 of the memopad → ZenSynora knowledge-base port fixes the existing
knowledge base before any new feature is added. Four people/agents work on it
in parallel, each on its own files. This page is the contract between them.

## Rules for everyone
- Repo: `/home/claude/zensynora` (package `myclaw`), branch `kb-phase0-fixes`.
  Python: `python3.13` (tests: `python3.13 -m pytest -q -p no:cacheprovider <files>`).
  Baseline: the full suite passes (730 passed).
- Edit ONLY the files assigned to you. Other agents edit other files at the
  same time, so a failure in a file you do not own may be transient: report it,
  do not fix it. No git commands (no commit/branch/stash/checkout).
- Nothing that works today may break: keep every public function name and
  signature (only add optional keyword parameters), keep return types, keep
  the markdown note format readable by the old code.
- Markdown files are the source of truth; the SQLite index can always be
  rebuilt from them (`sync_knowledge(base, force=True)`).
- Never delete user data permanently: deleted notes go to `<base>/.trash/`.
- Python 3.11-compatible code (`from __future__ import annotations` is fine).
  `ruff check .` and `mypy myclaw/ updater/` must stay clean for your files.
- Mark each change with a short comment: `# 2026-10-23 (KB phase 0): <why>`.
- Version stays 0.7.5. Do not edit CHANGELOG/docs — the coordinator does;
  instead, end your report with a list of user-visible behaviour changes.
- Put your tests in your own new test file (named below).

## Contracts (already stubbed in the code)
- `parser.Note.aliases: List[str]`, `parser.Note.metadata: Dict[str, Any]`
  (default empty). Filled by `parse_note` from frontmatter (agent B), written
  back by `generate_markdown` (agent B), stored by
  `KnowledgeDB.sync_entity_from_note` (agent A).
- `db.sanitize_fts_query(query, any_term=False, boolean=False)` and
  `KnowledgeDB.search_fts(query, limit=10, boolean=False)` — `boolean=True`
  keeps AND / OR / NOT and "quoted phrases" as search syntax (agent A).
  Free text (`boolean=False`, the default) behaves as before.
- `storage.search_notes(query, user_id="default", limit=10, db_path=None, boolean=False)`.
- `storage.knowledge_root()` / `storage.set_knowledge_root(path | None)` —
  the folder holding the knowledge bases (default `~/.myclaw/knowledge`).
- `KnowledgeDB.resolve_target(name) -> Optional[Entity]` — the note a link
  target points to: permalink, slug, name, title or alias, case-insensitive (agent A).
- `KnowledgeDB.get_unresolved_relations(entity_id) -> List[Tuple[str, str]]`
  — `(relation_type, target_name)` of links whose target note does not exist yet (agent A).
- `KnowledgeDB.search_by_tag(tag)` finds frontmatter tags too (agent A).
- `storage.delete_note` moves the file to `<base>/.trash/` (agent B); the
  sync never indexes folders whose name starts with `.` (agent A).
