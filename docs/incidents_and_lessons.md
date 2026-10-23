# Incidents and lessons learned

Real problems found on a running installation (Linux, `~/.local/share/zensynora`,
Web UI + Telegram, OpenRouter free models), October 2026. For each one: what
was seen, why it happened, how it was fixed, how it is caught now, and the
rule that keeps it from coming back. The rules are collected at the end and,
in short form, in `AGENTS.md`.

PR numbers refer to <https://github.com/adrianx26/zensynora/pulls>.
"Unreleased" fixes are listed in `CHANGELOG.md` under *[Unreleased]*.

---

## 1. Telegram

### 1.1 The bot never answered (2026-10-18, PR #26)

| | |
|---|---|
| **Symptom** | Messages to the bot got no answer; no error in the log. |
| **Root cause** | The Telegram worker was created but never started when the bot ran inside the Web UI. |
| **Fix** | `TelegramChannel.start()/stop()/status()`; the Web UI starts the bot when `channels.telegram.run_in_webui` is on. |
| **Detected by** | `tests/test_telegram_channel.py`, `tests/test_telegram_webui.py`; Doctor check *Telegram bot*; Settings → Channels shows the bot status. |
| **Rule** | A background worker that is created must have a test that it actually runs, and its state must be visible in the UI. |

### 1.2 "Token is wrong" — whitespace in the token (2026-10-18, PR #27)

| | |
|---|---|
| **Symptom** | A correct bot token was refused. |
| **Root cause** | The token was pasted with a trailing space/new line. |
| **Fix** | Tokens are trimmed on save and on use; **Send test message** button checks the whole path. |
| **Detected by** | `tests/test_telegram_webui.py`. |
| **Rule** | Trim every secret the user pastes; offer a one-click end-to-end test. |

### 1.3 Gateway crash loop under systemd (2026-10-18, PR #28/#29)

| | |
|---|---|
| **Symptom** | `zensynora gateway` (service `myclaw`) restarted every ~7 s: *"There is no current event loop"*; 217 starts in 24 h. Each start also stole the Telegram connection from the Web UI. |
| **Root cause** | The gateway used `get_event_loop()` in a thread without a loop; it also started Telegram even when the bot was set to run in the Web UI. |
| **Fix** | `asyncio.run(_serve())`; the gateway skips Telegram when `run_in_webui` is on (`--telegram` forces it). |
| **Detected by** | Doctor check *Restarts (crash loop)* (`restart_loop`), Doctor *Telegram bot* warns when `myclaw.service` runs next to the Web UI bot. |
| **Rule** | Never call `asyncio.get_event_loop()` outside a running loop; use `asyncio.run`. Only one component may own a channel. |

### 1.4 False "conflict" right after a restart (2026-10-18, PR #28/#29)

| | |
|---|---|
| **Symptom** | After *Restart*, the status said another program was running the bot. |
| **Root cause** | Telegram still held the old long-poll for a short time (409 Conflict). |
| **Fix** | The bot is stopped before the process restarts; a 409 within `CONFLICT_GRACE` (90 s) is retried, not reported. |
| **Detected by** | `tests/test_telegram_channel.py`. |
| **Rule** | Treat a 409 shortly after a restart as transient; stop pollers before `execve`. |

### 1.5 Persistent conflict: Hermes used the same bot token (2026-10-18)

| | |
|---|---|
| **Symptom** | *"Telegram: Another program is already running this bot … Stopping this copy"*, again and again. |
| **Root cause** | A second app on the same computer (Hermes Agent, `hermes-gateway.service`, `Restart=always`) polled Telegram with the **same** bot token. Telegram allows one poller per token. |
| **Fix** | Hermes was stopped and removed by the user. |
| **Detected by** | The conflict text in the log / Doctor *Errors in the last 24 h*. |
| **Rule** | One process per Telegram token. When a conflict persists beyond the grace period, look for other programs (systemd units, other bots) using the token; give each app its own bot. |

