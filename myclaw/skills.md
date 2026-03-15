# MyClaw Agent Skills Reference

> **Version**: 0.1 (Post-Improvement)
> **Evaluation Method**: Autoresearch-inspired iterative scoring loop
> **Scoring Rubric**: `Score = 0.4×Correctness + 0.3×Reliability + 0.2×Clarity + 0.1×Coverage`
> **Improvement Threshold**: KEEP improvement if delta_score ≥ 0.05 for ≥ 3 skills
> **Overall Baseline**: 0.880 avg → **Improved: 0.989 avg** (+12.4% lift)

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

## Skill Group 8: Web Scraping

### SK-8.1 — Scrapling Agent Guide
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
