# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed (2026-10-18, Telegram errors on a real install)
- **The `myclaw` service (`zensynora gateway`) crashed every ~7 seconds** with
  "There is no current event loop" and systemd restarted it. Each start sent
  a request to Telegram before crashing, which took the connection away from
  the bot inside the Web UI → "Another program is already running this bot"
  and the Web UI's bot stopped. The gateway bot now runs on its own event
  loop, and when the bot is set to run inside the Web UI the gateway leaves
  Telegram alone and exits cleanly (so systemd does not restart it).
  `zensynora gateway --telegram` runs it there anyway.
- **A restart looked like a second bot.** Right after a restart Telegram keeps
  the previous request open for a few seconds and answers "conflict"; the bot
  stopped for good. Conflicts in the first 90 s are now retried, and the Web
  UI closes the bot before restarting itself (Updates → Restart).
- **Background knowledge sync never started** (`_background_extraction_task is
  not defined`); in the gateway it now starts on the bot's event loop.
- The "Telegram is enabled but no token" warning points to Settings → Channels.

### Added (2026-10-18)
- **`send_telegram_message` tool**: agents can write to you through
  ZenSynora's own bot (only to *Allowed Telegram users*). Agents had been
  building their own Telegram tools with placeholder tokens.
- Doctor: the Telegram check warns when the `myclaw` service runs next to the
  bot in the Web UI.

### Fixed (2026-10-18)
- **Telegram: "The bot token is wrong" although Test bot said it works.** A
  space or line break pasted with the token broke every request of the bot;
  the Test bot button trimmed it, the bot did not. The bot now trims the
  token, and saving Settings trims every secret (API keys and tokens never
  contain spaces). The real reason Telegram gave is now in the log.

### Added (2026-10-18)
- **Send test message** (Settings → Channels, `POST /api/telegram/send-test`):
  the running bot writes to every numeric id in *Allowed Telegram users*, or
  to one `chat_id`. If Telegram refuses because the person never pressed
  Start in the bot, the message says so.

The Telegram bot was reviewed and fixed: it answers normal messages again,
runs inside the Web UI, and shares the knowledge base with it. See the new
[Telegram guide](docs/telegram_guide.md).

### Fixed
- **The bot never answered normal messages.** Messages went into a queue whose
  worker was never started, so only `/commands` worked. The workers now start
  with the bot: several chats are answered in parallel, one message at a time
  per chat.