### 1.6 Agents built their own Telegram tool with placeholder tokens (2026-10-18, PR #28/#29)

| | |
|---|---|
| **Symptom** | An agent asked the user for the bot token and chat id; a TOOLBOX tool `telegram_integration` contained `YOUR_BOT_TOKEN_HERE`. |
| **Root cause** | Agents had no built-in way to message the user. |
| **Fix** | Built-in `send_telegram_message`; agents are told never to ask for the token; the home-made tool was disabled. |
| **Detected by** | Doctor *Custom tools (TOOLBOX)* (`custom_tools`) flags placeholder credentials and hard-coded secrets. |
| **Rule** | If agents keep building a tool, the product is missing it — add it as a built-in and say so in their instructions. |

---

## 2. Knowledge base

### 2.1 Background extraction NameError (2026-10-18, PR #26)

| | |
|---|---|
| **Symptom** | *"Could not start background knowledge extraction: name '_background_extraction_task' is not defined"* (201× in one log). |
| **Root cause** | A module global was used before it was ever defined. |
| **Fix** | Defined in `myclaw/knowledge/sync.py`. |
| **Detected by** | `ruff` (undefined names) in CI. |
| **Rule** | CI must run ruff with real-error rules (F821 etc.); globals are declared at module level. |

### 2.2 Stale or invented "crash loop" notes written by agents (2026-10-23, branch fix/report-20261023)

