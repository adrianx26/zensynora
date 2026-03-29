# MyClaw Agent Skills Reference

> **Version**: 0.2 (Post-Phase1-4 Implementation)
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
- **Edge Cases Handled**: path traversal (`../`), non-existent files, permission errors
- **Known Limitations**: Returns raw bytes-as-string for binary files; no encoding detection
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
- **Description**: Executes a shell command from the strict allowlist in the workspace directory. Blocks a hardcoded list of dangerous commands. Enforces 30-second timeout.
- **Input Contract**: `cmd` — shell command string
- **Output Contract**: Combined stdout+stderr string, or `"Error: ..."` on rejection/timeout
- **Allowed Commands**: `ls`, `dir`, `cat`, `type`, `find`, `grep`, `findstr`, `head`, `tail`, `wc`, `sort`, `uniq`, `cut`, `git`, `echo`, `pwd`, `python`, `python3`, `pip`, `curl`, `wget`
- **Blocked Commands**: `rm`, `del`, `powershell`, `cmd`, `net`, `reg`, `schtasks`, `shutdown`, and others
- **Edge Cases Handled**: empty command, blocked command, not-in-allowlist command, subprocess timeout
- **Known Limitations**: No stdin piping support.
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
- **Tool**: `list_tools()`
- **Description**: Returns a sorted list of all currently registered tool names (core + TOOLBOX tools).
- **Input Contract**: None
- **Output Contract**: `"Available tools: {comma-separated names}"`
- **Baseline Score**: *TBD*

---

## Skill Group 5: Task Scheduling

### SK-5.1 — `schedule`
- **Category**: Scheduling
- **Tool**: `schedule(task, delay, every, user_id)`
- **Description**: Schedules a one-shot (delay) or recurring (every) job to be executed by the default agent.
- **Input Contract**: `task` — task string; `delay` or `every` — seconds (at least one must be > 0)
- **Output Contract**: `"One-shot job '{id}' scheduled — fires in {delay}s."` or recurring equivalent
- **Dependency**: Requires Telegram gateway JobQueue (`_job_queue must not be None`)
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
- **Description**: Schedule a task using natural language time expressions like "in 5 minutes", "at 8 AM daily", "every Monday at 9pm"
- **Input Contract**: `task` — task description; `natural_time` — natural language time expression
- **Output Contract**: Success message with parsed schedule info

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
- **Description**: Returns the full `{name}_README.md` content for a given TOOLBOX tool.
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
- **Reference**: Documentation is available in [`docs/scrapling_agent_guide.md`](docs/scrapling_agent_guide.md). Read it before attempting to use Scrapling in Python code or the CLI.
- **Baseline Score**: *TBD*

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 0.0 | 2026-03-15 | Initial baseline skills definition. Overall eval: 0.880 avg across 26 tasks |
| 0.1 | 2026-03-15 | Improved: rich docstrings for shell/file/toolbox (Clarity 0.40→1.0), HTML stripping in browse (Coverage 0.70→1.0), ALLOWED_COMMANDS expanded, bigram knowledge search in agent.py. Overall: 0.989 avg |
| 0.2 | 2026-03-29 | Phase 1-4 Implementation Complete: Added Lifecycle Hooks (SK-0.1 to 0.3), Natural Language Scheduling (SK-5.6), Session Learning (SK-8.1 to 8.5), Skill Management (SK-9.1 to 9.8), ZenHub Registry (SK-10.1 to 10.7). Total: ~35 new tools added. |