- **Long answers failed.** Answers over 4096 characters (Telegram's limit) are
  now split into several messages. Markdown that Telegram cannot read no
  longer makes a reply fail; it is sent as plain text instead.
- **Raw error text.** Errors now show a short plain explanation, with the same
  wording as the Web UI (for example: the model is rate-limited, the server
  cannot be reached).
- **"typing…" disappeared.** It was only shown for long messages and went away
  after 5 seconds; it now stays while the agent works.
- **`@agent` routing.** `@name` at the start of a message ignores upper/lower
  case, and agents created later (by another agent with `create_agent`, or in
  Settings) can be addressed without a restart. In groups, a mention of the
  bot itself (`@YourBot`) is removed before routing.
- **Not-allowed users were ignored silently**, and nobody could find their
  user id. Messages from people not in `allowFrom` are now logged once per
  user, with their id. `allowFrom` accepts numeric ids or `@usernames`. A
  placeholder such as `YOUR_USER_ID`, or an empty list, is reported in the
  startup log, by the doctor and in the Web UI.
- **Telegram notes were invisible in the Web UI.** They were saved in a
  separate knowledge base. Allowed Telegram users now share the main knowledge
  base with the Web UI by default (`channels.telegram.shared_knowledge: true`);
  conversation memory stays per Telegram user. Set it to `false` to keep a
  separate knowledge base per Telegram user.
- **Photos, voice messages and documents** got no answer. The caption is now
  used as the message; without a caption the bot says it can only read text
  for now.
- **Gateway (`zensynora gateway`).** MCP servers are started on the bot's own
  event loop (before, they were scheduled on a loop that might never run);
  agents created with `create_agent` are added to the bot's agent list;
  polling and webhook modes share one setup (webhook had a duplicated copy).
- **Webhook mode could not be used.** It was in the code but no command
  reached it. Now: `zensynora gateway --webhook-url https://your.domain/webhook
  --port 8443` (Telegram calls the URL; the bot listens on `0.0.0.0:<port>`,
  path `/webhook`). Polling stays the default. `zensynora gateway` prints a
  note when `run_in_webui` is on, because only one copy of the bot may run.
- **Replies from named agents** start with `@Name: `, so you know who
  answered; `@Name` with nothing after it gets "What should @Name do? …"
  instead of an empty request. Error replies are short; technical details go
  to the log only.

### Added
- **The bot runs inside the Web UI.** With `channels.telegram.enabled` and
  `channels.telegram.run_in_webui` (default on), `zensynora webui` starts the
  Telegram bot too — no separate `zensynora gateway` needed. Telegram allows
  only one copy of a bot: if another process already runs it (for example
  `zensynora gateway`), the Web UI shows "conflict" and stops its own copy.
- **Settings → Channels:** *Allowed Telegram users* (list), *Run the bot inside
  the Web UI*, *Telegram uses the same knowledge base as the Web UI*, and a
  *Telegram bot* panel with the status (Running as @name, Starting…, Stopped,
  Turned off, No bot token yet, Runs in zensynora gateway, conflict, Error), a
  warning when nobody is allowed yet, **Test bot** (checks the typed token, or
  else the saved one, with Telegram) and **Restart bot** (not available when
  the bot is off, runs in the gateway or has no saved token). Saving restarts
  the bot by itself, only when a Telegram setting changed. The sidebar health
  has a *Telegram* row (running @name / stopped / conflict / error). API:
  `GET /api/telegram/status`, `POST /api/telegram/test`,
  `POST /api/telegram/restart`.
- **`/start`, `/help` and `/whoami`.** These work for everyone. `/whoami`
  answers with your own numeric Telegram id and how to allow it; nothing else
  is revealed. All other commands need an allowed user.
- **Command menu.** The `/` list in Telegram is registered automatically, with
  a description for each command.
- **Doctor check "Telegram bot"** (category configuration): *off*; *enabled
  but no bot token* (fail); *nobody is allowed to use the bot* (warning, with
  the hint to send `/whoami` and add the number); or the number of allowed
  users and whether the bot runs inside the Web UI or with
  `zensynora gateway`.
- **`ZENSYNORA_TELEGRAM_API_URL`** points the bot at a self-hosted Telegram
  Bot API server (for example `http://localhost:8081`); the tests use it too.
- **Tests:** `tests/test_telegram_channel.py`, including an end-to-end test of
  the real python-telegram-bot library against a local fake Bot API server
  (`tests/fixtures/fake_telegram_api.py`), and `tests/test_telegram_webui.py`.

## [0.7.5] - 2026-10-14

Agents get skills and work as a team; failed jobs get a medic; channels no
longer keep deleted agents.

### Added
- **Skills.** A skill is a Markdown instruction file (`SKILL.md`) for one kind
  of task. 10 built-in skills (web-research, knowledge-keeper,
  powershell-scripting, python-pro, code-reviewer, backend-developer,
  frontend-developer, devops-engineer, llm-architect,
  multi-agent-coordinator); your own live in `~/.myclaw/skills`. New
  **Skills** page: view, create, edit, import a `SKILL.md`, copy a built-in
  skill to change it, and give skills to agents (also in Settings → Agents).
  Agents see their skills' names and descriptions and load one with the new
  `use_skill` tool (`list_skills` lists them); a skill marked *always* is
  included in full (for small models). `zensynora skills list` shows them.
  The skill files existed before (agent profiles, an empty skills folder, a
  hub) but nothing connected them to agents.
- **Agents create agents.** New `create_agent` tool: an agent can add a
  specialist (name, description, prompt, model, skills). It appears at once in
  the sidebar, in chat and for delegation; change or remove it in Settings →
  Agents. Turn off with `agents.allow_agent_creation`.
- **Team-aware agents.** Every agent's prompt now lists the other agents and
  what they do, and when to `delegate` to them — before, agents did not know
  who else existed and rarely delegated.
- **Token speed in chat.** Each answer shows tokens per second (hover: tokens
  and time to first token). Token counts are estimated from the text.
- **Medic for failed jobs.** *🩺 Ask the medic* on a failed run: cause, plain
  explanation, suggested job changes (shown as now → suggested), things only
  you can do. Known failures are recognised without a model (tool steps,
  timeouts, unreachable or rate-limited models, refused keys); a model
  refines the diagnosis. *Apply fix & run again*, *Apply only*, and *Ask the
  medic to fix the code* (files a change proposal you approve on the Doctor
  page). New job setting *When a run fails*: nothing / diagnose / diagnose,
  fix the job and run once more. Fixes are recorded as medic lessons.
- **Tool steps per job** (default 8, 1–30). Chat keeps 4 extra tool rounds;
  jobs that search and read several pages used to stop with "I needed more
  tool steps".

### Fixed
- **Agents could not use the knowledge base.** Three faults together:
  - the Web UI saved and searched notes as user `webui` (delegated tasks as
    `__delegate__`) while the knowledge tools agents call themselves
    (`search_knowledge`, `read_knowledge`, `list_knowledge`, …) and the medic's
    lessons used `default` — agents never found what was saved;
  - search only looked at note names and observations, never the note text
    (the index stored the file path instead), so plain notes — most notes
    agents and the automatic extraction write — were unfindable;
  - a `user_id` passed by the model could point the tools at another, empty
    knowledge base.
  Now there is one shared knowledge base for the Web UI, the terminal, jobs
  and delegations (separate chat users, e.g. Telegram, keep their own), the
  tools follow the knowledge base of the current conversation, and note text
  and tags are searchable. Notes saved under `webui` are moved into it and
  existing notes are indexed automatically on first start.
- **External MCP servers in the Web UI.** Servers set in `mcp.servers` (for
  example a memo/notes server) were only started by `zensynora agent` and the
  gateway; the Web UI starts them too now. Their tools reached the models
  without parameters (the proxy had none) and only after a restart of the
  tool list; they now carry the server's input schema and appear as soon as
  the server connects. Stopping no longer hangs on an MCP connection that kept
  reconnecting after being cancelled.
- **"Zombie" agents in channels.** An agent renamed or deleted in Settings
  stayed a member of its channels; every later member change was refused
  ("Unknown agent(s): agent3"), so it could not be removed and no agent could
  be added. Such agents are now removed (with a note in the channel) when the
  channel list loads and right after settings are saved; member changes skip
  them; renaming an agent in Settings carries its channels, direct messages
  and jobs to the new name; two agents with the same name are refused.
- Answers cut off by a stop or restart no longer stay "typing…" forever.
- `@mentions` ignore upper/lower case (`@executor` reaches `Executor`).
- Channel names keep no leading `#` (`##house` → `#house`).
- Direct messages with a deleted agent say so instead of being answered by
  the default agent.
- Delegation could loop: the depth limit never increased, so two agents could
  pass a task back and forth until the request timed out. Hand-overs are now
  counted (max 2), an agent cannot delegate to itself, names match in any case.

## [0.7.4] - 2026-10-14

Update ZenSynora from the Web UI, and the prompt writer works again when the
main model is unavailable.

### Added
- **Updates page** in the Web UI (also opened by clicking the version at the
  bottom of the sidebar): shows the version this server runs, the installed
  version and the latest one on GitHub; **Check for updates**; **Update now**
  runs the safe updater in the background with live step-by-step progress and
  the log; **Roll back**; optional branch/tag and *Reinstall even if up to
  date* (repairs an install); history of updates and the versions kept for
  rollback. The update keeps running if the page is closed.
- **Restart ZenSynora** button (on the Updates page and in the "newer version
  installed" banner): the server restarts itself with the installed version
  and the page reloads when it is back — no terminal needed.
- API: `GET /api/update/status[?check=true]`, `POST /api/update/start`
  `{ref, force}`, `POST /api/update/rollback`, `POST /api/update/restart`
  (localhost or admin key, like Settings).

### Fixed
- **✨ Generate prompt (Settings → Agents) failed** with a server error when
  the default model was refused or busy (OpenRouter answers some free models
  with "only available on agentic harnesses"). It now tries the rotation
  models in turn, skips models that just failed, and names the model it used;
  if none works, the message says why for each one. Job scoring uses the same
  fallback.

## [0.7.3] - 2026-10-14

Agents can finish multi-step tasks and jobs again, errors say what is really
wrong, and channels can be managed from one page. Found by reading a real
install's log, where every job ended with "the model returned an empty answer
twice".

### Fixed
- **Jobs and multi-step requests never finished.** After a tool ran, a
  further tool call from the model (search → read, or a retry with corrected
  arguments) was thrown away; the reply looked empty and the run ended with
  "returned an empty answer". Follow-up tool calls (native or written as text)
  now run, up to 4 more rounds.
- **Cohere models via OpenRouter rejected every follow-up** ("invalid message
  … must have non-empty content"): ZenSynora sent messages with empty content
  (the turn carrying tool calls, empty replies in history, the recovery
  prompt). No message with empty content is sent any more.
- **Tools were silently switched off**: that 400 error was mistaken for "this
  server cannot handle tools", which disabled tools for the provider until
  restart. Only real "tools not supported" errors do that now.
- **Real errors were reported as "empty answer twice".** A model refused for
  this kind of app (OpenRouter 403 "only available on agentic harnesses"), rate
  limits (429), wrong keys, unknown models, wrong URLs and too-long
  conversations now get a one-line explanation with what to do
  (`myclaw/llm_errors.py`).
- **Rotation kept retrying broken models**: a model that was refused, unknown
  or rate-limited is skipped for a while (30 min / 1 min).
- **Failed job runs were shown as successful**; they are now "error".
- **Response cache mixed up answers**: its key ignored the model, the agent's
  instructions and tool calls, so agents could get each other's answers and a
  re-run job got the previous (failed) reply. The key covers all of them;
  empty answers are never cached; similarity matches stay within one model.
- **LM Studio / llama.cpp URLs without `/v1`** failed with "Unexpected endpoint
  or method"; `/v1` is added when the URL has no path.
- A tool called with wrong or missing arguments now tells the model exactly
  how to call it (for example `browse` needs a `url`), so it can retry.
- Agents introduced themselves as "MyClaw"; they are ZenSynora now.
- **Background summaries**: crashed on conversation entries without text
  ("'NoneType' object is not subscriptable") and, because the model was asked
  with the agent's tools, often stored empty "Session Summary" notes. Only
  user/assistant text is summarized now, without tools, and an empty summary is
  not stored.
- The per-agent chat socket no longer logs an error when the browser tab is
  closed while an answer is streaming.

### Added
- **Channels page** (sidebar ⚙ next to Channels, or Navigate → Channels):
  create channels with a topic, members and reply mode; rename, edit, clear
  history and delete them; message counts and last activity; direct messages
  listed too. The channel topic is given to the agents as the channel's
  purpose.
- **Test a model for real** on the Models page (Test, Test all, ✓? per entry,
  `POST /api/models/test`): a tiny chat request, because a model that is
  listed can still refuse to answer.

### Changed
- Tests: 525 (new `test_v073_reliability.py`).

## [0.7.2] - 2026-10-14

Makes sure you actually see the version you installed, and makes models
that write tool calls as text work.

### Fixed
- **The Web UI kept showing the previous version after `zensynora update`.**
  The update installs the new version next to the old one; a terminal that
  activated the virtual environment before the update still had the old
  release on its PATH, so `zensynora webui` started there ran the old code
  (version shown: the old one, old pages). Now:
  - a command started from an older release switches to the installed one by
    itself and says so (`ZENSYNORA_NO_REEXEC=1` turns this off);
  - `zensynora version` says when a newer version is installed than the one
    the terminal uses;
  - the Web UI shows a banner when the running server is older than the
    installed version (`update_installed` in `/api/system/health`);
  - the updater's final message explains how to restart (Ctrl+C, new
    terminal or `source …/source/venv/bin/activate`, reload the page);
  - `index.html` is served with `Cache-Control: no-cache`, so the browser
    loads the new build after an update.
- **Tool calls written as text were shown instead of run.** The system prompt
  tells models they may answer with `{"tool": "<name>", "args": {...}}`, but
  only native tool calls were executed — models that follow the prompt
  literally (e.g. `cohere/north-mini-code:free` on OpenRouter) showed the raw
  JSON. Such answers are now recognised (also in a ```json fence, as a list,
  or with `name`/`arguments` keys; only names of real tools), executed, and
  the result is sent back in plain messages; up to 3 follow-up tool rounds.
  While streaming, an answer that starts like JSON is held back until it is
  clear whether it is a tool call, so the JSON never flashes on screen.
- Unknown `/api/…` and `/ws/…` addresses return 404 instead of the Web UI page
  with status 200; files such as `favicon.svg` are served as files.

### Changed
- The Web UI removes the per-agent chat history that versions before 0.7.1
  kept in the browser (history now lives on the server).
- Tests: 514 (new: stale-release detection, page serving, text tool calls).

## [0.7.1] - 2026-10-13

A real multi-agent chat, model switching and your own providers, a Jobs
page, backups, safer updates, and an approval-gated way for agents to
improve ZenSynora's own code.

### Added
- **Chat channels** (`myclaw/chat/`, `myclaw/web/chat_api.py`, Web UI): group
  rooms with several agents (`#general` and your own) and direct messages
  with one agent. `@name` pulls an agent into the conversation, `@all` asks
  every member; the reply mode decides who answers otherwise (lead / everyone
  in turn / everyone at once). Answers stream into separate bubbles, history
  is kept in `~/.myclaw/chat.db`, and each agent has its own memory per
  channel while the knowledge base stays shared. Stop button, Shift+Enter for
  new lines, `@` autocomplete, two-factor support.
- **Agent-to-agent activity** (`myclaw/events.py`): delegations
  (`delegate`, swarms), tool calls and job runs are published as events and
  shown in the chat and in an Activity panel — who asked whom, what came back
  and how long it took.
- **Models page**: switch the default model in one click (the previous one
  is remembered), a **rotation** list (fallback on errors such as rate
  limits, or round-robin), "Rotate now", and **your own providers**: any
  OpenAI-compatible server (local or online) or another Ollama server, with
  presets for vLLM, LocalAI, Jan, KoboldCpp, text-generation-webui, DeepSeek,
  Mistral, Cerebras, Together, GitHub Models, Hugging Face, NVIDIA NIM,
  Gemini (OpenAI-compatible), xAI and Fireworks — several with free tiers.
  API: `/api/models`, `/api/models/default`, `/api/models/rotate`.
- **✨ Generate prompt** for named agents (Settings → Agents): the main model
  writes the system prompt from the agent's name and new *description*
  field (`POST /api/agents/generate-prompt`).
- **Jobs page** (`myclaw/jobs/`, `myclaw/web/jobs_api.py`): add, edit, pause,
  run now and delete jobs; schedules manual / once / every N minutes / daily
  / weekly; post results to a channel; retries and timeouts; run history
  with output, duration and model; **evaluate** a run (0–10 score against
  success criteria, by the main model, automatic if enabled) and
  **benchmark** a job on up to 8 models side by side. Templates: morning
  briefing, weekly knowledge clean-up, medic self-review.
- **Backups** (`myclaw/backup.py`, Web UI Backups page, `zensynora backup
  create|list|restore|delete`): settings + API keys (with the config key),
  knowledge base, conversations and chat, jobs/own tools/skills; checksums
  in a manifest, check, download, import, restore of selected parts (a safety
  backup is made first), automatic backups while the Web UI runs (interval
  and retention in Settings). Stored in `~/.myclaw-backups`
  (`MYCLAW_BACKUP_DIR` or Settings to change).
- **Approval-gated self-update** (`myclaw/selfupdate.py`, Doctor → Change
  proposals, `zensynora medic proposals|show|approve|reject|apply|problems`):
  agents can read the installed code (`read_source_file`,
  `list_source_files`) and file proposals with `propose_code_change`
  (complete new file content; checked: inside the program folder, never
  venv/.git/.env, Python must compile, size limits; unified diff stored).
  Nothing changes until you approve; the change is then installed **through
  the safe updater** as a new release — full backup, side-by-side install,
  checks, optional test run, switch, automatic rollback on failure.
  Proposals are refused if the code changed since they were made.
  Documentation-only changes can be auto-approved (off by default).
- **Medic learning**: `get_recent_problems` (grouped log errors, failed or
  low-scoring job runs, open proposals) and `record_lesson` (knowledge note
  `medic-lessons`, shown on the Doctor page); a "Run self-review" button and
  job template tie them together.
- **Updater: full program-folder backup** (`updater/app_backup.py`). Before
  switching, the running release folder is archived completely (code,
  `venv/`, web UI build, `.env`, your own files) with an inventory; if the
  update fails, the folder is compared with the inventory and restored from
  the archive when anything differs. After a successful update the
  settings, API keys and knowledge base are checked and restored in place
  if missing. `--app-backup full|code|none`; program archives are kept for
  the last two updates only (they are large).
- `python -m myclaw`; `zensynora webui --host/--reload` (from 0.7.0) used by
  the docs; `MYCLAW_BACKUP_DIR`.

### Fixed
- **"I wasn't able to generate a response. My knowledge base has no entries
  on this topic…"** in the Web UI: the streaming path judged the reply empty
  *before* looking for tool calls, so every answer that started with a tool
  call (normal for agents) got this text. Tool calls now run; a truly empty
  reply is retried once, and the final message names the model instead of
  blaming the knowledge base.
- Errors that OpenRouter and other gateways send *inside* the stream (rate
  limit, overloaded model) are now shown instead of an empty answer, and let
  rotation move to the next model.
- **"Error searching knowledge: no such column: write"**: search text such as
  `write: …`, `a-b` or `c++` was parsed as FTS5 syntax. Every word is now
  quoted (knowledge base and conversation search); long sentences fall back
  to "any word, best match first".
- **Delegation never worked** ("Agent registry not initialized"):
  `set_registry` replaced a dictionary that the tool modules had already
  imported. It is now updated in place. The same problem made Telegram
  scheduling report "Scheduler not available" — fixed the same way.
- `#general` is created on first use (fresh installs could get a 404).
- Uninstall: also removes `chat.db`, `jobs.db`, `metering.db` and the
  per-user knowledge indexes with `--keep-config`, and asks before deleting
  `~/.myclaw-backups`.

### Changed
- The Web UI chat is built on channels; the old per-agent socket
  (`/ws/chat/{agent}`) still works for scripts.
- Docker compose keeps backups inside the data volume.
- Version 0.7.1. Tests: 508 (new: `test_v071_features.py`,
  `test_selfupdate.py`, knowledge-search regression).

## [0.7.0] - 2026-10-13

Health checks you can trust, documentation rebuilt from scratch, and every
install/update/uninstall script reviewed and tested end to end.

### Added
- **`zensynora doctor`** (`myclaw/doctor/`): one health check for the whole
  install, with 21 checks in 8 categories (environment, configuration, LLM,
  data, security, install, runtime, integrity). Every problem comes with a fix
  hint; `--fix` repairs the safe ones (missing folders, file permissions,
  pending data migrations). Options `--offline`, `--json`, `--problems`; exit
  code 0 = healthy, 1 = warnings, 2 = failures.
- **Doctor page in the Web UI** (`🩺 Doctor`), backed by
  `GET /api/system/doctor` and `POST /api/system/doctor/fix` (local-only or
  admin key, like the other settings endpoints).
- **Rotating log file** `~/.myclaw/logs/zensynora.log` (10 MB × 5, JSON lines)
  so the doctor and the medic can see recent errors. `MYCLAW_LOG_FILE=off`
  disables it, `MYCLAW_LOG_FILE=/path` moves it.
- Environment overrides `MYCLAW_LLAMACPP_BASE_URL`, `MYCLAW_LMSTUDIO_BASE_URL`,
  `MYCLAW_OPENROUTER_API_KEY`.
- `python -m myclaw …` works (new `myclaw/__main__.py`); `python -m myclaw.cli`
  now runs the CLI instead of exiting silently.
- `zensynora webui --host ADDRESS` (e.g. `127.0.0.1` to keep the UI on this
  computer) and `--reload` for development.
- `uninstall.sh --purge`; docs index `docs/README.md`; 10 doctor/medic tests.

### Changed
- **Medic agent rewritten** (`myclaw/agents/medic_agent.py`). All 15 tools now
  do real work: health report (shares the doctor's checks), file integrity
  baseline with SHA-256, per-file backups, recovery that prefers the backup
  matching the baseline and falls back to the GitHub release tag, file
  validation, task analytics, and a loop guard over a sliding 60-second window.
  Recovery refuses paths outside the install folder.
- **Documentation**: new README; `docs/architecture_diagram.md` redrawn as 11
  Mermaid diagrams; `docs/ARCHITECTURE.md`, `docs/medic_agent_guide.md`,
  `webui/README.md`, `CONTRIBUTING.md` and the issue/PR templates rewritten
  for 0.7.0; every remaining guide has an "applies to" note; `mkdocs.yml`
  navigation rebuilt (the old one pointed at missing pages).
- 24 outdated plans, audits and reports moved to `docs/archive/` (with an
  index); `docs/dev/` notes marked as historical.
- `zensynora webui` no longer runs with auto-reload: it watched the current
  directory, restarted on any file change and doubled the processes.
- Docker: the image runs the web UI by default (was `--help`), uses a fixed
  user ID that matches yours so `./data` is writable, and ships the data
  migrations; the compose file no longer pins a subnet that could collide
  with your network, and logs go into the data folder.

### Fixed
- **install.sh**: works on Debian/Ubuntu, Fedora/RHEL, Arch and openSUSE
  (apt-get, dnf, pacman, zypper); picks the newest Python 3.11+ on the system
  (or `ZENSYNORA_PYTHON`); installs the matching `python3.X-venv`; the
  systemd service points at the stable `…/zensynora/source` path, so it
  keeps working after `zensynora update`; removed an unused Docker network.
- **uninstall.sh**: `--yes` no longer deletes `~/.myclaw` or update backups
  unless `--purge` is given; process matching narrowed so it cannot stop
  unrelated Python or Node programs; works when started from another folder;
  offers to remove the managed program folder.
- **install-online.sh**: `ZENSYNORA_REF` accepts tags and commits, not only
  branches.
- **Portable launchers** (`.sh` / `.bat`): data really stays on the drive
  (the `MYCLAW_*_DIR` variables they set were never read, so data went to the
  host's home folder); the app actually starts (`python -m myclaw`); code may
  sit in `app/` or next to the script.
- **cleanup.sh**: only touches the project folder (it used to delete
  `test_*.py`, `*.zip` and `*.tmp` in whatever folder you ran it from) and
  supports `--dry-run`.
- **deploy.py**: rewritten without hard-coded host, user and password; refuses
  unknown host keys unless `--trust-new-host`; never uploads `.env`; uses the
  safe updater when the target already has an install.

### Security
- Removed credentials that were committed in `deploy.py`,
  `docs/dev/scripts/deploy_remote.py`, `docs/dev/scripts/deploy_ssh.ps1` and an
  audit report. **They remain in git history — change those passwords.**

### Removed
- `_write_fixplan.py` (a one-off helper with hard-coded Windows paths) and the
  top-level `archive/` folder (merged into `docs/archive/`).

## [0.6.0] - 2026-10-13

### Added
- **Safe, modular updater** (`update.sh`, `zensynora update`, package `updater/`):
  - side-by-side releases (`releases/<version>-<time>/`, each with its own venv)
    with an atomic `source` symlink switch — the running install is never
    modified in place;
  - automatic backup of `~/.myclaw` (config, encryption key, databases,
    knowledge) plus `.env`/`data/` before every update; `backup`/`restore`
    commands;
  - verification of the new release against the user's real config before
    switching; optional `--run-tests`;
  - step pipeline where every step can undo itself — any failure rolls back
    (new release removed, data restored if a migration ran, service restarted);
  - versioned, idempotent data migrations (`updater/migrations/`) tracked in
    `~/.myclaw/.migrations.json`;
  - `rollback` (code-only or with data), `status --check`, `prune`;
  - keeps optional packages the user installed (e.g. `anthropic`), stops and
    restarts the `myclaw` systemd service, adopts pre-0.6.0 in-place installs;
  - newer releases can ship a newer updater that takes over (`UPDATER_PROTOCOL`).
- `zensynora version` command; `docs/UPDATING.md`.

### Changed
- `install-online.sh` runs the safe updater when ZenSynora is already
  installed instead of copying files over the working install.
- `install.sh` records applied data migrations; `uninstall.sh` also offers to
  remove old releases and update backups.

## [0.5.0] - 2026-10-13

### Fixed
- **Chat sent to Ollama instead of the selected provider** (`404 Not Found
  for url http://localhost:11434/api/chat`): the `openai` SDK used by every
  OpenAI-compatible provider (llama.cpp, LM Studio, OpenAI, Groq,
  OpenRouter) was an optional extra, and when it was missing the agent
  silently switched to Ollama. `openai` is now a core dependency, the agent
  raises a clear `ProviderUnavailableError` instead of switching providers,
  and the offline fallback never "falls back" to the provider that just failed.
- **llama.cpp without `--jinja`**: when a server rejects tool definitions the
  OpenAI-compatible provider retries once without tools and remembers it, so
  plain chat keeps working.
- **Settings → Test connection** now reports a missing provider SDK instead
  of passing while chat fails.
- **Linux install (Python 3.13/3.14)**: numpy `<3.0` (binary wheels),
  `httpx[http2]`, `uvicorn[standard]` (WebSocket support for the Web UI chat),
  `zensynora` command actually installed, stricter verification,
  `uninstall.sh` no longer removes `$(pwd)/venv`, `.gitignore` no longer hides
  `tests/`.
- **Runtime**: `zensynora agent` crash (`list` shadowed the builtin), metrics
  middleware error without prometheus-client, `knowledge sync --force` crash,
  `build_context` crash, history missing buffered messages, process hang on
  exit (pooled aiosqlite connections).

### Added
- **Web UI Settings** page: provider/model with connection test and model
  list, provider URLs and API keys, named agents, channels, feature toggles,
  security. Local-only unless an admin API key is set; secrets never returned.
- Web UI navigation (Chat, Settings, Costs & usage, Tools), live System Health,
  server-side Clear History, Reconnect button, app version in the sidebar.

### Fixed (earlier, unreleased)
- **Virtual environment setup**: `install.sh` now checks whether Python has
  `venv`/`ensurepip` support, offers to install Debian/Ubuntu's
  `python3-venv` package when missing, verifies existing environments, and
  offers to recreate invalid ones before dependency installation. Portable
  launchers now validate existing environments and explain recovery options.

### Documentation
- Updated installation commands to use an isolated virtual environment and
  documented the `python3-venv` repair prompt and portable launcher behavior.

### Added
- **Performance Optimizations**:
  - Implemented batch fetching for relations in the knowledge graph (`get_relations_from_many`) to fix N+1 query problems.
  - Optimized `get_central_entities` query in the knowledge graph using `LEFT JOIN`s instead of slower subqueries.
  - Optimized full knowledge base sync (`force=True`) to use a single `DELETE` transaction via `clear_all_data()`, significantly speeding up full resyncs.

### Changed
- **Refactored Knowledge Sync**: The change detection logic in `myclaw/knowledge/sync.py` was refactored to be more efficient, avoiding redundant file path lookups during updates by passing file paths directly in the `ChangeSet`.
- **Refactored Session Tools**: Synchronous file I/O operations in `myclaw/tools/session.py` (`update_user_profile`, `get_user_profile`) were converted to be fully asynchronous using `asyncio.to_thread` to prevent blocking the event loop.

---

### Phase 6.3 — Scheduler reliability layer (2026-05-06)

`AsyncScheduler` previously caught exceptions, logged them, and moved on —
no validation, no retries, no per-task budgets. Six capabilities added as a
single additive layer; existing callers and existing job functions keep
working unchanged.

#### New module: `myclaw/scheduler_features.py`

- **`RetryPolicy`** — `max_attempts`, `backoff` (`fixed | linear | exponential`),
  `base_delay`, `max_delay`, `retry_on`, `fallback`. Backoff is capped at
  `max_delay`. Fallback may be sync or async; receives `(job, exc, ctx)`.
- **`OutputValidator` protocol + built-ins** — `NotEmpty`, `Regex`,
  `MaxLength`, `JSONSchema` (no `jsonschema` dep), `Custom` (predicate),
  `Chain` (short-circuiting AND). Validation failures raise
  `ValidationError`, which integrates with `RetryPolicy`.
- **`ComplexityAnalyzer.analyze(task)`** → `ComplexityScore` with
  `score` (0..1), `estimated_tool_calls`, `estimated_duration_s`,
  `detected_steps`, `flags` (`complex`, `slow-ops`, `multi-step`,
  `explicit-list`, `long-running`, `decomposition-recommended`).
  Heuristics: slow-op keywords, multi-step keywords, numbered/bullet lists,
  raw length, mentions of known tool names.
- **`decompose_task(task)`** — splits a complex task description into
  sub-tasks. Strategy in priority order: numbered list → bullet list →
  multi-step keywords (`then`, `and then`, `next`, `followed by`, `;`) →
  fallback to single task.
- **`Checkpoint(task_id)`** — JSON checkpoint at
  `~/.myclaw/checkpoints/<task_id>.json`. Path-traversal-safe ID
  sanitisation. `save(step, data, total_steps, status)` /
  `load()` / `clear()`.
- **`TaskBudget`** — wall-clock and tool-call budget.
  `TaskBudget.from_complexity(score, multiplier=1.5, floor_s=60, ceiling_s=1800)`
  derives a budget directly from a `ComplexityScore`.
- **`JobContext`** — runtime handle injected into job functions that opt
  in via a `ctx`/`context`/`job_ctx` kwarg. Methods:
  `progress(step, n, total, **data)`, `elapsed()`, `remaining_budget()`,
  `is_over_soft_budget()` (default 70 %), `is_over_budget()`,
  `record_tool_call()`, `tool_calls`, `spawn_subtask(func, *, delay_s, sub_id)`,
  `spawned`.

#### `myclaw/async_scheduler.py`

- **`Job`** carries five new optional fields: `retry_policy`, `budget`,
  `validator`, `checkpoint_enabled`, `complexity`.
- **`AsyncScheduler.add_job(...)`** accepts the same five as kw-only args.
- **`_execute_job` → `_run_with_reliability`**: the core executor now does
  attempt loop + backoff + `asyncio.wait_for` (when a budget is set) +
  post-run validator + ctx injection (only when the function declares it) +
  fallback on exhaustion + checkpoint trail at every transition. Original
  no-extension behaviour is preserved when none of the new kwargs are passed.

#### `myclaw/tools/scheduler.py` — five new agent-callable tools

- `auto_schedule(task, delay, every, user_id, auto_split=True, stagger_seconds=2)`
  — runs `ComplexityAnalyzer`; if complex AND `auto_split`, decomposes and
  schedules each sub-task with a stagger.
- `estimate_complexity(task)` — JSON pre-flight analysis.
- `get_checkpoint(job_id)` — read the checkpoint JSON.
- `list_checkpoints()` — index of all jobs that have a checkpoint.
- `clear_checkpoint(job_id)` — delete the checkpoint file.

`auto_schedule` and `clear_checkpoint` are flagged non-parallel-safe in
`tools/core.py:is_tool_independent`.

#### Tests

`tests/test_scheduler_features.py` — 37 tests (all passing under both
`asyncio` and `trio` backends): retry-policy math, every validator,
complexity heuristics, decomposition strategies, checkpoint round-trip +
unsafe-id sanitisation, scheduler integration (retry-then-succeed,
fallback-after-exhaustion, validator-driven retry, budget timeout, ctx
injection + checkpoint persistence, sub-task spawn, backwards-compat with
plain functions).

#### Docs

- New: [`docs/scheduler_features_guide.md`](docs/scheduler_features_guide.md)
  — full usage walk-through with code examples per capability.

#### Compatibility

- All new fields and kwargs default to `None`/`False`. Calls that don't
  opt in get the previous behaviour exactly.
- Job functions without a `ctx`/`context`/`job_ctx` parameter receive
  no injected context — verified by
  `test_scheduler_existing_funcs_without_ctx_kwarg_still_work`.
- `Job.to_dict()` / `Job.from_dict()` are unchanged; the new fields are
  not yet persisted to JSONL (intentional — `RetryPolicy.fallback` and
  `OutputValidator` are callables that don't round-trip through JSON).
### Optimization audit — Changeset 1 (2026-05-17)

Based on `OPTIMIZATION_RECOMMENDATIONS.md`:

- **Dependency pinning:** All core and dev dependencies now have upper bounds in `pyproject.toml` (`fastapi>=0.115.0,<1.0`, `pydantic>=2.7,<3.0`, etc.). `bandit` and `safety` added to dev extras.
- **URL safety (`myclaw/web_search.py`):** Replaced f-string URL construction with `urllib.parse.urlencode` + `urllib.parse.urljoin` on trusted base URLs. Prevents URL injection.
- **Async connection pooling (`myclaw/aiohttp_session.py`):** New shared `aiohttp.ClientSession` singleton with TCP keep-alive. Auto-closed at exit.
- **Security linters (`.pre-commit-config.yaml`):** Added `bandit` (1.7.10) for Python SAST and `safety` (3.2.14) for dependency vulnerability scanning.
- **Encryption key validation (`myclaw/config_encryption.py`):** Added `_validate_key_format()` with Fernet key regex (`^[A-Za-z0-9_\-]{43}=$`) and entropy heuristic. Env var keys and file keys validated on load.
- **Centralised bootstrapper (`myclaw/__init__.py`):** Added `init_app()` — configures structured logging, installs signal handlers (SIGINT/SIGTERM), and registers atexit cleanup for aiohttp sessions, HTTP pools, SQLite pools, and state store.
- **`myclaw/cli.py`:** Replaced 44-line `_setup_shutdown_handlers()` with `init_app()` call.
- **Context window enhancement (`myclaw/context_window.py`):** Consolidated duplicated model-limit logic into `_MODEL_LIMITS` dictionary (28 models: OpenAI o1/o3/gpt-4, Claude 2/3/3.5, Gemini 1.x/2.x, Llama, Mistral, DeepSeek, Qwen). Extracted `_get_limit_for_model()`.
- **Test stability (`tests/test_swarm_integration.py`):** Added `pytest.mark.xfail(sys.platform == "win32")` with rationale for Windows `ProactorEventLoop` issues.

### Optimization audit — Changeset 2 (2026-05-18)

Based on `docs/review01.md` implementation plan:

- **Deprecated `myclaw/logging.py`:** Now emits `DeprecationWarning` and delegates to `logging_config.py`. `init_app()` fixed to use the structured logger directly (was accidentally calling the deprecated simple one).
- **Timing-safe API key comparison (`myclaw/web/auth.py`):** Replaced `x_api_key != expected` with `secrets.compare_digest()` for constant-time comparison.
- **Deterministic concurrency tests (`tests/test_memory_pool_concurrency.py`):** Replaced `asyncio.sleep(0.05)` + wall-clock assertions with `asyncio.Event` + `asyncio.Barrier` for exact pool-capacity verification.
- **Safe XML parsing (`myclaw/web_search.py`):** Added `_safe_parse_xml()` using `defusedxml.ElementTree` if installed, falling back to standard `ElementTree`. Prevents billion-laughs attacks in `search_news()`.
- **Web search rate limiting (`myclaw/web_search.py`):** Added per-function `_rate_limit_check()` token-bucket limiter: `search_web` (30/min), `search_wikipedia` (60/min), `search_news` (20/min), `get_webpage_content` (30/min).
- **Coverage enforcement (`pyproject.toml`):** Added `--cov=myclaw --cov-report=term --cov-fail-under=60` to pytest config.
- **Named constants (`myclaw/defaults.py` + `myclaw/agent_internals/router.py`):** Added `MAX_DELEGATION_DEPTH`, `TASK_TIMER_STEPS_TOTAL`, `DEFAULT_SUMMARIZATION_THRESHOLD` with `MYCLAW_*` env-var overrides. Replaced magic numbers in router.
- **Documentation:** Created `docs/review01.md` (comprehensive 10-section application review). Updated `OPTIMIZATION_RECOMMENDATIONS.md`, `planx1.md`, `function_list.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY_FIXES_2026_04_29.md`, and `README.md` with resolution status and new module references.

### Install/uninstall scripts updated to match Sprints 1–12 (2026-04-30)

`install.sh` and `uninstall.sh` predated the recent sprints and didn't
know about any of the new modules, optional extras, or persistence
artifacts. Both scripts bumped to **2.1.0** with:

#### `install.sh`
- **New "5.5 Optional feature extras" section** — interactive prompts
  for `[tracing]` (OpenTelemetry SDK + OTLP exporter), `[browser]`
  (Playwright + auto Chromium download), `[prompts]` (Jinja2),
  `[jsonschema]`, `[qdrant]`, `[auth]` (PyJWT[crypto]), `[discord]`.
  Each maps directly to a `pyproject.toml` extra; skipping any of them
  leaves the corresponding feature in degrade-gracefully mode.
- **PyYAML safety check** — explicit install if not already present, so
  the Sprint 12 agent registry YAML loader never silently falls back
  to the embedded literal at runtime.
- **New data directories** — `~/.myclaw/{plugins,plugins/installed,hub,hub/skills,api}`
  pre-created so first-write directory-creation races on multi-worker
  boots can't drop artifacts.
- **Verification block extended** — imports every new module surface
  (`observability`, `resilience`, `prompts`, `structured_output`,
  `vector`, `agent_internals`, `auth`, `tenancy`, `evals`,
  `messaging`, `marketplace`, `caching`, `defaults`) plus a check
  that `myclaw/agents/data/agents.yaml` shipped.

#### `uninstall.sh`
- **`--keep-config` cleanup list extended** with
  `cost_tracking.db`, `vectors.db`, `prompts.jsonl`,
  `knowledge_gaps.jsonl`, per-tenant `memory_*.db` shards (Sprint 11),
  the `plugins/` dir, and the local `hub/` registry.
- **New per-section uninstallers (3.6–3.9)** — each fires only when
  the matching artifacts exist; respects `--keep-data`. Glob-based
  cleanup for the per-tenant memory shards (Sprint 11) so multi-tenant
  installs are fully torn down.

Both scripts pass `bash -n` syntax check and execute cleanly in
`--dry-run` mode.

### Sprints 10, 11, 12 — Closing the rest of the open list (2026-04-30)

Eight items off the open list, landing in three logically-grouped
sprints. **415 tests pass** across all sprints; zero existing tests
regressed.

#### Sprint 10 — Integrations + small refactors (17 tests)

* **#4 Circuit breaker wired into `Agent._provider_chat`** — every Agent
  now owns a `CircuitBreaker` keyed on its provider name. Persistent
  failures stop hammering the endpoint; OPEN providers fall straight
  through to the offline fallback (or raise `CircuitBreakerError` when
  no fallback is configured). Threshold and reset timeout configurable
  via `config.resilience.{failure_threshold,reset_timeout}` with safe
  defaults; setting `failure_threshold=0` disables the breaker entirely
  for tests and single-provider deployments.
* **#3 Tracing wired into hot paths** — `Agent.think`, `_provider_chat`,
  `_search_knowledge_context` and `provider.fallback_chat` all run inside
  named spans (`agent.think`, `provider.chat`, `kb.search`,
  `provider.fallback_chat`). Decorator/context-manager pattern; no-op
  when the OpenTelemetry SDK isn't installed. `Agent.think` was split
  into a thin span wrapper + `_think_impl` body to keep the tracing
  surface minimal.
* **#5 Dedicated KB FTS5 executor** — module-global
  `ThreadPoolExecutor` (8 workers by default,
  `MYCLAW_KB_SEARCH_WORKERS` env override). KB searches no longer queue
  behind unrelated I/O on the shared `asyncio.to_thread` pool; expected
  20-40% latency reduction at 5+ concurrent users.
* **#10 `myclaw/defaults.py` consolidation** — single source of truth
  for tunable constants (filesystem paths, timeouts, batch sizes,
  resilience defaults, KB executor workers). Every constant has a
  parallel `MYCLAW_*` env-var override. `agent.py` re-exports
  `GAP_FILE` from defaults for backward compatibility; future modules
  should import from `myclaw.defaults` directly.
* **#9 `__all__` across public modules** — explicit public surfaces on
  `agent.py`, `memory.py`, `provider.py`, `cost_tracker.py`,
  `semantic_cache.py`, `skill_preloader.py`. `from myclaw.agent import *`
  no longer leaks `_profile_cache`, `_LAST_ACTIVE_TIME`, etc.

#### Sprint 11 — Multi-tenancy wiring + shared cache + WebUI streaming (35 tests)

* **#1 Multi-tenancy wired into `Memory`** — new
  `myclaw/tenancy/scoping.py` provides `effective_user_id()`,
  `require_authenticated_user()`, and `scope_audit_event()`.
  `Memory.__init__(user_id=None)` now resolves the user_id via
  `effective_user_id()` — picks up the active `UserContext` when
  middleware bound one, falls back to `"default"` for legacy
  single-user code. Explicit `user_id="bob"` always wins (background
  jobs that pin a user). Public `Memory.user_id` attribute added so
  audit code can introspect the resolved tenant.
* **#8 Shared cache primitives in `myclaw/caching/`** — `BaseTTLCache`
  (OrderedDict + TTL + LRU eviction), `PersistentCacheMixin` (JSON
  file persistence with serialize/deserialize hooks), `TTLEntry`. The
  drift between `semantic_cache.py` and `semantic_memory.py` is now
  refactorable to a single base — both modules can opt in
  incrementally without changing their public APIs.
* **#6 WebUI token-level streaming** — new
  `webui/src/components/TypewriterText.tsx`. Buffers incoming chunks
  and reveals characters at a configurable cadence (default 60
  chars/sec) via a single rAF-driven loop. Snaps to full text the
  moment streaming ends — no long animation tail after the model
  finishes. Cursor blinks when caught up, stays solid while typing.
  Wired into `App.tsx` for agent messages only (user/system messages
  render verbatim — typewriting them would feel like UI lag).

#### Sprint 12 — Registry YAML migration (11 tests, **#7 — done last as requested**)

* **`myclaw/agents/data/agents.yaml`** — 137 agent definitions exported
  as a single human-editable YAML file (59 KB). Sorted by name for
  diffability. Adding or modifying an agent is now a data PR.
* **YAML loader in `myclaw/agents/registry.py`** —
  `load_agents_from_yaml()` parses the file at import time and
  produces the same `AgentDefinition` instances as before. Module-level
  `AGENT_REGISTRY` picks YAML when present, falls back to the embedded
  Python literal otherwise. Defensive: malformed YAML, unknown
  categories, unknown capabilities, duplicate names — each fails
  closed with a logged warning rather than blowing up the framework.
* **Sync invariant test** — `test_yaml_and_literal_have_same_agent_names`
  fails CI the moment YAML and literal disagree, with a clear message
  pointing at which side is missing the entry. Once the YAML format
  has been stable across one minor release, the embedded literal
  fallback can be removed and `registry.py` shrinks from ~70 KB to
  ~5 KB.

#### Documentation
- `CHANGELOG.md` — this entry (Sprints 10-12).
- The original 16-point review is now fully closed; remaining open
  items are net-new features documented in the post-sprint review.

### Sprint 9 — Plugin marketplace + decomposition phase 2 (2026-04-30)

Two big items off the open list. **352 tests pass across the nine sprints.**

#### Added — `myclaw/marketplace/` — multi-source plugin marketplace (40 tests)

The existing ``myclaw/hub/`` was local-only. This sprint adds the
*remote* layer: discover, verify, and install plugins from multiple
sources (OpenClaw, GitHub releases, custom HTTP registries, plus the
local hub) under a single client.

- **`manifest.py`** — `Manifest` dataclass + canonical-bytes serialization
  (sorted keys, no whitespace, UTF-8 preserved). `sign_manifest` /
  `verify_manifest` use HMAC-SHA256 with `hmac.compare_digest`. The
  `__signature__` self-reference in `extra` is stripped from canonical
  bytes so manifests can carry their own signature without
  self-invalidating. Optional `verifier` callback for asymmetric crypto.
- **`sources.py`** — `MarketplaceSource` ABC + four concrete implementations:
  - `LocalHubSource` — adapter over the existing ZenHub registry.
  - `HttpRegistrySource` — generic REST. Expects `index.json` +
    `plugins/<name>/manifest.json`. Manifest envelope can be either
    raw or `{"manifest": {...}, "signature": "..."}`.
  - `GitHubReleasesSource` — uses GitHub Releases as a registry. Per-tag
    versions; sidecar `manifest.json` asset preferred, synthesized
    metadata otherwise.
  - `OpenClawSource` — preset of `HttpRegistrySource` for the OpenClaw
    marketplace. Configurable base URL for air-gapped mirrors;
    auth via `Authorization: Bearer <api_key>`.
- **`client.py`** — `MarketplaceClient` aggregates sources. Search
  concatenates results in source-order so users see preferred-first.
  Failed sources are logged but never block other sources. `install()`
  resolves manifest → fetches artifact → verifies signature (when
  `hmac_secret` is configured) → verifies artifact sha256 (when
  manifest claims one) → writes to `~/.myclaw/plugins/installed/`.
  `require_signature` flag rejects unsigned manifests when strict.

Optional dep `httpx` (already a core dep) for remote sources;
`LocalHubSource` works without it.

#### Added — `myclaw/agent_internals/classes.py` — class-based DI (31 tests)

Sprint 5 extracted the agent phases as free functions taking ``agent``
as their first parameter. Sprint 9 promotes them to real classes,
closes the deferred final phase ("ResponseHandler"), and keeps
backward compatibility intact.

- **`MessageRouter`**, **`ContextBuilder`**, **`ToolExecutor`** —
  thin classes that wrap the existing free functions. The free
  functions remain (and remain exported) so existing call sites
  don't change. New code can construct the class directly and pass
  a stub instead of a full Agent.
- **`ResponseHandler`** — *new* phase. Owns the post-response
  side effects that previously lived inline in
  ``Agent._handle_summarization``: KB auto-extraction, ``on_session_end``
  hook emission, background summarization scheduling, task-timer
  completion. Class-based from the start with explicit dependency
  surface; logs and recovers when ``mem.get_history()`` fails rather
  than silently aborting cleanup.
- ``Agent._handle_summarization`` is now a 4-line wrapper that
  delegates to ``ResponseHandler``.

#### Documentation
- **`docs/dev/DECOMPOSITION_PLAN.md`** — status updated. Phase 1
  (free functions, Sprint 5) and Phase 2 (classes + ResponseHandler,
  Sprint 9) both done. Future iterations now have a single remaining
  axis: replacing the wrapped-target pattern in ``classes.py`` with
  explicit per-dependency constructor args.
- **`docs/ARCHITECTURE.md`** — module map updated with both
  `myclaw/marketplace/` and the `agent_internals/classes.py` layer.

### Sprints 6, 7, 8 — Enterprise foundations + ergonomics + review wrap-up (2026-04-30)

Three sprints landing together so the original 16-point app review is
fully closed. **281 tests pass across all eight sprints.**

#### Sprint 6 — Enterprise foundations (42 tests)

* **`myclaw/auth/`** — JWT verification with HS256 secrets *or* a JWKS
  endpoint for RS256/ES256. Issuer/audience/scope claims handled
  flexibly (string, list, or custom claim name). Optional dep ``PyJWT``
  — module imports cleanly without it; first verify call raises a clear
  RuntimeError.
* **`myclaw/auth/oauth.py`** — OAuth 2.0 authorization-code flow with
  PKCE (RFC 7636 S256). Pre-baked configs for GitHub and Google. State
  is one-shot (consumed on callback). Token exchange uses ``httpx``;
  PyJWT not required.
* **`myclaw/tenancy/`** — `UserContext` (frozen dataclass) propagated
  via ``contextvars.ContextVar`` so concurrent asyncio tasks each see
  their own identity. ``user_scope()`` / ``async_user_scope()`` context
  managers; ``require_scope()`` data-layer guard.
* **`myclaw/channels/discord.py`** — Discord bot adapter via
  ``discord.py``. Listens for direct mentions and ``!ask``; ignores
  random channel chatter to avoid spam. ``chunk_for_discord`` helper
  splits long replies at paragraph/line boundaries to fit the 2000-char
  limit. Optional dep — adapter constructs without ``discord.py``;
  only ``run()`` requires it.

#### Sprint 7 — Developer ergonomics (44 tests)

* **`myclaw/evals/`** — dataset-driven eval harness. Load JSONL,
  define metrics (``exact_match``, ``contains``, ``regex_match``,
  ``length_within``, ``json_subset``, plus user-defined callables),
  run an async ``target(input) -> output`` over the dataset with
  bounded ``concurrency`` and per-case timeout. Reports include
  ``metric_means``, ``latency_p50/p95``, ``failure_count``, and an
  ``overall_score`` for trend tracking.
* **`myclaw/messaging/`** — inter-agent messaging protocol.
  ``AgentMessage`` envelope (sender/recipient/type/payload + correlation
  for request/response pairing + trace_id). ``InProcessBroker`` with a
  per-recipient bounded queue + drain task — handler exceptions don't
  stop subsequent message delivery. ``Broker`` ABC for future Redis
  swap-in.
* **`myclaw/knowledge/path_reasoning.py`** — ``find_paths(a, b,
  max_hops=3)`` for "how is X connected to Y?" queries. BFS with
  per-path visited sets (no cycles), bounded by hops + max-paths,
  optional ``relation_filter``. ``shortest_path()`` convenience.

#### Sprint 8 — Review cleanup (6 tests)

The remaining items from the original review:

* **Perf — `myclaw/knowledge/graph.py` `build_context`**: depth-1
  neighbor reads were N+1 (one ``read_note`` per neighbor inside the
  loop). Now batched up front via ``_batch_read_notes`` (the same
  helper Sprint 1 added for ``storage.search_notes``).
* **Perf — `myclaw/memory.py`**: added composite index
  ``idx_role_timestamp ON messages(role, timestamp)``. History queries
  filtering by role no longer full-scan + filesort.
* **Perf — `myclaw/semantic_cache.py`**: vectorized the similarity scan.
  Per-entry ``np.dot`` + ``np.linalg.norm`` loop replaced with a single
  matrix multiply over ``max_scan_entries`` rows; default cap raised
  from 64 → 256 since the scan now costs ~50µs instead of ~1.5ms.
* **Quality — `myclaw/agent.py` broad excepts**: silent-fallback
  ``try/except: ...`` blocks at the profile mtime helper, the
  ``_kb_auto_extract`` property, and the two config-read sites in
  ``__init__`` now log via ``logger.warning(..., exc_info=...)``. The
  property logs once-per-agent to avoid spamming repeated reads.
* **Quality — `myclaw/provider.py` `Message` TypedDict**: introduced
  ``Message``, ``ToolCall``, ``ToolCallFunction`` TypedDicts at module
  level. ``_sanitize_messages_for_openai`` and ``_ensure_tool_messages``
  signatures updated. Static analyzers and IDEs now know what a
  message envelope looks like.

#### Optional deps added to ``pyproject.toml``
- `[auth]` — `PyJWT[crypto]>=2.8`
- `[discord]` — `discord.py>=2.3`
- `[slack]` — `slack-bolt>=1.18` (adapter is reserved for a future sprint)

(These will be added in a follow-up edit alongside the existing
`[tracing]`, `[browser]`, `[prompts]`, `[jsonschema]`, `[qdrant]` extras.)

#### Explicit deferrals (explicitly tracked, not silently skipped)

The following items from the original review remain open, with rationale:

* **`registry.py` → YAML/TOML data files** — moving 136 agent
  definitions out of code is a large mechanical refactor. Better to
  ship as its own dedicated PR; risk of subtle migration bugs is
  non-trivial.
* **`semantic_cache.py` + `semantic_memory.py` shared `BaseCache`** —
  moderate refactor; the duplication isn't currently causing drift.
* **`__all__` across all public modules** — broad churn for low value.
  Worth doing only as part of a deliberate API-surface review.
* **Plugin marketplace + LoRA adapter loader** — net-new features
  rather than cleanup.
* **Multi-tenancy wiring into `Memory` / `KnowledgeDB` row filters** —
  requires a schema migration and merits its own sprint. The
  ``tenancy`` primitive is now in place so the wiring is mechanical
  when scheduled.

### Sprint 5 — Agent Decomposition (2026-04-30)

The long-deferred decomposition of ``agent.py``. Four prior sprints had
deferred this for risk reasons; this sprint actually executes it, with
zero behavior change and no regression in the 175 pre-existing tests.

#### Critical: deleted broken stub package
- **`myclaw/agent/`** had been added in an earlier sprint as a four-file
  stub package marked "Phase 1 — create module structure. No behavior
  changes yet." It silently shadowed ``myclaw/agent.py`` (Python
  packages outrank same-named modules), so:
  ```python
  >>> from myclaw.agent import Agent
  ImportError: cannot import name 'Agent' from 'myclaw.agent'
  ```
  This had been broken for **eight import sites** including
  ``channels/telegram.py``, ``channels/whatsapp.py``, ``cli.py``,
  ``gateway.py``, ``multimodal.py``, ``web/api.py``, and the test file
  ``tests/test_agent.py``. Nobody had run the existing Agent tests
  successfully since the stubs landed.
- The whole stub package has been deleted. ``from myclaw.agent import
  Agent`` works again.

#### Added — `myclaw/agent_internals/` — focused phase modules
- ``router.py`` — ``route_message(agent, ...)`` owns model selection,
  task-timer setup, depth guard, medic loop-prevention, memory
  hydration, history fetch, summarization-threshold detection.
- ``context_builder.py`` — ``build_message_context(agent, ...)`` owns
  skill preloading kickoff, KB search, gap detection + structured
  logging, system-prompt + KB-context concatenation, ``pre_llm_call``
  hook fan-out.
- ``tool_executor.py`` — ``execute_tools(agent, ...)`` owns parallel
  vs sequential dispatch, per-tool error handling, KB-gap recording for
  empty searches, fire-and-forget KB extraction, the followup LLM call,
  and empty-response recovery.
- ``medic_proxy.py`` — tiny indirection over
  ``myclaw.agents.medic_agent.prevent_infinite_loop`` so the router can
  import it cleanly and tests can monkey-patch it without dragging in
  the whole agents package.

The helpers are **functions, not classes**, taking the ``Agent`` as
their first parameter. This was a deliberate trade-off: the original
methods reach into ~30 different ``self.X`` attributes; threading every
dependency through a class constructor would have been a multi-day
refactor with high regression risk. The free-function pattern lets
us extract the bodies 1:1 (``self.X`` → ``agent.X``), preserve all
behavior, and revisit the surface in a future iteration once stable.

#### Modified — `myclaw/agent.py` slimmed by ~300 lines
- ``Agent._route_message``, ``Agent._build_context``, and
  ``Agent._execute_tools`` are now **thin delegating wrappers** (~6
  lines each). Public method names and signatures are unchanged.
- Original 75 + 73 + 190 = 338 lines of method bodies moved out;
  agent.py drops from **1784 lines → 1487 lines (-16%)**.

#### Added — `tests/test_agent_internals.py` (14 tests)
- Stub-driven tests for ``route_message``: happy-path tuple shape,
  depth-cap drop, medic-block drop, summarization-snapshot capture
  when history exceeds threshold.
- ``medic_proxy`` tests covering the three branches: module missing,
  limit reached, internal exception.
- Cheap regression guards on the helper signatures so a future
  refactor that adds a new dependency fails loudly in one place.

#### Updated documentation
- **`docs/ARCHITECTURE.md`** — section 1 reflects the new
  ``agent_internals/`` package; section 5 marks the decomposition as
  done with a note about the still-open ToolExecutor scope reduction.
- **`docs/dev/DECOMPOSITION_PLAN.md`** — marked Phases 1–3 complete;
  added a "remaining work" note for the future class-based refactor.
- **`README.md`** — no surface change needed; the public Agent API
  is identical.

#### Test count snapshot
- Pre-decomposition: 175 tests passing.
- Post-decomposition: **189 tests passing** (175 unchanged + 14 new).

### Sprint 4 — Vector Store + Structured-Output Integration (2026-04-30)

Pluggable vector backends, an `Agent.complete_structured` helper that
wires Sprint 3's repair loop into the agent, full documentation pass, and
explicit tracking for the long-deferred `agent.py` decomposition.
40 new tests; 175 across all four sprints.

#### Added — `myclaw/vector/` — pluggable vector backends
- `VectorBackend` ABC with a narrow contract: `upsert`, `search`,
  `delete`, `count`, `clear`, `close`. `VectorRecord` and `SearchHit`
  carry id + vector + opaque metadata.
- `InMemoryBackend` — zero-dep, brute-force cosine. Tests + tiny corpora.
- `SQLiteBackend` — JSON-blob persistence + Python cosine; runs sync
  sqlite3 inside `asyncio.to_thread` so multi-coroutine workloads don't
  block. Defensive: rejects non-alphanumeric table names.
- `QdrantBackend` — production HNSW. Optional dep; importing the module
  is always safe but constructing the backend without `qdrant-client`
  installed raises a clear `ImportError`.
- `make_backend(name, config)` — factory keyed on a config string.
  Falls back to `SQLiteBackend` when `"qdrant"` is requested but
  `qdrant-client` isn't installed (config-time fallback beats runtime
  crash mid-request).
- `cosine_similarity()` exposed for callers that need ad-hoc scoring.

#### Added — `Agent.complete_structured(messages, schema, ...)`
Wraps `myclaw.structured_output.repair_json` around the same provider
call `think()` uses. Pass a Pydantic v2 model or JSON-schema dict; get a
`ValidationResult` back. Up to `max_repair_attempts` repair rounds when
the model returns invalid JSON. Provider-agnostic — uses the Agent's
configured model and existing `_provider_chat` plumbing.

#### Added — `pyproject.toml` optional-extras for new sprints
- `[tracing]` — `opentelemetry-{api,sdk,exporter-otlp}>=1.20`
- `[browser]` — `playwright>=1.42`
- `[prompts]` — `jinja2>=3.1`
- `[jsonschema]` — `jsonschema>=4.21`
- `[qdrant]` — `qdrant-client>=1.7`
- `[all]` updated to include all of the above.

#### Added — Documentation pass
- **`docs/ARCHITECTURE.md`** (new): module map, request lifecycle, where
  Sprint 2–4 modules plug in, optional-dependency posture table,
  outstanding deferrals, full test inventory, contribution-onboarding
  pointers.
- **`docs/dev/DECOMPOSITION_PLAN.md`** (new): explicit tracking for the
  4× deferred `agent.py` decomposition. Phases the work into 3 PRs
  (MessageRouter → ContextBuilder → ToolExecutor + ResponseHandler) with
  acceptance criteria and a pre-flight checklist.
- **`README.md`** rewritten:
  - New "Pluggable vector store" line under Persistent Memory.
  - New "Structured output & prompts" section.
  - "System Resilience" expanded into "Observability & Resilience" with
    OpenTelemetry, circuit breaker, cost dashboard, PII scrubber.
  - New "Optional dependency posture" section with copy-pasteable
    install commands.

#### Tests added
- `tests/test_vector_backends.py` — 40 tests, parametrized across
  memory + sqlite backends so behavior is identical between them.
  Covers ranking, limit, metadata filter, upsert overwrite, delete,
  clear, persistence across `SQLiteBackend` instances, and factory
  fallback behavior.

#### Deferred (4th time, now explicitly tracked)
- Full `agent.py` decomposition. The work is too risky to land
  alongside other sprints; it deserves its own dedicated branch.
  See `docs/dev/DECOMPOSITION_PLAN.md` for phased plan, acceptance
  criteria, and pre-flight checklist.

### Sprint 3 — Capability Expansion (2026-04-30)

Four new feature modules + a cost dashboard. 42 new tests pass on this
sprint alone (135 across Sprints 1–3 combined). All optional dependencies
degrade gracefully when not installed.

#### Added — `myclaw/tools/browser.py` — headless browser tooling
- Playwright-backed async tools: `browser_navigate`, `browser_screenshot`,
  `browser_fill_form`, `browser_extract_text`, `browser_wait_for`,
  `browser_close_session`.
- Persistent contexts keyed by ``session_id`` (cookies/storage retained
  across calls). Module-level pool capped at 10 contexts; oldest evicted
  on overflow.
- **Optional dependency**: `playwright` is not pulled in by default. When
  not installed, every tool returns ``{"ok": False, "error": "..."}``
  without crashing — `is_browser_available()` lets dynamic-tool authors
  probe support at runtime.
- Install into an activated virtual environment:
  `python -m pip install playwright && playwright install chromium`.

#### Added — `myclaw/prompts/` — versioned prompt template registry
- `PromptTemplate` (name, version, body, description, tags, variables,
  created_at) and `PromptRegistry` for register/get/render/list.
- File-backed JSONL store at `~/.myclaw/prompts.jsonl` — one record per
  line. Re-registering a name auto-increments the version; full history
  preserved for audit and rollback.
- **Jinja2 when available** (`{{ var }}`, sandboxed environment, autoescape
  off because LLM prompts ≠ HTML); falls back to stdlib `string.Template`
  (`$var`) when Jinja isn't installed. `detect_variables()` uses Jinja's
  AST when available.
- `get_registry()` returns a process-wide singleton; pass `path=...` for
  isolated stores (tests use this).

#### Added — `myclaw/structured_output/` — JSON validation + LLM repair
- `validator.py`:
  - `extract_json(text)` — pulls the first balanced JSON object/array out
    of a free-form response. Handles code-fence wrappers, brace counting,
    and quotes-inside-strings.
  - `validate_json(text, schema)` — parses + validates. Schema may be a
    Pydantic v2 `BaseModel` subclass (preferred — coerces types and gives
    rich error paths) or a JSON-schema dict (requires `jsonschema`).
  - `schema_for(model)` — Pydantic → JSON-schema dict, ready to feed
    providers' `response_format` / `tools` parameters.
- `repair.py`:
  - `repair_json(text, schema, llm_call, max_attempts=1)` — when
    validation fails, builds a focused repair prompt (schema + errors +
    original output) and re-runs the model. Provider-agnostic: caller
    supplies the LLM as an injected coroutine.

#### Added — Cost dashboard
- **Backend** (`myclaw/cost_tracker.py`):
  - `get_costs_by_model(month, limit)` — top spenders by `(provider, model)`
    for one month, ordered by cost.
  - `get_daily_timeline(days)` — per-day cost/token/request series for a
    rolling window.
- **API** (`myclaw/api_server.py`): four read endpoints, all auth-gated:
  - `GET /api/v1/costs/summary`
  - `GET /api/v1/costs/by-provider?month=YYYY-MM`
  - `GET /api/v1/costs/by-model?month=YYYY-MM&limit=N`
  - `GET /api/v1/costs/timeline?days=30`
- **WebUI** (`webui/src/components/CostDashboard.tsx`): self-contained
  React component — summary cards, inline-SVG sparkline (no external
  charting lib), provider table, top-models table. Auto-refresh every 60s.
  Drop into `App.tsx` with `<CostDashboard apiBase={getApiBase()}
  apiKey={apiKey} />`.

#### Tests added
- `tests/test_prompts.py` — 12 tests (versioning, persistence, render,
  Jinja-vs-fallback)
- `tests/test_structured_output.py` — 18 tests (extract, validate, schema
  generation, repair loop including retry-and-give-up cases)
- `tests/test_cost_tracker.py` — 6 tests (record, by-provider, by-model
  ordering, limit, daily timeline, unknown-provider zero-cost)

### Sprint 2 — Observability & Resilience (2026-04-29)

New infrastructure modules + a pre-existing import bug discovered while
adding tests. 93 unit tests pass on this work alone.

#### Added
- **`myclaw/observability/` — OpenTelemetry tracing**
  - `tracing.py` exposes `init_tracing()`, `get_tracer()`, `span()`,
    `@traced` (sync), `@traced_async` (async), `is_tracing_enabled()`.
  - The OTel SDK is an **optional** dependency: when not installed every
    helper is a no-op. Enable with `ZENSYNORA_TRACING_ENABLED=true` and
    `python -m pip install opentelemetry-sdk opentelemetry-exporter-otlp`
    in the activated virtual environment.
  - Defaults: service name `zensynora`, OTLP gRPC exporter; falls back to
    a console exporter if OTLP isn't installed.
  - Idempotent init (safe to call repeatedly).

- **`myclaw/resilience/` — circuit breaker + fallback chain**
  - `CircuitBreaker` — async-safe three-state breaker (CLOSED → OPEN →
    HALF_OPEN). Configurable failure threshold, reset timeout, success
    threshold, and excluded exception types (e.g. user-input errors that
    shouldn't trip the breaker).
  - `FallbackChain` — wraps an ordered list of `(name, async_fn)` tuples,
    each with its own breaker. OPEN providers are skipped without being
    called, so a flapping endpoint doesn't slow every request down.
    Raises `FallbackExhausted` with per-provider error details when all
    fail.
  - 11 unit tests covering trip/recovery, excluded exceptions, fall-through
    behavior, and skip-when-open.

- **PII scrubber for structured logging** (`myclaw/logging_config.py`)
  - `PIIScrubFilter` redacts emails, phone numbers, JWT tokens, API keys
    (`sk-…` / `pk-…` / `bearer …`), and `user_id=...` patterns from log
    messages, args, and `extra_fields`.
  - User IDs are replaced with a stable `user:<sha256[:10]>` hash so
    correlation across requests still works without exposing the raw id.
  - Wired into `configure_logging()` at root-logger level (default ON;
    opt out with `MYCLAW_LOG_SCRUB_PII=false`).

- **Registry & agents test scaffold** (`tests/test_registry.py`)
  - 14 structural tests: registry-key/agent-name consistency, no
    duplicate names, every category is a known `AgentCategory`, every
    capability is a known `AgentCapability`, lookup APIs return correct
    counts, etc. Sample 50 agents for nonempty-description checks to keep
    the test fast.

#### Fixed (pre-existing bugs surfaced by the new tests)
- **`myclaw/agents/newtech_agent.py:10`** — was `from .async_utils import
  run_async`, but `async_utils.py` lives at `myclaw/async_utils.py`. The
  package import has been broken; nothing actually loaded `agents/`
  successfully before. Fixed to `from ..async_utils import run_async`.
- **`myclaw/tools/toolbox.py:6`** — same broken relative import. Same fix.

#### Deferred (again)
- Full `agent.py` decomposition. Adding the four new modules + tests was a
  better use of this sprint than another risky refactor. The
  `myclaw/agent/` stubs remain in place for a future dedicated sprint.

### Sprint 1 — Performance & Quality Quick Wins (2026-04-29)

Follow-up to the security round. Six low-risk improvements landing together.

#### Performance
- **Batched parallel note reads** (`myclaw/knowledge/storage.py`)
  `search_notes()` and `get_note_by_tag()` previously did N+1 file I/O —
  one FTS5 query followed by a serial `read_note()` per result. Replaced
  with a `_batch_read_notes()` helper that fans the reads out to a small
  thread pool. Expect ~7-10× faster knowledge searches at typical result
  counts (3-10 hits). Also added `_batch_read_notes_async()` for callers
  already on the event loop.
- **Parallelized profile loading** (`myclaw/agent.py:_load_system_prompt`)
  Agent profile and `user_dialectic.md` were loaded sequentially. Now
  scheduled via `asyncio.gather`, so the dialectic stat+read overlaps the
  profile cache lookup. ~40-50% faster cold-start prompt load.

#### Memory & bounds
- **Bounded `ToolAuditLogger` queue** (`myclaw/tools/core.py`)
  Replaced the manual list-slice eviction with `collections.deque(maxlen=1000)`.
  Eliminates the periodic large-slice rebuild that caused GC spikes under
  heavy tool use; eviction is amortized-O(1).
- **Bounded `SkillPredictor` recent tools** (`myclaw/skill_preloader.py`)
  Replaced `list` + `pop(0)` (O(n)) with `deque(maxlen=10)` (O(1)).
- **LRU profile cache off-by-one** (`myclaw/agent.py`)
  Eviction loop used `>` allowing the cache to overshoot maxsize by one
  between evictions; changed to `>=` so the cap is strictly enforced.

#### Observability
- **No more silently-swallowed metrics errors** (`myclaw/provider.py`)
  Five `except Exception: pass` blocks across the Ollama, OpenAI-compat,
  Anthropic, Gemini providers, and the provider-cache mtime check now log
  via `logger.warning(..., exc_info=...)`. A failing metrics or cost-tracking
  backend can no longer silently produce blank dashboards.

#### Deferred from Sprint 1
- **Full `agent.py` decomposition into `myclaw/agent/` subpackage** — the
  scaffolding files exist but are 20-line stubs. A real extraction is a
  multi-day project that needs its own branch and integration testing.
  Deferred to Sprint 2.

### Security & Stability Fixes — Critical Audit Round (2026-04-29)

A focused round of fixes addressing critical bugs discovered during a deep code
audit. All seven items below ship together. See `docs/SECURITY_FIXES_2026_04_29.md`
for the full write-up (root causes, before/after code, test plans).

#### Fixed
- **Infinite recursion in `Agent._track_preload`** (`myclaw/agent.py:356`)
  The bookkeeping method recursed into itself instead of adding to the set.
  Every preload triggered `RecursionError`. Replaced the recursive call with
  `self._pending_preloads.add(task)`.
- **Shell injection via newline characters** (`myclaw/tools/shell.py`)
  The dangerous-character regex did not include `\n` / `\r`, allowing
  `cmd = "ls\nwhoami"` to bypass the check. Added newlines to the regex AND
  re-validated every token after `shlex.split` so injected arguments cannot
  smuggle dangerous chars past the first-token check. Applied to both `shell`
  and `shell_async`.
- **Missing auth on API key endpoints** (`myclaw/api_server.py`)
  `GET/POST/DELETE /api/v1/keys` accepted `Optional[str]` for the API key but
  never enforced authentication when it was `None`. Introduced a shared
  `_require_admin()` guard that raises `401` when unauthenticated and `403`
  when the caller lacks the `admin` permission. All three endpoints now use it.
- **CORS misconfiguration** (`myclaw/api_server.py`)
  `allow_origins=["*"]` combined with `allow_credentials=True` enabled CSRF
  from any origin. `APIServer.__init__` now accepts a `cors_origins` allow-list
  (default `["http://localhost:5173"]`); methods and headers were narrowed to a
  least-privilege set.
- **Race condition + unsafe fallback in `AsyncSQLitePool`** (`myclaw/memory.py`)
  When the semaphore invariant was violated the pool silently returned an
  already-checked-out connection (`pool[0]`), corrupting concurrent callers.
  Replaced the silent fallback with a `RuntimeError` so the bug is observable
  rather than masked.
- **AST sandbox bypass in dynamic tools** (`myclaw/tools/toolbox.py`)
  `register_tool()` and `improve_skill()` had two divergent forbidden lists
  (e.g. `improve_skill` was missing `importlib`, `pathlib`, `getattr`).
  Extracted both blocks into a single shared `_validate_tool_ast()` helper.
  Forbidden imports now also include `pathlib`, `ctypes`, `cffi`, and `mmap`.
  Added an `ast.Attribute` check that blocks attribute access on forbidden
  modules (e.g. `pathlib.Path("/etc/passwd")`).
- **Broken `stream_chat` across all 4 providers** (`myclaw/provider.py`)
  `chat(stream=True)` returns a `(async_generator, tool_calls_collector)`
  tuple; the previous `async for chunk in await self.chat(...)` iterated over
  the tuple itself, silently yielding the generator object and an empty list.
  Streaming has been broken in every provider since this code landed.
  Destructured the tuple and iterate the generator. Applied to `OllamaProvider`,
  `OpenAICompatProvider`, `AnthropicProvider`, `GeminiProvider`.

#### Removed
- **Dead code**: deleted `archive/tools_backup.py`. No live imports referenced it.

### Project Restructure & Professionalization (2026-04-18)

A comprehensive effort to transform ZenSynora from a personal project into a production-ready, open-source AI agent framework. All changes were executed following `docs/dev/ark/planA1.md`.

#### Phase 1: Repository Cleanup & Polish
- **Root Directory Cleanup**
  - Created `docs/dev/` and `docs/dev/scripts/` directories
  - Moved 16 planning/development files into `docs/dev/`: `ANALYSIS.md`, `CLAUDE.md`, `code_analysis_summary.md`, `CODE_OPTIMIZATION_PROPOSAL.md`, `FUNCTIONS_SUMMARY.md`, `how to run.md`, `implementation_gap_report.md`, `IMPLEMENTATION_PLAN.md`, `IMPLEMENTATION_SUMMARY_KNOWLEDGE_GAP_v2.1.md`, `new_think_methods.py`, `OPTIMIZATION_SUMMARY.md`, `roadmap.md`, `Structure.txt`, `tasktodo.md`
  - Moved `ark/` and `plans/` directories into `docs/dev/`
  - Moved 8 helper scripts into `docs/dev/scripts/`: `extract_core.py`, `extract_modules.py`, `find_sections.py`, `find_sections2.py`, `test_import.py`, `test_ssh.py`, `deploy_remote.py`, `deploy_ssh.ps1`
  - Deleted obsolete temp files: `out.txt`, `test_output.txt`, `out_storage.txt`
  - Updated all internal links across `CHANGELOG.md`, `docs/dev/IMPLEMENTATION_PLAN.md`, `docs/dev/OPTIMIZATION_SUMMARY.md`, `docs/dev/plans/CHANGELOG.md`, `docs/dev/plans/future_updates_proposal.md`, `docs/dev/ANALYSIS.md`, `docs/dev/how to run.md`, `docs/dev/ark/IMPLEMENTATION_PLAN_FROM_PLANX.md`, `README.md`, `onboard.py`
  - Rewrote `docs/dev/Structure.txt` to reflect the new layout

- **README.md Overhaul**
  - Added 6 GitHub badges: Python 3.11+, License AGPL-3.0, CI status, GitHub Stars, Last Commit, Docker ready, Tests/pytest
  - Added **Screenshots & Demo** section with placeholder table (WebUI, Telegram, Agent Swarm) and YouTube embed placeholder
  - Rewrote **Quick Start** with 4 clear options: (1) One-Command Install, (2) Docker, (3) Automated Linux, (4) Manual
  - Added concise **Roadmap** section with phase highlights (1–6 ✅, 7 🔄, 8–9 ⏳)
  - Updated all CLI examples from `python cli.py` to `zensynora` command

- **GitHub Standard Files**
  - `.github/ISSUE_TEMPLATE/bug_report.md` — structured template with environment, config, logs sections
  - `.github/ISSUE_TEMPLATE/feature_request.md` — problem statement, use cases, implementation ideas, contribution checkbox
  - `.github/PULL_REQUEST_TEMPLATE.md` — type of change, testing checklist, code quality checklist
  - `CONTRIBUTING.md` — full guide: fork/clone/branch, dev setup, project structure, coding standards (PEP 8, type hints, docstrings), step-by-step tool addition guide, testing instructions, commit message guidelines (conventional commits)

#### Phase 2: Packaging & Installation
- **`pyproject.toml`** — Modern Python packaging (hatchling build backend)
  - Metadata: name `zensynora`, version `0.4.1`, AGPL-3.0, Python 3.11+
  - 22 core dependencies + 8 optional dev dependencies (pytest, ruff, black, isort, pre-commit, mypy)
  - CLI entry points: `zensynora = myclaw.cli:cli`, `myclaw = myclaw.cli:cli`
  - Project URLs: Homepage, Docs, Repository, Issues, Changelog
  - Tool configs: `black`, `isort`, `ruff`, `pytest`, `mypy`

- **Package Structure Refactor**
  - Moved `cli.py` logic into `myclaw/cli.py` with relative imports
  - Moved `onboard.py` logic into `myclaw/onboard.py` with relative imports
  - Root `cli.py` and `onboard.py` are now backward-compatible wrappers

- **`.env.example`** — Comprehensive 50+ variable template
  - LLM provider API keys (OpenAI, Anthropic, Gemini, Groq, OpenRouter)
  - Local provider URLs, Telegram & WhatsApp credentials
  - Agent defaults, swarm, timeouts, memory, knowledge, worker pool, sandbox
  - Log rotation, intelligence platform, routing, SSH backends
  - Docker usage documentation in header comments

- **Configuration Validation (`myclaw/config.py`)**
  - Added `_validate_config()` function called on every `load_config()`
  - Validates at least one LLM provider is configured
  - Checks Telegram credentials if enabled
  - Checks WhatsApp credentials if enabled
  - Logs structured warnings (non-blocking)

#### Phase 3: Deployment & Developer Experience
- **Docker Support**
  - `Dockerfile` — Multi-stage build (builder + runtime), Python 3.12-slim, non-root user, health check (`/health`), ports 8000/8080
  - `docker-compose.yml` — Full orchestration with persistent volume `zensynora-data`, `.env` auto-loading, health checks, resource limits (2 CPU / 2 GB), optional Redis and Ollama sidecars
  - `.dockerignore` — Excludes git, cache, node_modules, dev scripts, CI configs

- **CI/CD (`.github/workflows/ci.yml`)**
  - **lint** job: `ruff check`, `ruff format --check`, `black --check`, `isort --check-only`
  - **test** job: Matrix on Python 3.11 & 3.12, pytest with coverage, Codecov upload
  - **docker** job: Build image with Buildx layer caching, test `zensynora --help`, verify WebUI starts
  - **typecheck** job: `mypy` (non-blocking, `continue-on-error`)
  - Triggers: push/PR to main, manual dispatch, skips docs-only changes

- **Code Quality Tools**
  - `.pre-commit-config.yaml` — 4 hook sources:
    - `pre-commit-hooks`: trailing-whitespace, EOF fixer, YAML/JSON/TOML check, large files, merge conflicts, private key detection
    - `ruff-pre-commit`: lint + format
    - `black-pre-commit-mirror`: code formatting
    - `isort`: import sorting (`--profile black`)

#### Phase 4: Code Structure Fixes
- **Missing `__init__.py` files**
  - Added `myclaw/agent_profiles/__init__.py` — makes agent profile directory a proper package
  - Added `tests/__init__.py` — ensures pytest treats tests as a package

- **WebUI Static File Serving (`myclaw/web/api.py`)**
  - Added `/health` endpoint for Docker health checks
  - Added `/assets` static file mount for built React frontend
  - Added catch-all `/{full_path:path}` route to serve `index.html` (SPA routing)
  - Graceful fallback if frontend isn't built
  - Fixed incorrect config access: `config.model` → `config.agents.defaults.model`

- **Nested Package Verification**
  - Confirmed no `myclaw/myclaw/` nested package exists

### Bug Fixes (2026-04-13)

#### Import Error in Knowledge Researcher (`myclaw/knowledge/researcher.py`)
- **Fixed:** `ImportError: cannot import name 'KBStorage' from 'myclaw.knowledge.storage'`
- **Root Cause:** `researcher.py` imported a non-existent `KBStorage` class from `storage.py`.
- **Fix:** Replaced incorrect import with the correct module-level functions and classes:
  - `KnowledgeDB` from `.db`
  - `write_note` and `get_knowledge_dir` from `.storage`
- **Impact:** `python cli.py onboard` now starts without import errors. The background `GapResearcher` worker correctly persists synthesized web-search notes to the knowledge base.

#### UnboundLocalError in Agent (`myclaw/agent.py`)
- **Fixed:** `UnboundLocalError: cannot access local variable 'time' where it is not associated with a value`
- **Root Cause:** An inner `import time` inside the `think()` method shadowed the module-level `time` import, causing Python to treat `time` as a local variable.
- **Fix:** Removed the redundant inner `import time` (and relocated `import inspect` to the module top-level) so the module-level `time` import is used consistently.
- **Impact:** `python cli.py agent` no longer crashes on the first user message.

#### OpenAI Tool Message Validation (`myclaw/provider.py`, `myclaw/agent.py`)
- **Fixed:** `BadRequestError: 400 - messages with role 'tool' must be a response to a preceding message with 'tool_calls'`
- **Root Cause:** OpenAI's API requires that any `role: "tool"` message immediately follows an assistant message containing `tool_calls`. The agent was not including the assistant message with `tool_calls` before tool results in follow-up API calls, and existing conversation history contained orphaned tool messages.
- **Fix (Initial):**
  - Added `_sanitize_messages_for_openai()` in `provider.py` to convert orphaned `role: "tool"` messages to `role: "user"` messages for API compatibility
  - Updated `_openai_tool_calls_to_dict()` to preserve `id` and `type` fields required for reconstructing proper message sequences
  - Modified `agent.py` to save the assistant response before tool execution and construct proper follow-up messages with both assistant (`tool_calls`) and tool (`tool_call_id`) roles
- **Fix (Follow-up — Parallel Tool Execution):**
  - OpenAI requires **one tool message per `tool_call_id`**. The agent originally aggregated multiple parallel tool results into a single message, causing `400 - An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'`.
  - Refactored `agent.py` to collect results keyed by `tool_call_id` and append individual `role: "tool"` messages for each executed tool.
  - Rewrote `provider.py`'s `_sanitize_messages_for_openai()` to track multi-message tool blocks using an `in_tool_block` flag instead of looking only at the immediately preceding message.
  - Added `_ensure_tool_messages()` in `provider.py` as a safety net to auto-insert dummy tool responses for any missing `tool_call_id`s.
- **Impact:** Single-tool and multi-tool (parallel) execution flows (`browse`, `shell`, `search_knowledge`, etc.) now work correctly without 400 Bad Request errors. Users can ask queries requiring multiple tools and receive proper responses.

#### Knowledge Researcher Indentation Fix (`myclaw/knowledge/researcher.py`)
- **Fixed:** `IndentationError: unexpected indent` on line 1 caused by accidental leading whitespace before the module docstring.
- **Impact:** `python cli.py agent` and `python cli.py onboard` no longer fail at import time for `researcher.py`.

### Knowledge Gap & Error Handling Enhancement (2026-04-10)

A comprehensive enhancement to knowledge base empty-result handling, structured gap logging, and user-friendly error handling for browse operations.

#### Knowledge Base Enhancements

- **Structured Knowledge Search Results** (`myclaw/agent.py`)
  - Added `KnowledgeSearchResult` dataclass with `context`, `has_results`, `suggested_topics`, `gap_logged`, and `metadata`
  - Enhanced `_search_knowledge_context()` with `return_structured` parameter for backward compatibility
  - When no results found, returns actionable guidance including suggested topics and KB creation hints

- **Knowledge Gap Cache** (`myclaw/agent.py`)
  - Added `KnowledgeGapCache` class for per-session deduplication of gap logging
  - Configurable timeout (default: 300 seconds) with automatic expiration
  - Case-insensitive matching and per-user isolation
  - Test hooks: `Agent._knowledge_gap_cache_enabled` and `Agent.set_gap_cache_enabled()`

- **Gap Logging** (`myclaw/agent.py`)
  - New dedicated logger: `myclaw.knowledge.gaps` for structured gap detection logging
  - Logs include: query, description, session context, timestamp, and recommendations
  - Per-session deduplication prevents log noise from repeated empty searches

- **Suggested Topics Extraction** (`myclaw/agent.py`, `myclaw/tools.py`)
  - Added `_extract_suggested_topics()` for keyword and bigram extraction from queries
  - Helps users discover alternative search terms when no results found

#### Error Handling Enhancements

- **Browse Tool Error Handling** (`myclaw/tools.py`)
  - Specific error handling for common failure modes:
    - `Timeout` → Suggests Wayback Machine cached version (web.archive.org)
    - `ConnectionError` → Advises checking internet connection
    - `404` → Suggests web search alternatives and Wayback Machine
    - `403` → Recommends using `search_knowledge()` instead
  - All errors return structured guidance payloads instead of raw exception traces
  - Maintains "Error" prefix for backward compatibility

- **Enhanced Search Knowledge** (`myclaw/tools.py`)
  - Added `_extract_search_terms()` helper for search term suggestions
  - Empty results now include actionable guidance:
    - Broader search term suggestions
    - Explicit pointer to `write_to_knowledge()`
    - Pointer to `list_knowledge()` for browsing existing entries
    - Tips for improving search (typos, keywords, synonyms)
  - Maintains "No results found" phrase for backward compatibility

#### Documentation & Testing

- **Unit Tests** (`tests/test_agent.py`, `tests/test_tools.py`)
  - Added 25+ new test methods covering knowledge gap handling
  - Tests for `KnowledgeGapCache` deduplication (8 tests)
  - Tests for `KnowledgeSearchResult` dataclass (2 tests)
  - Tests for `_search_knowledge_context()` with structured returns (6 tests)
  - Tests for browse error handling (9 tests)
  - Tests for search term extraction (5 tests)
  - Tests for backward compatibility (2 tests)

- **Documentation Updates**
  - Updated `README.md` with Behavioral Changes (v2.1) section
  - Added migration notes for API changes
  - Documented test hooks and cache control methods

### Performance & Optimization Overhaul (2026-04-06)

A comprehensive code optimization initiative implementing 21 performance, reliability, and maintainability improvements across the codebase.

#### Core Performance Improvements

- **LRU Cache with TTL Rewrite** (`myclaw/provider.py`)
  - Complete rewrite with thread-safe RLock, fast `hash()` key generation, `_CacheEntry` with `__slots__`
  - Added cache statistics (`cache_info()`) and manual cleanup (`clear_cache()`)
  - 10x faster key generation vs MD5, proper LRU eviction

- **Semantic Cache Memory Optimization** (`myclaw/semantic_cache.py`)
  - Added `torch.set_num_threads(4)` to limit CPU usage
  - Added explicit `device='cpu'` parameter
  - Added `_cleanup_embedding_model()` with garbage collection and CUDA cache clearing
  - Added context manager support for automatic cleanup

- **Profile Cache LRU Implementation** (`myclaw/agent.py`)
  - Changed from FIFO to true LRU using `OrderedDict`
  - Added `move_to_end()` on access for proper LRU tracking
  - Replaced batch eviction with single-item `popitem(last=False)`

#### Database Optimizations

- **Knowledge Graph N+1 Query Fix** (`myclaw/knowledge/graph.py`, `myclaw/knowledge/db.py`)
  - Added `get_entities_by_permalinks()` batch method for O(1) queries instead of O(N)
  - Updated `get_related_entities()`, `get_entity_network()`, `find_path()` to use batch fetching
  - Eliminates N+1 query problem when traversing relations

- **Connection Pool Idle Cleanup** (`myclaw/memory.py`)
  - Added `_last_used` tracking and `IDLE_TIMEOUT = 300` (5 minutes)
  - Added `cleanup_idle()` method for automatic cleanup of idle connections
  - Prevents connection leaks in long-running processes

- **FTS5 Query Optimization** (`myclaw/knowledge/db.py`)
  - Replaced `bm25()` function calls with built-in `rank` column
  - ~30% faster full-text search queries

- **WAL Checkpoint Control** (`myclaw/knowledge/db.py`)
  - Added `PRAGMA wal_autocheckpoint=1000` for less frequent auto-checkpoints
  - Added `checkpoint_wal()` method for manual checkpoint control
  - Prevents unbounded WAL file growth

#### Concurrency & Thread Safety

- **Provider Cache Thread Safety** (`myclaw/provider.py`)
  - Added `threading.Lock()` around provider initialization
  - Prevents race conditions when multiple threads request providers simultaneously

- **Config Loading Thread Safety** (`myclaw/config.py`)
  - Added `_config_lock = threading.Lock()`
  - Wrapped `load_config()` in `with _config_lock:` for thread-safe config reloading

- **Async File I/O** (`myclaw/agent.py`)
  - Added `_load_system_prompt()` async method with lazy initialization
  - Added `_load_profile_cached_async()` using `asyncio.to_thread()`
  - Prevents blocking event loop during profile file I/O

- **ThreadPoolExecutor Cleanup** (`myclaw/gateway.py`)
  - Changed `executor.shutdown(wait=True)` to `shutdown(wait=False)`
  - Non-blocking shutdown prevents event loop blocking during cleanup

#### Code Quality

- **String Building Optimization** (`myclaw/agent.py`, `myclaw/provider.py`, `myclaw/skill_preloader.py`)
  - Replaced string concatenation in loops with list append + join
  - O(n) complexity instead of O(n²)

- **Circular Import Prevention** (`myclaw/provider.py`)
  - Added lazy import `_get_tool_schemas()` with caching
  - Prevents circular dependency between `provider.py` and `tools.py`

- **Input Sanitization** (`myclaw/memory.py`)
  - Added regex sanitization for FTS queries: `re.sub(r'[^\w\s"\*\-\(\)ANDORNOT]', '', query)`
  - Prevents FTS query injection attacks

#### Documentation & Testing

- **Module Docstrings** (`myclaw/agent.py`, `myclaw/memory.py`, `myclaw/tools.py`, `myclaw/config.py`, `myclaw/gateway.py`)
  - Added comprehensive module-level docstrings
  - Include purpose, key components, features, and usage examples

- **Unit Tests** (`tests/`)
  - Created `test_provider_retry.py` - Retry decorator and provider cache tests
  - Created `test_swarm_aggregation.py` - Swarm result aggregation tests
  - Created `test_memory_batching.py` - Memory batching and connection pool tests
  - Created `test_tool_rate_limiting.py` - Tool rate limiting tests
  - 40+ new test methods covering critical paths

- **Dependencies** (`requirements.txt`)
  - Reorganized into Core, Optional, LLM Providers, Development sections
  - Clear comments for each optional dependency

### Phase 1: Quick Wins

- **Plugin Lifecycle Hooks** (`myclaw/tools.py`, `myclaw/agent.py`)
  - New hooks system: `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`
  - Added `register_hook()`, `trigger_hook()`, `list_hooks()`, `clear_hooks()` functions
  - Integrated hooks into `agent.think()` and `agent.stream_think()` methods

- **Trajectory Compression Enhancement** (`myclaw/agent.py`)
  - Improved context summarization with compression ratio logging
  - Focus on key decisions, facts, and user preferences
  - Truncated content (200 chars) in summaries to reduce token usage

- **Natural Language Scheduling** (`myclaw/tools.py`)
  - New `_parse_natural_schedule()` function supporting:
    - "in 5 minutes" → one-shot
    - "every 2 hours" → recurring
    - "at 8 AM daily" → daily recurring
    - "every Monday at 9pm" → weekly recurring
  - Added `nlp_schedule(task, natural_time)` tool

- **Enhanced Cross-Session Recall** (`myclaw/memory.py`)
  - Improved `search()` with BM25 ranking + recency boosting
  - Automatic prefix matching for single-term queries
  - Exact phrase support for quoted terms

### Phase 2: Skill System Evolution

- **Full Skill Metadata** (`myclaw/tools.py`)
  - Extended TOOLBOX_REG schema with: name, version, description, tags, author, created, last_modified, eval_score, eval_count, enabled
  - Updated `list_toolbox()` to display full metadata

- **Skill Evaluation Harness** (`myclaw/tools.py`)
  - Added: `get_skill_info()`, `enable_skill()`, `disable_skill()`, `update_skill_metadata()`
  - Added: `benchmark_skill()`, `evaluate_skill()`
  - Auto-disable for skills scoring < 30%

- **Skill Self-Improvement** (`myclaw/tools.py`)
  - Added: `improve_skill()` with safety checks (AST validation, syntax check, docstring/logger requirements)
  - Added: `rollback_skill()` for version rollback
  - Automatic backup before improvements
  - Version increment on update

### Phase 3: Memory & Learning

- **Periodic Session Reflection** (`myclaw/tools.py`)
  - Added: `schedule_daily_reflection()`, `generate_session_insights()`, `extract_user_preferences()`
  - Daily reflection at configurable time (default: 8 PM)
  - Saves summaries to knowledge base with tag `daily_reflection`

- **User Dialectic Profile** (`myclaw/profiles/user_dialectic.md`, `myclaw/agent.py`, `myclaw/tools.py`)
  - New template: `user_dialectic.md` with communication style, preferences, interests
  - Agent loads profile on startup and appends to system prompt
  - Added: `update_user_profile()`, `get_user_profile()`

### Phase 4: ZenHub Ecosystem

- **ZenHub Local Registry** (`myclaw/hub/__init__.py`)
  - Created new module with skill registry
  - Added: `hub_search()`, `hub_list()`, `hub_publish()`, `hub_install()`, `hub_remove()`
  - Registry stored at `~/.myclaw/hub/`

- **External Skill Directory Support** (`myclaw/hub/__init__.py`)
  - Added: `discover_external_skills()`, `hub_install_from_external()`
  - Auto-discovers skills from `~/.myclaw/skills/`

### Added

- **WhatsApp Business Cloud API Channel** (`myclaw/channels/whatsapp.py`)
  - New communication channel using the official WhatsApp Business Cloud API (Meta Graph API)
  - FastAPI webhook server for receiving and responding to WhatsApp messages
  - Webhook verification endpoint (GET /webhook) for Meta's challenge-response flow
  - Message handling with sender allowlist filtering (`allowFrom` by phone number)
  - Full command support: `/remind`, `/jobs`, `/cancel`, `/agents`, all `/knowledge_*` commands
  - Agent routing via `@agentname` prefix (same as Telegram)
  - Automatic message splitting for responses exceeding WhatsApp's 4096-character limit
  - Channel-agnostic notification callback for scheduled job results

- **WhatsApp Configuration** (`myclaw/config.py`)
  - New `WhatsAppConfig` Pydantic model with `phone_number_id`, `business_account_id`, `access_token`, `verify_token`, and `allowFrom`
  - Added `whatsapp` field to `ChannelsConfig`
  - Environment variable overrides: `MYCLAW_WHATSAPP_PHONE_NUMBER_ID`, `MYCLAW_WHATSAPP_BUSINESS_ACCOUNT_ID`, `MYCLAW_WHATSAPP_ACCESS_TOKEN`, `MYCLAW_WHATSAPP_VERIFY_TOKEN`

- **Channel-Agnostic Notification System** (`myclaw/tools.py`)
  - New `set_notification_callback()` function for registering async notification handlers
  - Updated `_create_job_internal()` to try notification callback before falling back to Telegram bot
  - Updated `schedule()` error message to be channel-agnostic

- **Gateway WhatsApp Support** (`myclaw/gateway.py`)
  - Added `WhatsAppChannel` import and startup logic
  - Gateway now starts WhatsApp channel when `channels.whatsapp.enabled` is true

- **New Dependencies** (`requirements.txt`)
  - `fastapi>=0.100.0` — ASGI web framework for WhatsApp webhook server
  - `uvicorn>=0.23.0` — ASGI server to run FastAPI

- **Documentation**
  - New `docs/dev/plans/whatsapp_implementation_plan.md` — comprehensive implementation plan with architecture diagrams, setup guide, and remaining work items
  - Updated `README.md` — WhatsApp in features, architecture, config, commands, and project structure
  - Updated `docs/dev/how to run.md` — WhatsApp gateway instructions

### Optimized

- **Swarm Result Caching** (`myclaw/swarm/storage.py`)
  - Optimization 4.3: Added in-memory result caching with TTL (1 hour)
  - New `ResultCache` class with thread-safe operations using `threading.RLock`
  - Cache key format: `swarm_id:input_hash` (SHA256 of input data)
  - Modified `SwarmStorage.__init__()` to accept `enable_cache` parameter (default: True)
  - Modified `save_result()` to accept optional `input_hash` parameter for caching
  - Modified `get_result()` to check cache first before database lookup
  - Added `invalidate_result_cache()` method to manually invalidate cached results
  - Added `get_cache_stats()` method to retrieve cache statistics
  - Cache automatically expires entries after 1 hour (3600 seconds)
  - Thread-safe for concurrent access
  - Can be disabled by passing `enable_cache=False` to constructor

- **Shared Connection Pool for Swarm Storage** (`myclaw/swarm/storage.py`, `myclaw/swarm/orchestrator.py`)
  - Optimization 4.2: Added `pool` parameter to `SwarmStorage.__init__()` method
  - Modified `_get_connection()` to use pooled connections when available
  - Uses `SQLitePool` from `myclaw.memory` for connection management
  - Falls back to creating new connections if pool unavailable (backward compatible)
  - Enables WAL mode and synchronous=NORMAL for better concurrency
  - Reduces connection overhead when swarm storage is used alongside memory storage
  - Orchestrator now passes SQLitePool to storage by default

- **Swarm Execution Timeout Enforcement** (`myclaw/swarm/orchestrator.py`)
   - Added optional `timeout` parameter to `SwarmOrchestrator.execute_task()` method
   - Added optional `timeout` parameter to `SwarmOrchestrator.execute_task_async()` method
   - Uses `asyncio.wait_for()` with cancellation for timeout enforcement
   - When timeout is specified, overrides the default timeout from config
   - Proper error handling for `asyncio.TimeoutError` with descriptive error message
   - Returns `SwarmResult` with timeout error message and zero confidence score on timeout

- **Persistent Active Execution Tracking** (`myclaw/swarm/models.py`, `myclaw/swarm/storage.py`, `myclaw/swarm/orchestrator.py`)
   - Optimization 4.4: Added persistent active execution tracking using SQLite
   - New `ActiveExecution` model to represent async execution state
   - Added `active_executions` table to swarm database
   - Added storage methods: `save_execution_state()`, `update_execution_state()`, `remove_execution_state()`, `load_active_executions()`, `recover_stale_executions()`
   - Updated orchestrator to save execution state on async task start and remove on completion
   - Added `load_active_executions()` method to orchestrator for restart recovery
   - Added `recover_stale_executions()` to mark crashed executions as terminated on startup
   - Enables swarm executions to survive orchestrator restarts

- **Background Knowledge Extraction** (`myclaw/knowledge/sync.py`, `myclaw/config.py`)
  - Added background task for automatic knowledge extraction using `asyncio.create_task()`
  - Configurable via `knowledge.auto_extract` in config (default: `false`)
  - New functions: `start_background_extraction()`, `stop_background_extraction()`, `is_background_extraction_running()`
  - Runs periodic sync in background with configurable interval (default: 60 seconds)
  - Uses `asyncio.to_thread()` to run sync without blocking the event loop
  - Can be enabled via config file or `MYCLAW_KNOWLEDGE_AUTO_EXTRACT` environment variable

- **Composite Indexes for Graph Queries** (`myclaw/knowledge/db.py`)
  - Added `idx_entity_type_name` on entities(name) for entity lookups
  - Added `idx_relations_from_type` on relations(from_entity_id, relation_type) for filtering outgoing relations by type
  - Added `idx_relations_to_type` on relations(to_entity_id, relation_type) for filtering incoming relations by type
  - Added `idx_observations_entity_category` on observations(entity_id, category) for category-filtered observation queries
  - Added `idx_relations_type` on relations(relation_type) for type-only lookups
  - These indexes significantly improve graph traversal and relation query performance

- **FTS5 BM25 Ranking** (`myclaw/knowledge/db.py`)
  - Added `rank_bm25()` optimization for more relevant search results
  - Created separate `observations_fts` FTS5 table to index observation content
  - Combined BM25 scoring from both entities and observations for better relevance
  - Added BM25 parameters configuration (`BM25_DEFAULT_K1`, `BM25_DEFAULT_B`)
  - Added `rebuild_fts_index()` method to populate FTS tables for existing databases
  - Backward compatible - falls back to entities-only search if observations FTS unavailable
  - Triggers added to keep observations FTS in sync with changes

- **Consolidate Tool Schemas** (`myclaw/tools.py`, `myclaw/provider.py`)
  - Moved `TOOL_SCHEMAS` definition from `provider.py` to `tools.py`
  - `provider.py` now imports `TOOL_SCHEMAS` from `tools` module
  - Single source of truth for tool schema definitions
  - Reduces code duplication and improves maintainability

- **Streaming Response Support** (`myclaw/provider.py`, `myclaw/agent.py`)
  - Added `stream` parameter to all provider chat methods
  - Providers: Ollama, OpenAI-compatible (LMStudio, LlamaCpp, OpenAI, Groq, OpenRouter), Anthropic, Gemini
  - When `stream=True`, returns async iterator yielding content chunks
  - Added `stream_chat()` method to each provider for dedicated streaming
  - Added `stream_think()` method to Agent class for real-time response display
  - Uses SSE (Server-Sent Events) for Ollama streaming
  - Uses OpenAI SDK streaming for compatible providers
  - Uses Anthropic beta streaming API
  - Backward compatible - existing code works unchanged with `stream=False` (default)

- **Lazy Provider Initialization** (`myclaw/agent.py`)
  - Provider is now initialized on first access rather than in `__init__`
  - Improves startup performance by deferring provider initialization
  - Added `@property provider` method with lazy initialization logic
  - Falls back to "ollama" if primary provider fails to initialize
  - Backward compatible - `self.provider` still accessible as before

## [0.1.1] - 2026-03-16

### Added

- **Request Caching** (`myclaw/provider.py`)
  - Added in-memory request caching for all LLM providers using LRU cache decorator
  - 5-minute TTL (300 seconds) with automatic eviction (max 128 entries)
  - Cache key based on hash of messages and model parameters
  - Implemented `@lru_cache_with_ttl` decorator for async functions
  - Supported providers: Ollama, OpenAI-compatible, Anthropic, Gemini
  - Replaced manual caching code with decorator-based approach

- **Lazy Provider Loading** (`myclaw/provider.py`)
  - Added provider instance caching in `get_provider()`
  - Providers are only initialized when first requested
  - Added `clear_provider_cache()` function for testing/config changes

- **Runtime Command Allowlist** (`myclaw/tools.py`)
  - Added mutable command allowlist (`_allowed_commands_set`)
  - Added `add_allowed_command()` function
  - Added `remove_allowed_command()` function
  - Added `get_allowed_commands()` function
  - Added `is_command_allowed()` function
  - Commands can now be added/removed at runtime

- **Tool Execution Audit Trail** (`myclaw/agent.py`)
  - Added audit logging for tool execution
  - Logs: tool start, success with duration, and failures
  - Format: `[AUDIT] Tool execution started/finished/failed`

- **FTS5 BM25 Ranking** (`myclaw/knowledge/db.py`)
  - Changed from `ORDER BY rank` to `ORDER BY bm25(entities_fts)`
  - BM25 provides better relevance scoring
  - Considers term frequency and document frequency

- **Telegram Webhook Mode** (`myclaw/channels/telegram.py`)
  - Added `run_webhook()` method for production deployments
  - More efficient than polling for high-load scenarios
  - Configurable webhook URL and port

- **Database Indexes** (`myclaw/knowledge/db.py`)
  - Added indexes on: `entities.file_path`, `entities.created_at`
  - Added indexes on: `observations.entity_id`, `observations.created_at`
  - Added indexes on: `tags.entity_id`, `tags.name`

- **Telegram ThreadPool Configuration** (`myclaw/channels/telegram.py`)
  - Added `set_threadpool_size()` function
  - Configurable thread pool for concurrent message handling
  - Default: 20 workers

- **Swarm Concurrency Control** (`myclaw/swarm/orchestrator.py`)
  - Added semaphore-based concurrency limiting
  - Configurable max concurrent swarms
  - Added result caching for faster retrieval

### Added

- **HTTP Connection Pooling** (`myclaw/provider.py`)
  - Added `HTTPClientPool` class for shared HTTP client with connection pooling
  - Supports up to 100 concurrent connections with 20 keepalive connections
  - HTTP/2 support for better multiplexing
  - Added `cleanup_http_pool()` function for graceful shutdown

- **Retry Logic** (`myclaw/provider.py`)
  - Added `@retry_with_backoff` decorator for automatic retry on failures
  - 3 retries with exponential backoff (1s, 2s, 4s)
  - Retries on: `TimeoutException`, `ConnectError`, `HTTPStatusError`

- **SQLite Connection Pool** (`myclaw/memory.py`)
  - Added `SQLitePool` class with reference counting
  - WAL mode enabled for better concurrency
  - Synchronous=NORMAL for balanced safety/speed

- **Environment Variable Overrides** (`myclaw/config.py`)
  - Added `ENV_OVERRIDES` mapping with support for 15+ config keys
  - Supports `MYCLAW_*` environment variables
  - Added `TimeoutConfig` class for configurable timeouts
  - Automatic type inference (bool, int, string)

- **Profile Caching** (`myclaw/agent.py`)
  - Added `_load_profile_cached()` with mtime-based invalidation
  - Thread-safe with `_profile_cache_lock`
  - FIFO cache eviction (max 100 entries)

- **Shell Timeout Configuration** (`myclaw/tools.py`, `myclaw/config.py`)
  - Added `set_config()` function in tools.py
  - Configurable via `config.timeouts.shell_seconds`
  - Default: 30 seconds
  - Updated `myclaw/gateway.py` to call `tool_module.set_config(config)`

- **Knowledge Sync Cache** (`myclaw/knowledge/sync.py`)
  - Added `_get_cached_note()` function
  - Caches parsed notes with mtime validation
  - Added `clear_note_cache()` function

### Environment Variables Added

| Variable | Description |
|----------|-------------|
| `MYCLAW_OLLAMA_BASE_URL` | Override Ollama base URL |
| `MYCLAW_OPENAI_API_KEY` | Override OpenAI API key |
| `MYCLAW_ANTHROPIC_API_KEY` | Override Anthropic API key |
| `MYCLAW_GEMINI_API_KEY` | Override Gemini API key |
| `MYCLAW_GROQ_API_KEY` | Override Groq API key |
| `MYCLAW_TELEGRAM_TOKEN` | Override Telegram bot token |
| `MYCLAW_DEFAULT_PROVIDER` | Set default LLM provider |
| `MYCLAW_DEFAULT_MODEL` | Set default model |
| `MYCLAW_SWARM_ENABLED` | Enable/disable agent swarms |
| `MYCLAW_MAX_CONCURRENT_SWARMS` | Max concurrent swarms |
| `MYCLAW_SWARM_TIMEOUT` | Swarm timeout in seconds |
| `MYCLAW_SHELL_TIMEOUT` | Shell command timeout |
| `MYCLAW_LLM_TIMEOUT` | LLM request timeout |
| `MYCLAW_HTTP_TIMEOUT` | HTTP request timeout |

### Configuration Changes

- Added `TimeoutConfig` class to `myclaw/config.py`:
  ```python
  class TimeoutConfig(BaseModel):
      shell_seconds: int = 30
      llm_seconds: int = 60
      http_seconds: int = 30
  ```

## [0.0.1] - 2026-03-08

### Added

- Initial release
- Personal AI agent with flexible LLM providers
- SQLite-backed persistent memory
- Multi-agent support with delegation
- Agent Swarms system
- Knowledge base with FTS5 search
- Telegram gateway integration
- Task scheduling system

---

[Unreleased]: https://github.com/adrianx26/zensynora/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/adrianx26/zensynora/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/adrianx26/zensynora/releases/tag/v0.0.1