| | |
|---|---|
| **Symptom** | Agents reported a "crash loop every 8 seconds, 217 restarts" and "25 files to recover" when the Doctor showed 0 restarts. Notes such as *System Crash Loop Analysis* appeared in the knowledge base. |
| **Root cause** | Auto-extraction saves facts from tool output (`shell`, `read_file`) and conversations. Agents read an **old** report file in the workspace (`system_problems.md`, from the 18 Oct crash loop) and saved it as current knowledge; later turns repeated it. |
| **Fix** | `shell` and `write_file` output is no longer auto-saved (listings, process lists and the agents' own reports were becoming "facts"); error outputs are skipped; both extraction prompts now exclude the current state of the computer (health, errors, crash counts, recovery progress, listings). Notes saved before stay — clean them on the **Knowledge** page (search "crash loop", "recover", "integrity", *Move to trash*). The integrity fix (6.1) removes the false "files differ" input. |
| **Detected by** | `test_transient_tool_output_is_not_saved_as_knowledge` in `tests/test_report_fixes_20261023.py`; compare agent claims with the Doctor report (Restarts, Code integrity). |
| **Rule** | Agents must check live diagnostics (`check_system_health`, `get_recent_problems`) before acting on a report file; knowledge from reports needs a date. |

---

## 3. Code structure

### 3.1 Broken relative imports after `myclaw/tools` became a package (2026-10-18, PR #31)

| | |
|---|---|
| **Symptom** | *"cannot import name 'TaskStatus' / 'SwarmOrchestrator' from 'myclaw.tools.swarm'"*, *"No module named 'myclaw.tools.memory'"*, *"Medic diagnosis itself failed: cannot import name 'check_system_health' from 'myclaw.tools'"*. |
| **Root cause** | After the split into `myclaw/tools/*.py`, `from .swarm`, `.memory`, `.config`, `.provider`, `.backends` pointed inside `myclaw/tools/` (or at the module itself); `admin_dashboard.py` used `..` above the package; `cli memory` used a removed `get_db`; the medic diagnosis imported names that `myclaw.tools` only registers lazily. 26 broken imports in total. |
| **Fix** | `..` imports, medic tools imported from `myclaw.agents.medic_agent`, memory CLI rewritten. |
| **Detected by** | `tests/test_log_fixes_20261018.py::test_every_relative_import_points_at_a_real_module_and_name` (static check of every relative import). |
| **Rule** | Inside `myclaw/tools/`, imports of other `myclaw` modules use `..`. Never `from .tools import <lazy tool>` — import from the module that defines it. |

### 3.2 `SQLitePool._get_lock` missing (2026-10-18, PR #31)

| | |
|---|---|
| **Symptom** | Swarm storage failed after the import fix: *"type object 'SQLitePool' has no attribute '_get_lock'"*. |
| **Root cause** | The sync pool used a helper that only the async pool has. |
| **Fix** | Uses its own `_pool_lock`. |
| **Detected by** | `tests/test_log_fixes_20261018.py::test_sqlite_pool_get_and_release`, swarm tool tests. |
| **Rule** | When an import error is fixed, run the feature end to end — the next bug hides behind it. |

---

## 4. Models and agent turns

### 4.1 Rate limits and timeouts treated as crashes (2026-10-18, PR #31)

| | |
|---|---|
| **Symptom** | *"Unexpected error in think loop"* with traceback and a medic diagnosis for *"temporarily rate-limited upstream"* or *"Request timed out"*. |
| **Root cause** | Every exception took the "unexpected" path. |
| **Fix** | `llm_errors.is_provider_error()`: one warning with a readable reason, no medic run; a rate-limited model is retried once after 5 s when no rotation is set. |
| **Detected by** | `tests/test_log_fixes_20261018.py`. |
| **Rule** | Classify errors: outside problems (rate limit, timeout, bad key, unknown model) are warnings with advice; only real crashes get tracebacks and diagnostics. |

### 4.2 Log noise hid real problems (2026-10-18, PR #31)

| | |
|---|---|
| **Symptom** | 1,861 warnings in a day: configuration warnings written 7 lines × 232 times, *"pyotp not installed"* 31×, a missing file of a disabled TOOLBOX tool. |
| **Root cause** | Warnings logged on every config re-read / every object creation. |
| **Fix** | Config warnings logged once and again only when they change; pyotp notice once (info); disabled tools skipped before the file check. |
| **Detected by** | `tests/test_log_fixes_20261018.py`; Doctor *Errors in the last 24 h* groups repeats. |
| **Rule** | A warning that can repeat must be de-duplicated at the source. |

### 4.3 "I needed more tool steps than allowed for one message" (2026-10-21, PR #33)

| | |
|---|---|
| **Symptom** | *"review all tools one by one"* stopped after 5 tool calls with a canned message. |
| **Root cause** | Chat allowed a fixed 4 follow-up rounds; at the limit all tool results were thrown away; `get_tool_documentation` answered "No documentation found" for every built-in tool, so the agent kept calling it. |
| **Fix** | `agents.max_tool_rounds` (default 12); at the limit the agent answers from what it found and the user can say "continue"; built-in tool docs and `list_tools(details=True)`. |
| **Detected by** | `tests/test_tool_step_limit.py`. |
| **Rule** | Limits are settings, never silent constants; at a limit, return the work done so far. A tool must give useful output for every input it accepts. |

### 4.4 Fixed 300-second task limit (2026-10-23, branch fix/report-20261023)

| | |
|---|---|
| **Symptom** | *"Task … FAILED - exceeded 300s timeout"* (CRITICAL) for "analyze this GitHub app and make a diagram". |
| **Root cause** | The task timer had a hard-coded 300 s maximum; with 12 tool rounds and free models it is too short, and it could also stop a job before the job's own timeout. |
| **Fix** | `timeouts.task_seconds` (default 600, 60–7200; Settings → Features → *Timeouts & memory*); jobs pass their own timeout via `TASK_MAX_SECONDS`. |
| **Detected by** | `tests/test_report_fixes_20261023.py::test_task_limit_comes_from_settings_and_jobs`. |
| **Rule** | Two limits on the same work must agree (job timeout vs task timer); the outer one wins. |

---

## 5. Agent tools

### 5.1 Agents could not create or run jobs (2026-10-21, PR #34)

| | |
|---|---|
| **Symptom** | Agents' "scheduled tasks" never appeared on the Jobs page and disappeared on restart; without Telegram `schedule` crashed (`None.run_once`). |
| **Root cause** | `schedule`/`nlp_schedule` used Telegram's in-memory JobQueue; there were no tools for the Jobs system. |
| **Fix** | `job_create`, `job_list`, `job_run`, `job_status`, `job_update`; `schedule`/`nlp_schedule` create Jobs-page jobs when the Web UI runs. |
| **Detected by** | `tests/test_agent_jobs.py`. |
| **Rule** | One scheduling system: anything an agent schedules must be visible, persistent and reported. |

### 5.2 Shell refused every `|` and `&&` (2026-10-23, branch fix/report-20261023)

| | |
|---|---|
| **Symptom** | *"Blocked command with dangerous characters"* for a pipe (`ps aux` piped into `grep zensynora`) or `pwd && ls -la` — agents spent many tool steps working around it. |
| **Root cause** | A character blacklist rejected all operators. Also: `shell.py` imported `ALLOWED_COMMANDS` once, so allow-list changes were not seen; `find -delete` / `-exec` were allowed. |
| **Fix** | A parser allows pipe, `&&`, or-chains and `;` between **allowed** commands and runs them without a shell (pipes are wired between the processes). Still refused: dollar sign, backtick, backslash, braces, redirection, parentheses, background `&`, new lines. `find -delete` / `-exec` / `-ok` / `-fprint` are blocked; the allow-list is read at call time. |
| **Detected by** | `tests/test_report_fixes_20261023.py` (parse, refuse, run). |
| **Rule** | Security checks must validate structure (each command in a chain), not ban harmless syntax; read mutable settings at call time, not at import. |

### 5.3 `read_file` errors flooded the error list (2026-10-23, branch fix/report-20261023)

| | |
|---|---|
| **Symptom** | ERROR *"Is a directory: …/workspace"*, *"No such file or directory: …/workspace/config.json"*; a 64 MB zip could be read as text. |
| **Root cause** | Agent mistakes were logged as ERROR and answered with a bare OS error. |
| **Fix** | Folders are listed; a missing file shows what is there; paths outside the workspace explain what agents may read and which diagnostic tools to use; binary files are refused and large files are cut at 200 KB; logged as info/warning. |
| **Detected by** | `tests/test_report_fixes_20261023.py`, `tests/test_tools.py`. |
| **Rule** | Errors caused by agent input are INFO/WARNING and say what to do next; ERROR is for faults in ZenSynora. |

---

## 6. Doctor, medic and integrity

### 6.1 "44 files differ" after every update (2026-10-23, branch fix/report-20261023)

| | |
|---|---|
| **Symptom** | Doctor *Code integrity*: *"44 file(s) differ: install.sh, myclaw/__init__.py …"*; agents then "restored" files with `recover_file`. |
| **Root cause** | The integrity baseline was stale only when the **version** changed. Updates install a new release folder with the same version (0.7.5 → 0.7.5), so the baseline of an older release (14 Oct) was compared with new code. |
| **Fix** | A baseline made for another install folder is stale and is re-recorded automatically. |
| **Detected by** | `tests/test_report_fixes_20261023.py::test_baseline_from_another_release_folder_is_stale`. |
| **Rule** | Anything keyed on the version must also key on the install root / release id. |

---

## 7. Install, update and CI

### 7.1 Removing another app removed Node.js; updates reused the old web UI (2026-10-21, PR #35)

| | |
|---|---|
| **Symptom** | Every update said *"npm not found; reusing the previous web UI build"*; new pages never appeared. |
| **Root cause** | `~/.local/bin/node`, `npm`, `npx` were symlinks into `~/.hermes`; removing Hermes removed them. Nothing installed Node.js; the updater only noted it. |
| **Fix** | `updater/nodejs.py`: find Node.js 20.19+ or download Node.js 22 LTS (SHA-256 verified) into `~/.local/share/zensynora/node`; `update.sh node`; preflight checks Python venv/pip; `UPDATER_PROTOCOL = 3` so old updaters hand over. |
| **Detected by** | `tests/test_updater_node.py`; Doctor *Node.js for Web UI updates* (`node_runtime`). |
| **Rule** | Every build requirement is checked and installed by the installer/updater — never assumed. Before removing software, check what links into it. |

### 7.2 An update was killed when the desktop session restarted (2026-10-21)

| | |
|---|---|
| **Symptom** | The update log stopped mid `pip install`; ZenSynora was stopped. |
| **Root cause** | ZenSynora ran from the desktop session, not as a service; the session restart ended it and the update it had started. The previous release stayed active (nothing was broken). |
| **Fix** | Run the update again (`update.sh --force`); it completed and installed Node.js 22. |
| **Detected by** | Update log ends without *✔ Updated*; Doctor *Background service* shows *inactive*. |
| **Rule** | Long operations (updates, jobs) need ZenSynora running as a service (systemd user unit) — not in a terminal or desktop session. |

### 7.3 Docs CI failed after adding diagrams (2026-10-18, PR #32)

| | |
|---|---|
| **Symptom** | GitHub check *Docs / build* failed: *"diagrams/ is out of date"*. |
| **Root cause** | New mermaid blocks were added to the docs without regenerating `diagrams/*.html`. |
| **Fix** | `python diagrams/generate.py` and commit the result. |
| **Detected by** | `.github/workflows/docs.yml`. |
| **Rule** | Every new or changed mermaid block → run `python diagrams/generate.py` before committing. |

### 7.4 CI checks that were missing or wrong (2026-10-18, PR #29)

| | |
|---|---|
| **Symptom** | CI failed on unrelated things (graphify extra, ruff running all rules, Docker build missing README, mypy without stubs); later the updater was not type-checked at all. |
| **Fix** | ruff with real-error rules, mypy with stub packages, Docker smoke test, `mypy myclaw/ updater/` (PR #35). |
| **Rule** | CI checks every shipped Python package; a red check must mean a real problem. |

---

## Rules for future changes

1. **Imports:** inside `myclaw/tools/`, imports of other `myclaw` modules use `..`. Never import lazily registered tools from `myclaw.tools` — import from the defining module. The static import test must stay green.
2. **Version-keyed state:** baselines, caches and markers keyed on the version must also key on the install root / release folder.
3. **Telegram:** one process per bot token; only one ZenSynora component owns the bot (`run_in_webui`).
4. **Diagrams:** after adding or changing a mermaid block, run `python diagrams/generate.py`.
5. **Updater:** any change in update behaviour bumps `UPDATER_PROTOCOL` so installed updaters hand over.
6. **Requirements:** installer and updater check and install every requirement (Python venv/pip, Node.js); nothing is assumed to be on PATH.
7. **Log levels:** errors caused by agent or user input are INFO/WARNING; ERROR/CRITICAL only for faults in ZenSynora. Repeating warnings are de-duplicated at the source.
8. **Tool results:** a tool returns an actionable message for every input (what is wrong, what to do next); never a bare exception text.
9. **Limits:** limits are settings with sane defaults (`agents.max_tool_rounds`, `timeouts.task_seconds`, job timeout), and nested limits agree. At a limit, return the partial result.
10. **Security checks:** validate structure (each command in a chain, each path) instead of banning harmless syntax; read allow-lists at call time.
11. **Error classes:** model-server problems (rate limit, timeout, bad key, unknown model) are not crashes — no traceback, no medic run.
12. **Stale information:** agents verify with live diagnostics before acting on a report or a knowledge note; reports carry a date.
13. **Missing features show up as agent workarounds:** when agents build their own tool for something, add it as a built-in.
14. **Long operations:** run ZenSynora as a service for updates and scheduled jobs.
15. **After fixing an error, run the feature end to end** — the next bug is often hidden behind the first.
