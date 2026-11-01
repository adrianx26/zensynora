# MyClaw Agent Skills Reference

> **Version**: 0.3 (medic tools implemented — ZenSynora 0.7.0)
> **Evaluation Method**: Autoresearch-inspired iterative scoring loop
> **Scoring Rubric**: `Score = 0.4×Correctness + 0.3×Reliability + 0.2×Clarity + 0.1×Coverage`
> **Improvement Threshold**: KEEP improvement if delta_score ≥ 0.05 for ≥ 3 skills
> **Overall Baseline**: 0.880 avg → **Improved: 0.989 avg** (+12.4% lift)

---

## Skill Group 0: Lifecycle Hooks (NEW)

### SK-0.1 — `register_hook`
- **Category**: Lifecycle Hooks
- **Tool**: `register_hook(event_type, callback)`
- **Description**: Register a callback function for lifecycle events. Event types: `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`
- **Input Contract**: `event_type` — one of valid event types; `callback` — function to call
- **Output Contract**: `"Hook registered: {event_type}"` or error message

### SK-0.2 — `list_hooks`
- **Category**: Lifecycle Hooks
- **Tool**: `list_hooks()`
- **Description**: List all registered lifecycle hooks by event type
- **Output Contract**: Formatted list of hooks or "No hooks registered" message

### SK-0.3 — `clear_hooks`
- **Category**: Lifecycle Hooks
- **Tool**: `clear_hooks(event_type)`
- **Description**: Clear all hooks or hooks for a specific event type
- **Input Contract**: Optional `event_type` — if None, clears all

---

## Skill Group 1: File I/O

### SK-1.1 — `read_file`
- **Category**: File I/O
- **Tool**: `read_file(path)`
- **Description**: Reads a file from within the workspace directory (`~/.myclaw/workspace`). All paths are validated against directory traversal before execution.
- **Input Contract**: `path` — relative path string within workspace
- **Output Contract**: File contents as string, or `"Error: ..."` on failure
- **Edge Cases Handled**: path traversal (`../`), non-existent files, permission errors. Since 2026-10-23: a folder returns a listing of its entries; a missing file returns what is in that folder; a path outside the workspace explains that ZenSynora's settings and logs are not readable by agents (use `check_system_health`, `get_health_report`, `get_recent_problems`); binary files are refused; files over 200 KB are cut with a note. These cases are logged as INFO/WARNING, not ERROR.
- **Documents (2026-10-31)**: PDF, Word (.docx) and Excel (.xlsx) are returned as extracted text (`[PDF file '…', text extracted]`); a scanned PDF without text says so. Files sent on Telegram are in `inbox/telegram/<date>/`.
- **Known Limitations**: Text files: UTF-8 (invalid bytes replaced), no encoding detection; images and audio cannot be read
- **Baseline Score**: 0.880 → **Improved: 1.000** ✅ KEEP

### SK-1.2 — `write_file`
- **Category**: File I/O
- **Tool**: `write_file(path, content)`
- **Description**: Writes content to a file in the workspace. Creates parent directories automatically.
- **Input Contract**: `path` — relative path; `content` — string to write
- **Output Contract**: `"File written: {path}"` on success, or `"Error: ..."` on failure
- **Edge Cases Handled**: nested paths, path traversal rejection
- **Known Limitations**: Always overwrites; no append mode; no binary write support
- **Baseline Score**: 0.880 → **Improved: 1.000** ✅ KEEP

---

## Skill Group 2: Shell Execution

