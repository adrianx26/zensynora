# Web UI Guide — chat, models, jobs, backups

> **Applies to:** v0.7.1 · **Added:** 2026-10-13

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

**Good to know.**
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
  model; agents with their own model keep it.
* **Your providers** — *Add provider* for any OpenAI-compatible server
  (local: vLLM, LocalAI, Jan, KoboldCpp, text-generation-webui; online:
  DeepSeek, Mistral, Cerebras, Together, GitHub Models, Hugging Face, NVIDIA
  NIM, Gemini, xAI, Fireworks — several have free tiers) or another Ollama
  server. Presets fill the URL; add the key, *Test connection*, save. The new
  provider then appears everywhere a provider can be chosen. Keys are stored
  in `~/.myclaw/config.json` and never sent back to the browser.

**Writing agent prompts.** In Settings → Agents, give a new agent a name and a
short description and click **✨ Generate prompt**: the default model writes
a system prompt (role, how to work, tools to prefer, limits). Review it and
save.

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

**Monitor** in the table: running state, last and next run, success rate,
average score. **Runs** shows each run's output, model, duration and
evaluation (*Evaluate* scores a run on demand). **Benchmark** runs the same
job on up to 8 models at once and compares time and score — a quick way to
choose a model for a task. Templates: morning briefing, weekly knowledge
clean-up, medic self-review.

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

## Doctor: change proposals

![Change proposals](images/webui-proposals.png)

Below the health checks, **Change proposals** lists code changes that agents
proposed, with the reason and the exact diff. *Approve & install* installs
the change through the safe updater (backup, side-by-side build, checks,
automatic rollback); *Reject* drops it. **Run self-review** asks the medic to
look at recent errors and failed jobs and propose a fix; **What the medic
learned** lists the lessons it saved. Details:
[Medic & Doctor guide](medic_agent_guide.md#6-self-update-proposals).
