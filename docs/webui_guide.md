# Web UI Guide — chat, models, jobs, backups

> **Applies to:** v0.7.5 · **Added:** 2026-10-13 · **v0.7.3:** Channels page,
> real model tests · **v0.7.4:** Updates page, Restart button · **v0.7.5:**
> Skills, agents that create and delegate, medic for failed jobs, token speed
> · **Unreleased:** Telegram bot inside the Web UI (Settings → Channels)

Start the Web UI with `zensynora webui` and open <http://localhost:8000>
(`--host 127.0.0.1` keeps it on this computer only). This guide covers the
pages added in 0.7.1. Settings, Costs, Tools and Doctor are described in the
[README](../README.md) and the [Medic & Doctor guide](medic_agent_guide.md).

![Chat with several agents](images/webui-chat.png)

## Chat: channels and several agents

| Where | What it is |
|---|---|
| **Channels** (`#general`, `＋` for more) | Group rooms. Pick the members when you create one; change them with ⚙ in the channel bar. |
| **Agents** (sidebar) | Click an agent for a private conversation (direct message) with it alone. |

**Managing channels** (v0.7.3): the **Channels** page (⚙ next to *Channels*
in the sidebar) creates channels with a name, a **topic** (the agents are told
it is the channel's purpose), members and reply mode, and renames, edits,
clears or deletes them. It shows message counts and last activity, and lists
the direct-message conversations too.

**Who answers.** In a channel, `@name` addresses an agent (type `@` for a
list; an agent that is not a member yet is added), `@all` asks every
member. Without a mention the channel's *reply mode* decides:

| Reply mode | Behaviour |
|---|---|
| Lead answers | The first member answers; others only when mentioned (default) |
| Everyone, in turn | Each member answers and sees the earlier answers — good for review chains |
| Everyone, at once | All members answer in parallel — fastest, independent opinions |

**Agent-to-agent talk.** When an agent hands work to another one (the
`delegate` tool or a swarm), uses a tool, or a job posts into the channel,
you see it as a small card in the conversation — *@default asked @writer*,
*@writer answered @default · 1.2 s* — with the task and the answer one click
away. The **Activity** panel on the right lists the same events for the
channel; *inline activity* hides them from the conversation.

**Agents as a team** (v0.7.5). Every agent knows the other agents and what
they do, and hands work to them with `delegate` when another one fits better —
you see the hand-over as a card. An agent can also **create a new agent**
(`create_agent`) when a task needs a specialist nobody is; it appears in the
sidebar at once (✨ card in the channel) and can be changed or removed in
Settings → Agents. A task is handed over at most twice, so agents cannot pass
it back and forth forever. Switch creation off with
`agents.allow_agent_creation` (Settings → Features).

**Deleted agents.** When you rename an agent in Settings, its channels,
direct messages and jobs move to the new name. An agent you delete is removed
from its channels (the channel says so).

**Message actions.** Point at a message (on a phone: tap **…**) for a small
bar with the time (hover it for the full date) and these actions:

| Action | Your messages | Agent answers |
|---|---|---|
| **Copy** | Copies the text | Copies the text; each ``` code block also has its own **Copy code** button |
| **Edit** | Edit in place, **Save & send** (Enter; Esc cancels): everything after it in the channel is removed and the agents answer the edited message again (same rules, `@mentions` work) | Correct the text in place; nothing is run again |
| **Delete** | *Delete this message*, or *Delete this message and the answers to it* (up to your next message) | Deletes the answer (asks first) |
| **Regenerate** | — | The same agent writes this answer again, in the same place |

Edited messages show *(edited)*. Changes and deletions appear in every open
browser at once. While the agents are answering these actions wait: press ■
or let them finish. **↑** in the empty input edits your last message.
**⤓ MD** in the channel bar copies or downloads the conversation as
Markdown (`GET /api/channels/{id}/export.md`).

After an edit, delete or regenerate, each agent's memory of the channel is
rebuilt from the messages you see (your messages and its own answers), so it
never answers an edited question with the old one in mind. Details of earlier
tool calls are not kept in that memory.

**Good to know.**
* Under each answer: model, time and **⚡ tokens per second** (hover for the
  token count and the time to the first token; counts are estimates).
* Answers stream; ■ stops the agents. Messages you send meanwhile wait their turn.
* Enter sends, Shift+Enter starts a new line.
* Every agent keeps its own memory per channel; the knowledge base is shared.
* History lives in `~/.myclaw/chat.db` and is included in backups (*Conversations*).
* With two-factor authentication on, the chat asks for the code first.

## Models: switch, rotate, add providers

![Models page](images/webui-models.png)

* **Default model** — pick a provider and a model (the list comes from the
  server; ↻ refreshes) and click *Make default*. The previous default is
  remembered in the rotation list, so switching back is one click (★).
* **Rotation** — extra models used together with the default one:
  * *Fallback on errors*: the default answers; if it fails (rate limit,
    server down, model missing) the next model in the list answers.
  * *Round-robin*: requests are spread over all models.
  * *Rotate now* makes the next model the default.
  Rotation applies to the default agent and to agents that use the default
  model; agents with their own model keep it. A model that was just refused,
  unknown or rate-limited is skipped for a while.
* **Test / Test all** (v0.7.3) sends a tiny real request. A model can be
  listed and still refuse to work with ZenSynora (OpenRouter answers some
  free models with "only available on agentic harnesses"), need credits or not
  be loaded — test before you rely on it.
* **Your providers** — *Add provider* for any OpenAI-compatible server
  (local: vLLM, LocalAI, Jan, KoboldCpp, text-generation-webui; online:
  DeepSeek, Mistral, Cerebras, Together, GitHub Models, Hugging Face, NVIDIA
  NIM, Gemini, xAI, Fireworks — several have free tiers) or another Ollama
  server. Presets fill the URL; add the key, *Test connection*, save. The new
  provider then appears everywhere a provider can be chosen. Keys are stored
  in `~/.myclaw/config.json` and never sent back to the browser.

### Several keys for one provider

A provider can have more than one API key — for example two free OpenRouter
accounts, or a work and a personal key.

* **Add keys.** Settings → Providers shows a **Keys** list under each
  provider (for your own providers: Models → Your providers → **Keys**).
  *+ Add key*, give it a name and paste the key. The provider's normal API
  key is the **Default key**.
* **Per key:** *Test* checks the connection with that key and lists the
  models it can use; *Edit* renames it or replaces the secret (leave the key
  field empty to keep it); *Delete* removes it. Each key shows which agents
  use it; a key in use cannot be deleted — pick another key for those agents
  first.
* **Pick a key.** Settings → Agents: *Provider*, then *Key*, then *Model*
  (↻ lists the models of that provider and key). The Models page has the same
  *Key* picker for the default model and for rotation entries. *Default key*
  means the provider's normal key; an agent that has no provider of its own
  also uses the default agent's key.
* **Use the other keys when one is limited.** Turn on *When a key hits a rate
  limit or runs out of quota, try the other keys* (shown once a provider has
  two keys). On a rate limit, missing credits or a refused key, the same model
  is tried with the provider's other keys before the next model in the
  rotation. A key that just failed is skipped for a while (per provider, model
  and key). Off by default.
* If an agent's key was deleted (for example by editing the config file), the
  agent uses the default key; the log and the Doctor warn about it.

In `config.json`:

```json
"providers": {"openrouter": {
  "api_key": "sk-or-main",
  "keys": [{"id": "work", "label": "Work account", "api_key": "sk-or-work"}],
  "rotate_keys": true}},
"agents": {"named": [{"name": "Coder", "provider": "openrouter", "model": "…", "key": "work"}]}
```

Key ids use lower-case letters, digits, `-` and `_`; `default` is reserved.

**Writing agent prompts.** In Settings → Agents, give a new agent a name and a
short description and click **✨ Generate prompt**: the default model writes
a system prompt (role, how to work, tools to prefer, limits). Review it and
save. If the default model is refused or busy, the rotation models are tried
in turn (v0.7.4); the message under the button names the model that wrote it.

![Generate prompt](images/webui-generate-prompt.png)

## Jobs: scheduled and on-demand tasks

![Jobs page](images/webui-jobs.png)

A job is an instruction an agent carries out on a schedule or when you click
**Run**. Scheduled jobs run while `zensynora webui` is running.

| Field | Meaning |
|---|---|
| Agent | Who does the work |
| Schedule | Manual, once, every N minutes, daily at HH:MM, weekly on chosen days |
| Post results in | A channel (the run and its result appear there as activity) or nowhere |
| Success criteria | What a good result looks like — used for scoring |
| Score every run | The default model scores each run 0–10 against the criteria |
| Retries / timeout | Retry failed runs; stop runs that take too long |
| Clean memory | Each run starts without the previous runs' conversation |

**When a run fails** (v0.7.5): click **🩺 Ask the medic** on the run. The
medic says what went wrong, explains it, and suggests changes to the job
(shown as *now → suggested*) and anything only you can do (start a model
server, add credits). **Apply fix & run again** changes the job and runs it;
**Ask the medic to fix the code** is offered when ZenSynora itself is at fault —
the medic reads the code and files a proposal you approve on the Doctor page.
Common failures are recognised even when no model answers. In the job's
settings, *When a run fails* can do this automatically (diagnose, or diagnose,
fix the job and run once more). **Tool steps** sets how many extra tool rounds
(search, read, retry…) the agent may use per run — raise it for research jobs.

**Monitor** in the table: running state, last and next run, success rate,
average score. **Runs** shows each run's output, model, duration and
evaluation (*Evaluate* scores a run on demand). **Benchmark** runs the same
job on up to 8 models at once and compares time and score — a quick way to
choose a model for a task. Templates: morning briefing, weekly knowledge
clean-up, medic self-review.

## Knowledge base

All agents share one knowledge base (notes in `~/.myclaw/knowledge/default`,
Markdown files you can also edit yourself). Before answering, an agent
searches it automatically; agents can also use these tools themselves:

| Tool | What it does |
|---|---|
| `search_knowledge` | Full-text search over titles, note text, tags and observations |
| `read_knowledge` / `list_knowledge` | Read one note / list the notes |
| `write_to_knowledge` | Save a note (title, text, tags, observations, relations) |
| `get_knowledge_context` / `get_related_knowledge` | Follow relations between notes (knowledge graph) |
| `list_knowledge_tags` / `sync_knowledge_base` | Tags; re-read files you edited by hand |
| `record_lesson` | The medic's lessons (note `medic-lessons`) |

With *auto extract* on (Settings → Features → `knowledge.auto_extract`),
useful facts from conversations and tool results are saved by themselves. The
**knowledge-keeper** skill teaches an agent when to save and how. Allowed
Telegram users use the same knowledge base as the Web UI (switch it off in
Settings → Channels to give each Telegram user their own); WhatsApp users each
keep their own knowledge base.

**MCP memory servers.** Any MCP server (a memo/notes server, a database, a
file system …) can be added in `~/.myclaw/config.json`:

```json
"mcp": {"servers": {"memo": {"command": "npx", "args": ["-y", "<memo-mcp-package>"]}}}
```

Its tools appear for all agents as `mcp_<server>_<tool>` when the Web UI (or
`zensynora agent`) starts. In the other direction, `zensynora mcp-server`
offers ZenSynora's tools — knowledge base included — to other MCP clients.

## Telegram bot (Settings → Channels)

*Settings → Channels* is about messaging apps — not the chat channels above.
With the Telegram bot switched on, `zensynora webui` runs the bot too, so you
can talk to your agents from your phone. No separate `zensynora gateway` is
needed. The bot restarts by itself when you save a change to a Telegram
setting. WhatsApp still runs in `zensynora gateway` — restart the gateway
after changing WhatsApp settings.

| Setting or button | What it does |
|---|---|
| **Telegram bot enabled** · **Telegram bot token** | Switch the bot on; paste the token from @BotFather |
| **Allowed Telegram users** | Your numeric user id — send `/whoami` to the bot to see it — or `@username`. Nobody else can use the bot. |
| **Run the bot inside the Web UI** | On (default). Turn off if you run the bot with `zensynora gateway` instead. |
| **Telegram uses the same knowledge base as the Web UI** | On (default): notes are shared; each Telegram user keeps their own conversation memory |
| **Test bot** | Checks the token with Telegram — the token you just typed, or else the saved one. Success: *Token works — bot @name (Name)*. |
| **Restart bot** | Starts the bot again. Not available when the bot is turned off, runs in `zensynora gateway`, or has no saved token. |
| **Send test message** | The running bot writes a short message to every numeric id in *Allowed Telegram users* — proves the whole path works. If it says the bot cannot write first, open the bot in Telegram and press **Start**. |

The **Telegram bot** panel shows the **Status**:

| Status | Meaning |
|---|---|
| Running as @name | All good |
| Starting… | Wait a few seconds |
| Stopped · Turned off | The bot is not running / *Telegram bot enabled* is off |
| No bot token yet | Paste the token from @BotFather and Save |
| Runs in zensynora gateway, not in the Web UI | *Run the bot inside the Web UI* is off |
| Another ZenSynora (zensynora gateway?) is already running this bot … | Conflict: Telegram allows only one copy. Stop the other one, or turn off *Run the bot inside the Web UI*. |
| Error: … | Read the message; click **Test bot** |

While nobody is allowed, the panel warns: *Nobody can use the bot yet: add your
user id to Allowed Telegram users below.* The sidebar health has a
**Telegram** row too (running @name / stopped / conflict / error). Set-up step by step, commands and
troubleshooting: [Telegram guide](telegram_guide.md).

## Skills: instructions agents load when needed

**Skills** in the sidebar. A skill is a Markdown file of instructions for one
kind of task — how to research the web and cite sources, how to write a
PowerShell script, how to review code.

* **Give a skill to an agent**: click the agent's chip under the skill
  (bright = has it). The same chips are in Settings → Agents.
* The agent's prompt lists its skills by name and description; when a task
  matches, it reads the full text with the `use_skill` tool. *Always include
  the full instructions* puts the whole text in the prompt instead — better
  for small local models that rarely call tools, but it costs tokens.
* **+ New skill** writes one; **Import…** adds a `SKILL.md` made elsewhere
  (the same format other agent tools use: a `name:` and `description:` header,
  then Markdown). Built-in skills are read-only: **Copy to change** makes your
  own version (deleting it brings the built-in one back).

Your skills are files in `~/.myclaw/skills/<name>/SKILL.md` and are included
in backups (*Jobs, own tools & skills*). `zensynora skills list` shows all
skills and which agents have them.

## Backups: settings, API keys, knowledge

![Backups page](images/webui-backups.png)

* **Back up now** — choose what to include: *Settings & API keys* (always;
  includes the config encryption key so the backup can be restored on a new
  machine), *Knowledge base*, *Conversations & chat channels*, *Jobs, own
  tools & skills*. Add a note if you like.
* **Automatic backups** — every N hours while the Web UI runs; keeps the
  last N automatic ones (manual backups are never deleted automatically).
* **Restore** — choose which parts to restore (click the part chips);
  your current files are backed up first, so a restore can be undone.
  **Check** verifies every file against its checksum; ⬇ downloads;
  **Import…** adds a backup file made elsewhere.

Backups are `.tar.gz` files in `~/.myclaw-backups` (change it in Settings or
with `MYCLAW_BACKUP_DIR`). They contain your API keys — keep them private.
The same works from a terminal:

```bash
zensynora backup create                 # parts chosen in Settings
zensynora backup create -p settings     # only settings + API keys
zensynora backup list
zensynora backup restore NAME [-p knowledge]
```

Updates make their own backups automatically — see [UPDATING.md](UPDATING.md).

## Updates: check, update, roll back, restart

Open **Updates** in the sidebar (or click the version at the bottom).

| Part | What it does |
|---|---|
| Version | The version this server runs, the installed one and the latest on GitHub. **↻ Check for updates** asks GitHub again. |
| Update now | Runs the safe updater: backups of your data and the program folder, side-by-side install, checks, switch, automatic rollback on failure. Shows the step (*step 7 of 14 …*), a progress bar and the log. It keeps running if you close the page. |
| Roll back | Back to the previous installed version. |
| Branch or tag / Reinstall | Install a branch or tag instead of `main`; reinstall the same version to repair an install. |
| Restart ZenSynora | After an update or rollback the server still runs the old code. This restarts it with the installed version; the page reloads when it is back (a few seconds). The same button appears in the yellow banner on every page. |
| History | Recent updates and rollbacks, and the versions kept for rollback. |

Works for standard installs (`~/.local/share/zensynora`); a git checkout
shows why it cannot update itself. Restart works when the server was started
with `zensynora webui`. Details: [UPDATING.md](UPDATING.md).

## Doctor: report, errors and download

The Doctor page has two tabs. **Checks** is the health check (the same as
`zensynora doctor`). **Report** shows:

* summary cards — checks OK / warnings / failures, errors and warnings in the
  last 24 h, restarts (marked *crash loop* when the service keeps
  restarting), failed jobs;
* **Errors** — warnings and errors from the log, grouped (messages that
  differ only in numbers count as one). Filter by period (1 h, 24 h, 7 days),
  level and text; click a group to see its log lines and traceback, and
  **Copy** them;
* **View report** — the whole diagnostics report in the page;
* **Download** — **Markdown**, **JSON** or **ZIP** (report + the last log
  lines). Keys and tokens are never included, so the file is safe to attach
  to a bug report.

## Doctor: change proposals

![Change proposals](images/webui-proposals.png)

Below the health checks, **Change proposals** lists code changes that agents
proposed, with the reason and the exact diff. *Approve & install* installs
the change through the safe updater (backup, side-by-side build, checks,
automatic rollback); *Reject* drops it. **Run self-review** asks the medic to
look at recent errors and failed jobs and propose a fix; **What the medic
learned** lists the lessons it saved. Details:
[Medic & Doctor guide](medic_agent_guide.md#6-self-update-proposals).