### SK-2.1 — `shell`
- **Category**: Shell
- **Tool**: `shell(cmd)`
- **Description**: Executes commands from the strict allowlist in the workspace directory, without a shell. Blocks a hardcoded list of dangerous commands. Enforces a 30-second timeout for the whole call. Since 2026-10-23 allowed commands can be chained with `|`, `&&`, `||` and `;` (at most 8 commands; pipes are connected between the processes, no shell interpretation).
- **Input Contract**: `cmd` — one command (`ls -la`) or a chain of allowed commands (`ps aux | grep zensynora`, `pwd && ls -la`)
- **Output Contract**: Combined stdout+stderr string, or `"Error: ..."` on rejection/timeout
- **Allowed Commands**: `ls`, `dir`, `cat`, `type`, `find`, `grep`, `findstr`, `head`, `tail`, `wc`, `sort`, `uniq`, `cut`, `git`, `echo`, `pwd`, `python`, `python3`, `pip`, `curl`, `wget`
- **Blocked Commands**: `rm`, `del`, `powershell`, `cmd`, `net`, `reg`, `schtasks`, `shutdown`, and others
- **Edge Cases Handled**: empty command, blocked command, not-in-allowlist command (checked for every command in a chain; the allowlist is read at each call), subprocess timeout, quoted operators (`grep "a|b"` is an argument, not a pipe)
- **Accepted after a command (2026-10-29)**: `2>/dev/null` (hide that command's errors) and `2>&1` (already the default)
- **Refused**: `$`, backticks, `\`, `{ }`, any other `< >` redirection, `( )`, background `&`, new lines; `find -delete`, `-exec`, `-execdir`, `-ok`, `-okdir`, `-fprint`, `-fprintf`, `-fls`
- **Known Limitations**: No redirection to files (use `write_file`); 10 calls per minute — combine steps in one chain.
- **Baseline Score**: 0.880 → **Improved: 1.000** ✅ KEEP

---

## Skill Group 3: Web & Downloads

### SK-3.1 — `browse`
- **Category**: Web
- **Tool**: `browse(url, max_length=5000)`
- **Description**: Fetches a URL, strips HTML tags/scripts/entities to plain text, and returns content truncated to `max_length` characters.
- **Input Contract**: `url` — full URL string; `max_length` — integer (default 5000)
- **Output Contract**: `"URL: {url}\nStatus: {code}\n\nContent:\n{plain_text}"`, or `"Error browsing {url}: ..."`
- **Edge Cases Handled**: HTTP errors (4xx/5xx), network timeout (30s), large pages (truncated), HTML stripping (script/style removed)
- **Known Limitations**: No JavaScript rendering. No cookie/auth support.
- **Baseline Score**: 0.930 → **Improved: 1.000** ✅ KEEP

### SK-3.2 — `download_file`
- **Category**: Web
- **Tool**: `download_file(url, path)`
- **Description**: Downloads a file from a URL and saves it to the workspace.
- **Input Contract**: `url` — source URL; `path` — relative workspace path
- **Output Contract**: `"[OK] Downloaded file from {url} to {path} ({size} bytes)"`, or `"Error: ..."`
- **Edge Cases Handled**: path traversal, HTTP errors, timeout (60s), streaming chunked download
- **Known Limitations**: No progress reporting; no resume-on-failure
- **Baseline Score**: *TBD*

---

## Skill Group 4: Multi-Agent Delegation

### SK-4.1 — `delegate`
- **Category**: Multi-Agent
- **Tool**: `delegate(agent_name, task)`
- **Description**: Sends a task to another named agent and returns its response. Depth-limited to 2 levels.
- **Input Contract**: `agent_name` — name matching a registered agent; `task` — instruction string
- **Output Contract**: Agent response string, or `"Error: ..."` (unknown agent, depth exceeded, registry not initialized)
- **Edge Cases Handled**: depth limit (max 2), unregistered agent name, empty registry
- **Known Limitations**: Synchronous in terms of result (must await full response); no streaming; requires Telegram gateway to be running for full context injection
- **Baseline Score**: *TBD*

### SK-4.2 — `list_tools`
- **Category**: Multi-Agent / Discovery
- **Tool**: `list_tools(details=False)`
- **Description**: Returns all currently registered tools (core + TOOLBOX). With `details=True`, one line per tool with its arguments (`?` = optional) and what it does — use it to review or compare tools in one call (2026-10-21).
- **Input Contract**: `details` (bool, optional)
- **Output Contract**: `"Available tools (N): {names}"` or `"N tools:\n- name(arg, opt?) — description"` per line
- **Baseline Score**: *TBD*

---

## Skill Group 5: Task Scheduling

### SK-5.1 — `schedule`
- **Category**: Scheduling
- **Tool**: `schedule(task, delay, every, user_id)`
- **Description**: Schedules a one-shot (delay) or recurring (every) task. In the Web UI (2026-10-21) it becomes a job on the Jobs page run by the asking agent (repeats at least every 5 minutes); only `zensynora gateway` without the Web UI uses Telegram's in-memory queue.
- **Input Contract**: `task` — task string; `delay` or `every` — seconds (at least one must be > 0)
- **Output Contract**: `"Created job job-… \"…\" for @agent, once at …"` (Web UI) or `"One-shot job '{id}' scheduled — fires in {delay}s."` (gateway)
- **Baseline Score**: *TBD*

### SK-5.2 — `edit_schedule`
- **Category**: Scheduling
- **Tool**: `edit_schedule(job_id, new_task, delay, every)`
- **Description**: Edits an active job's task, delay, or interval.
- **Baseline Score**: *TBD*

### SK-5.3 — `split_schedule`
- **Category**: Scheduling
- **Tool**: `split_schedule(job_id, sub_tasks_json)`
- **Description**: Splits one job into multiple sub-jobs. `sub_tasks_json` must be a valid JSON array of strings.
- **Baseline Score**: *TBD*

### SK-5.4 — `suspend_schedule` / `resume_schedule` / `cancel_schedule`
- **Category**: Scheduling
- **Tools**: `suspend_schedule(job_id)`, `resume_schedule(job_id)`, `cancel_schedule(job_id)`
- **Description**: Lifecycle management for scheduled jobs.
- **Baseline Score**: *TBD*

### SK-5.5 — `list_schedules`
- **Category**: Scheduling
- **Tool**: `list_schedules()`
- **Description**: Lists all active scheduled jobs with status, task name, and next execution time.
- **Baseline Score**: *TBD*

### SK-5.6 — `nlp_schedule` (NEW)
- **Category**: Scheduling / NLP
- **Tool**: `nlp_schedule(task, natural_time, user_id)`
- **Description**: Schedule a task using natural language time expressions like "in 5 minutes", "at 8 AM daily", "every Monday at 9pm". In the Web UI it becomes a job on the Jobs page (like `schedule`).
- **Input Contract**: `task` — task description; `natural_time` — natural language time expression
- **Output Contract**: Success message with parsed schedule info

### SK-5.7 — `job_create` / `job_list` / `job_run` / `job_status` / `job_update` (2026-10-21)
- **Category**: Scheduling / Jobs page
- **Tools**:
  - `job_create(name, prompt, agent?, schedule?, when?, every_minutes?, at_time?, days?, run_now?, report_here?, success_criteria?, max_steps?, timeout_s?)` — a job on the Jobs page (`manual` · `once` · `interval` ≥ 5 min · `daily` · `weekly`), run by any agent; results are posted in the chat channel it was created from.
  - `job_list()` — all jobs with schedule, last result, success rate, next run, who made them.
  - `job_run(job_id)` — start now in the background (a job cannot start itself).
  - `job_status(job_id, runs?)` — recent runs: status, duration, model, score, answer or error.
  - `job_update(job_id, state?, …)` — `state="pause" | "resume"` for any job; other fields only for jobs made by agents.
- **Runs**: the Web UI job runner executes them with the job's agent, timeout, retries, tool steps and medic-on-failure; every run is recorded in `~/.myclaw/jobs.db` (`trigger` = `schedule`, `manual`, `agent` or `medic`).
- **Switch**: `agents.allow_job_creation` (Settings → Features). Agents may make at most 50 jobs.

---

## Skill Group 6: Knowledge Base

### SK-6.1 — `write_to_knowledge`
- **Category**: Knowledge
- **Tool**: `write_to_knowledge(title, content, tags, observations, relations, user_id)`
- **Description**: Creates a new Markdown note in the per-user knowledge base with FTS5 indexing.
- **Input Contract**: `title` — note title (becomes permalink); `content` — body text; `tags` — CSV; `observations` — `"category | content"` per line; `relations` — `"relation_type | target"` per line
- **Output Contract**: `"✅ Knowledge note created: [{title}](memory://{permalink})"`
- **Baseline Score**: *TBD*

### SK-6.2 — `search_knowledge`
- **Category**: Knowledge
- **Tool**: `search_knowledge(query, limit, user_id)`
- **Description**: Searches the knowledge base using SQLite FTS5 full-text search.
- **Input Contract**: `query` — FTS5 query string (supports AND, OR, NOT, *)
- **Output Contract**: Formatted list of matching notes with observations and tags
- **Baseline Score**: *TBD*

### SK-6.3 — `read_knowledge`
- **Category**: Knowledge
- **Tool**: `read_knowledge(permalink, user_id)`
- **Description**: Reads a specific knowledge note by its permalink.
- **Baseline Score**: *TBD*

### SK-6.4 — `get_knowledge_context`
- **Category**: Knowledge
- **Tool**: `get_knowledge_context(permalink, depth, user_id)`
- **Description**: Traverses the knowledge graph up to `depth` hops and builds a rich context string.
- **Baseline Score**: *TBD*

### SK-6.5 — `list_knowledge` / `sync_knowledge_base` / `list_knowledge_tags`
- **Category**: Knowledge
- **Tools**: listing, sync, tags enumeration
- **Baseline Score**: *TBD*

---

## Skill Group 7: TOOLBOX (Dynamic Tool Building)

### SK-7.1 — `register_tool`
- **Category**: TOOLBOX
- **Tool**: `register_tool(name, code, documentation)`
- **Description**: Dynamically creates and persists a new Python tool at runtime. Validates identifier, syntax, docstring, try-except, and logger.error() presence.
- **Input Contract**: `name` — valid Python identifier; `code` — full function source; `documentation` — description string
- **Output Contract**: `"Tool '{name}' registered in TOOLBOX and available immediately."`, or `"Error: ..."` with specific reason
- **Validations**: syntax compile, docstring required, try-except required, logger.error required, duplicate check, similar-name check
- **Baseline Score**: *TBD*

### SK-7.2 — `list_toolbox`
- **Category**: TOOLBOX
- **Tool**: `list_toolbox()`
- **Description**: Lists all tools in TOOLBOX with creation time and documentation preview.
- **Baseline Score**: *TBD*

### SK-7.3 — `get_tool_documentation`
- **Category**: TOOLBOX
- **Tool**: `get_tool_documentation(name)`
- **Description**: Returns the full `{name}_README.md` content for a TOOLBOX tool; for a built-in tool its description, arguments and docstring (2026-10-21).
- **Baseline Score**: *TBD*

---

## Skill Group 8: Session Reflection & Learning (NEW)

### SK-8.1 — `schedule_daily_reflection`
- **Category**: Learning
- **Tool**: `schedule_daily_reflection(user_id, hour, minute)`
- **Description**: Schedule a daily session reflection that analyzes what was learned and saves to knowledge base
- **Input Contract**: `user_id` — user ID; `hour` — 0-23; `minute` — 0-59 (default: 20:00)
- **Output Contract**: Success message with scheduled time

### SK-8.2 — `generate_session_insights`
- **Category**: Learning
- **Tool**: `generate_session_insights(user_id, save_to_knowledge)`
- **Description**: Analyze recent conversation history for insights, patterns, and preferences
- **Output Contract**: Formatted insights summary

### SK-8.3 — `extract_user_preferences`
- **Category**: Learning
- **Tool**: `extract_user_preferences(user_id)`
- **Description**: Build user profile from conversation history (topics, communication style, preferences)
- **Output Contract**: JSON with user profile data + saved to knowledge base

### SK-8.4 — `update_user_profile`
- **Category**: Learning
- **Tool**: `update_user_profile(insights, user_id)`
- **Description**: Update the user dialectic profile with new insights
- **Input Contract**: `insights` — markdown content to add

### SK-8.5 — `get_user_profile`
- **Category**: Learning
- **Tool**: `get_user_profile(user_id)`
- **Description**: Get the current user dialectic profile content
- **Output Contract**: Profile content or placeholder message

---

## Skill Group 9: Skill Management (NEW)

### SK-9.1 — `get_skill_info`
- **Category**: Skill Management
- **Tool**: `get_skill_info(skill_name)`
- **Description**: Get detailed information about a skill (version, tags, eval score, etc.)
- **Output Contract**: Formatted skill information

### SK-9.2 — `enable_skill`
- **Category**: Skill Management
- **Tool**: `enable_skill(skill_name)`
- **Description**: Enable a disabled skill in TOOLBOX
- **Output Contract**: Success or error message

### SK-9.3 — `disable_skill`
- **Category**: Skill Management
- **Tool**: `disable_skill(skill_name)`
- **Description**: Disable an enabled skill (soft delete, keeps file)
- **Output Contract**: Success or error message

### SK-9.4 — `update_skill_metadata`
- **Category**: Skill Management
- **Tool**: `update_skill_metadata(skill_name, tags, description, version)`
- **Description**: Update skill metadata (tags, description, version)
- **Output Contract**: Success message

### SK-9.5 — `benchmark_skill`
- **Category**: Skill Management
- **Tool**: `benchmark_skill(skill_name, test_cases_json)`
- **Description**: Run benchmark tests on a skill with JSON test cases
- **Input Contract**: `test_cases_json` — JSON array of `{"input": {...}, "expected": "..."}`
- **Output Contract**: Benchmark results with pass/fail rates and scores

### SK-9.6 — `evaluate_skill`
- **Category**: Skill Management
- **Tool**: `evaluate_skill(skill_name)`
- **Description**: Run basic evaluation checks (syntax, docstring, error handling, logging)
- **Output Contract**: Formatted evaluation results

### SK-9.7 — `improve_skill`
- **Category**: Skill Management
- **Tool**: `improve_skill(skill_name, improved_code, documentation)`
- **Description**: Improve an existing skill with new code (with safety checks and rollback)
- **Output Contract**: Success message with version info

### SK-9.8 — `rollback_skill`
- **Category**: Skill Management
- **Tool**: `rollback_skill(skill_name)`
- **Description**: Rollback a skill to its previous version
- **Output Contract**: Success message

---

## Skill Group 10: ZenHub Registry (NEW)

### SK-10.1 — `hub_search`
- **Category**: ZenHub
- **Tool**: `hub_search(query, limit)`
- **Description**: Search ZenHub for skills by name/description/tags
- **Output Contract**: Formatted list of matching skills

### SK-10.2 — `hub_list`
- **Category**: ZenHub
- **Tool**: `hub_list()`
- **Description**: List all skills available in ZenHub
- **Output Contract**: Formatted list of all published skills

### SK-10.3 — `hub_publish`
- **Category**: ZenHub
- **Tool**: `hub_publish(skill_name, description, tags, from_toolbox)`
- **Description**: Publish a skill from TOOLBOX to ZenHub
- **Output Contract**: Success or error message

### SK-10.4 — `hub_install`
- **Category**: ZenHub
- **Tool**: `hub_install(skill_name, user_id)`
- **Description**: Install a skill from ZenHub into TOOLBOX
- **Output Contract**: Success or error message

### SK-10.5 — `hub_remove`
- **Category**: ZenHub
- **Tool**: `hub_remove(skill_name)`
- **Description**: Remove a skill from ZenHub (unpublish)
- **Output Contract**: Success or error message

### SK-10.6 — `discover_external_skills`
- **Category**: ZenHub
- **Tool**: `discover_external_skills()`
- **Description**: Discover skills in external directory (~/.myclaw/skills/)
- **Output Contract**: Formatted list of discovered skills

### SK-10.7 — `hub_install_from_external`
- **Category**: ZenHub
- **Tool**: `hub_install_from_external(skill_name, user_id)`
- **Description**: Install a skill from external directory into TOOLBOX
- **Output Contract**: Success or error message

---

## Skill Group 11: Web Scraping

### SK-11.1 — Scrapling Agent Guide
- **Category**: Web Scraping
- **Tool**: Scrapling (via custom python code / TOOLBOX or direct `shell` CLI)
- **Description**: For advanced web scraping, bypass anti-bot protections (like Cloudflare Turnstile), stealth headless browsing, and full spider framework.
- **Reference**: Documentation is available in [`docs/scrapling_agent_guide.md`](../docs/scrapling_agent_guide.md). Read it before attempting to use Scrapling in Python code or the CLI.
- **Baseline Score**: *TBD*

---

## Skill Group 12: Medic — Health, Integrity & Recovery (NEW in 0.7.0)

All tools below are implemented in `myclaw/agents/medic_agent.py` (before 0.7.0
twelve of them were advertised but missing). Full guide:
[`docs/medic_agent_guide.md`](../docs/medic_agent_guide.md).

### SK-12.1 — `check_system_health` / `get_health_report`
- **Description**: Run the doctor checks (packages, provider SDKs, config, LLM server, databases, disk, permissions, recent errors).
- **Input Contract**: `offline: bool = False` (skip network checks); `get_health_report` also takes `as_json: bool`.
- **Output Contract**: `"OK|WARN|FAIL: n checks ok, …"` followed by each problem and its fix hint / full report.

### SK-12.2 — `scan_files` / `verify_file_integrity` / `enable_hash_check`
- **Description**: Record and verify SHA-256 hashes of the installed code.
- **Input Contract**: `file_paths` (comma-separated, relative to the install) / `file_path` / `enabled: bool`.
- **Output Contract**: `"Recorded N file hash(es)…"`, `"OK: N file(s) match…"` or `"CHANGED: …"` with the list.

### SK-12.3 — `create_backup` / `list_backups` / `recover_file`
- **Description**: Back up and restore files inside the installation only. Recovery prefers the backup matching the integrity baseline, then GitHub at `v<version>` (`allow_main=True` for `main`).
- **Output Contract**: path of the backup / list / `"Restored … from …"`, or `"ERROR: … outside the ZenSynora installation"`.

### SK-12.4 — `detect_errors_in_file` / `validate_modification`
- **Description**: Syntax-check `.py/.json/.yaml/.toml`; validate a proposed change (syntax, size, risky calls such as `eval`, `os.system`, `shell=True`).
- **Output Contract**: `"OK: …"` / `"SYNTAX ERROR … line N"`; `"VALID"`, `"VALID WITH WARNINGS: …"` or `"INVALID: …"`.

### SK-12.5 — `check_file_virustotal`
- **Description**: Look up a file's SHA-256 on VirusTotal (no upload). Needs `medic.virustotal_api_key` or `api_key`.
- **Output Contract**: `"CLEAN|SUSPICIOUS|DANGER: …"` or `"UNKNOWN: …"`.

### SK-12.6 — `record_task_execution` / `get_task_analytics`
- **Description**: Log task outcomes to `~/.myclaw/medic/tasks.jsonl` and summarise success rate, duration and failing tasks.

### SK-12.7 — `prevent_infinite_loop`
- **Description**: Runaway-loop guard; the router calls it on every step. Blocks when more than `medic.max_loop_iterations` (default 100) steps happen within 60 s.
- **Output Contract**: `"OK: n/limit …"` or `"Execution limit reached: …"`.

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 0.0 | 2026-03-15 | Initial baseline skills definition. Overall eval: 0.880 avg across 26 tasks |
| 0.1 | 2026-03-15 | Improved: rich docstrings for shell/file/toolbox (Clarity 0.40→1.0), HTML stripping in browse (Coverage 0.70→1.0), ALLOWED_COMMANDS expanded, bigram knowledge search in agent.py. Overall: 0.989 avg |
| 0.2 | 2026-03-29 | Phase 1-4 Implementation Complete: Added Lifecycle Hooks (SK-0.1 to 0.3), Natural Language Scheduling (SK-5.6), Session Learning (SK-8.1 to 8.5), Skill Management (SK-9.1 to 9.8), ZenHub Registry (SK-10.1 to 10.7). Total: ~35 new tools added. |
| 0.3 | 2026-10-13 | Skill Group 12 (Medic): all 15 medic tools implemented and documented (ZenSynora 0.7.0). |
