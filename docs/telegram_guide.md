# Telegram Guide — chat with your agents from your phone

> **Applies to:** v0.7.5 + Unreleased changes · **Added:** 2026-10-16 — the
> Telegram bot was reviewed and fixed, and it now runs inside the Web UI.

The Telegram bot lets you talk to your ZenSynora agents from the Telegram app
on your phone or computer. It uses the same agents, models and knowledge base
as the Web UI. Your bot is private: only the people you allow can use it.

What it can do:

* answer your messages (the default agent, or any agent with `@name`);
* run reminders and repeating tasks (`/remind`);
* search, read and save notes in the knowledge base (`/knowledge_…`);
* answer several people at the same time, while keeping each chat in order.

Settings, buttons and status are in the Web UI under **Settings → Channels**
(see the [Web UI guide](webui_guide.md#telegram-bot-settings--channels)).

## Set it up

1. **Create a bot.** In Telegram, open **@BotFather**, send `/newbot`, choose
   a name and a username (it must end in `bot`). BotFather replies with a
   **token** — a long text like `123456:ABC-DEF…`. Copy it. Keep it secret:
   anyone with the token can run your bot.
2. **Add the token.** Start the Web UI (`zensynora webui`) and open
   **Settings → Channels**. Paste the token, turn on **Telegram bot
   enabled**, click **Save**, then click **Test bot**. It should say
   *Token works — bot @YourBot (Name)*.
3. **Find your user id.** Open your bot in Telegram (search for its username)
   and send `/whoami`. It answers with a number — your Telegram user id.
   Copy it.
4. **Allow yourself.** Back in Settings → Channels, add the number to
   **Allowed Telegram users** and click **Save**. The bot restarts by itself
   (or click **Restart bot**).
6. Click **Send test message**: the bot writes to every number in *Allowed
   Telegram users*. Telegram does not let a bot write first, so open the bot
   and press **Start** once before that.
5. **Chat.** Write to your bot. The default agent answers.

The **Telegram bot** panel in Settings → Channels shows *Status: Running as
@YourBot* when everything works (the sidebar health shows *Telegram: running
@YourBot*). Until someone is allowed, it warns: *Nobody can use the bot yet:
add your user id to Allowed Telegram users below.*

**Without the Web UI.** You can run the bot on its own with
`zensynora gateway` instead. If you do, turn off **Run the bot inside the Web
UI** in Settings → Channels — otherwise two copies of the bot run and
Telegram reports a *conflict* (see [Troubleshooting](#troubleshooting)).

## Commands

Type `/` in the chat to see the list — the bot registers its command menu
with Telegram by itself.

| Command | What it does |
|---|---|
| `/start` | Welcome message and the command list |
| `/help` | The command list |
| `/whoami` | Shows your Telegram user id and whether you are allowed |
| `/agents` | Lists the agents you can address with `@name` |
| `/remind <seconds> <text>` | Runs `<text>` once, after that many seconds |
| `/remind every <seconds> <text>` | Runs `<text>` again and again, every that many seconds |
| `/jobs` | Lists the reminders, with their ids |
| `/cancel <id>` | Cancels a reminder (the id comes from `/jobs`) |
| `/knowledge_search <words>` | Searches the knowledge base |
| `/knowledge_list` | Lists the notes |
| `/knowledge_read <permalink>` | Shows one note (the permalink comes from `/knowledge_list`) |
| `/knowledge_write <title> \| <text>` | Saves a note, for example `/knowledge_write Wi-Fi \| The router is in the hall` |
| `/knowledge_sync` | Re-reads the note files (after you edited them by hand) |
| `/knowledge_tags` | Lists the tags |

`/start`, `/help` and `/whoami` answer everyone (people who are not allowed
get only their own id and how to be allowed). All other commands, and normal
messages, work only for allowed users.

## Agents and @mentions

* A normal message goes to the **default** agent.
* Start a message with `@name` to ask another agent:
  `@writer a short poem about rain`. Upper or lower case does not matter.
  Answers from other agents start with their name (`@writer: …`), so you
  know who replied. `@writer` alone, with nothing after it, gets the question
  *What should @writer do?* — write the task after the name.
* `/agents` lists the agents. Agents created later — in Settings → Agents, or
  by another agent with `create_agent` — can be used right away, without a
  restart.
* An `@word` that is not an agent stays part of the message for the default
  agent.

While the agent works, Telegram shows **typing…**. Long answers come as
several messages (Telegram allows at most 4096 characters per message). If
something goes wrong, you get a short explanation — the same wording as in the
Web UI, for example that the model is rate-limited or the model server cannot
be reached. Technical details go only to the log
(`~/.myclaw/logs/zensynora.log`), never to the chat.

**Files, photos, voice messages.** Send a document (PDF, Word, Excel, text,
code…), a photo, a voice message, audio or video, with or without a caption.
The bot saves it in the agent workspace under
`inbox/telegram/<date>/<file name>` and tells the agent where it is. For
documents the agent also gets the start of the text and can read all of it
(`read_file`) or add it to the knowledge base (`assimilate`) — just ask, e.g.
*"save this in the knowledge base"*. Start the caption with `@AgentName` to give
the file to that agent. Agents cannot see images or hear audio yet (no OCR or
transcription); they get the file's details. Telegram lets bots download files
up to 20 MB.

To add every document and photo you send to the knowledge base automatically,
turn on **Settings → Channels → Add files sent on Telegram to the knowledge
base** (`files_to_knowledge`).

**Too many messages at once** are slowed down: the bot answers *Too many
messages. Please wait a moment.* Wait a few seconds and send again.

## Reminders

`/remind 600 check the oven` makes the default agent carry out "check the
oven" in 10 minutes and send you the result. `/remind every 3600 any news
about the weather?` repeats every hour. `/jobs` lists them; `/cancel <id>`
stops one. You can also simply ask an agent ("remind me in 10 minutes to …").

Reminders live in the running bot: they stop when the bot (or the Web UI)
stops or restarts, so set them again afterwards. For tasks that must survive
restarts, use a job in the Web UI (**Jobs** page).

## Knowledge

By default, allowed Telegram users use **the same knowledge base as the Web
UI**: a note you save from Telegram is found in the Web UI, and the other way
round. Conversation memory stays separate — each Telegram user has their own.

To give every Telegram user their own, separate knowledge base, turn off
**Telegram uses the same knowledge base as the Web UI** in Settings → Channels
(`channels.telegram.shared_knowledge: false`).

## Groups

You can add the bot to a Telegram group.

* Each person in the group must be in **Allowed Telegram users**; the bot
  ignores the others.
* Mentioning the bot itself (`@YourBot what is …`) works: the mention is
  removed before the message goes to the agent. `@YourBot @writer …` reaches
  the writer agent.
* In a group, one message is answered at a time, in order.
* Telegram's *privacy mode* (on by default for new bots) lets a bot see only
  some group messages, such as commands and replies to the bot. To let it
  read every message, send `/setprivacy` to @BotFather and turn it off.
* Memory is per person, not per group.

## Privacy: who may use the bot

**Allowed Telegram users** (`channels.telegram.allowFrom`) is the list of
people who may use the bot. Each entry is either a numeric user id (what
`/whoami` shows — the safest choice) or a `@username`. A username can be
changed by its owner, a user id cannot.

* People who are not on the list get no answers. Only `/start`, `/help` and
  `/whoami` reply, and they show nothing but the person's own id.
* Each not-allowed person is written to the log once, with their id — useful
  if you forgot to add someone.
* While the list is empty, or still holds a placeholder such as
  `YOUR_USER_ID`, nobody can use the bot. The startup log, `zensynora doctor`
  and Settings → Channels warn about it.

## Inside the Web UI or `zensynora gateway`

Only one copy may run. When *Run the bot inside the Web UI* is on, `zensynora
gateway` (and the `myclaw` service, which runs it) does not start the bot and
says so; use `zensynora gateway --telegram` to run it there anyway, or turn the
setting off. Agents can write to you with the `send_telegram_message` tool
(only to allowed users).

| | Inside the Web UI (default) | `zensynora gateway` |
|---|---|---|
| Start | `zensynora webui` starts the bot too | Run `zensynora gateway` in a terminal or as a service |
| Settings | Change them in Settings → Channels; the bot restarts by itself | Restart the gateway after a change |
| Status, Test bot, Restart bot | In Settings → Channels and the sidebar | Messages in the terminal |
| Good for | Most people | A server without the Web UI |

Telegram allows **only one copy** of a bot at a time. If the gateway already
runs your bot, the Web UI shows *Another ZenSynora (zensynora gateway?) is
already running this bot — stop it or turn off 'Run the bot inside the Web
UI'.* and stops its own copy. When *Run the bot inside the Web UI* is off, the
status says *Runs in zensynora gateway, not in the Web UI*, and **Restart
bot** is not available. Pick one:
leave **Run the bot inside the Web UI** on and do not start the gateway, or
turn it off and use the gateway. `zensynora gateway` prints a reminder when
**Run the bot inside the Web UI** is still on.

**Polling and webhook.** By default the bot asks Telegram for new messages
(*polling*). This works anywhere, also behind a home router — nothing to set
up. The gateway can also run in *webhook* mode, where Telegram calls your
server instead:

```bash
zensynora gateway --webhook-url https://bot.example.com/webhook --port 8443
```

Telegram then sends every message to that URL. The bot listens on all
network addresses (`0.0.0.0`) at the given port (default 8443), path
`/webhook`, so the URL should end in `/webhook`. Your server needs a public
HTTPS address that reaches that port. Webhook
mode is only for `zensynora gateway`; the bot inside the Web UI always uses
polling. If you are not sure, use polling.

## Settings in `~/.myclaw/config.json`

The Web UI writes these for you; you can also edit the file.

```json
"channels": {
  "telegram": {
    "enabled": true,
    "token": "123456:ABC-DEF…",
    "allowFrom": ["123456789", "@yourname"],
    "run_in_webui": true,
    "shared_knowledge": true,
    "files_to_knowledge": false,
    "max_workers": 4
  }
}
```

| Key | Meaning |
|---|---|
| `enabled` | Switch the bot on or off |
| `token` | The token from @BotFather (or set `MYCLAW_TELEGRAM_TOKEN`) |
| `allowFrom` | Allowed users: numeric ids or `@usernames` |
| `run_in_webui` | `true`: `zensynora webui` runs the bot (default) |
| `shared_knowledge` | `true`: same knowledge base as the Web UI (default) |
| `files_to_knowledge` | `true`: documents and photos you send are added to the knowledge base right away (default `false`; files are always saved and shown to the agent) |
| `max_workers` | How many chats are answered at the same time |

## Advanced: your own Telegram Bot API server

Normally the bot talks to Telegram's servers (`api.telegram.org`). If you run
your own [Telegram Bot API server](https://github.com/tdlib/telegram-bot-api),
point the bot at it with an environment variable before starting ZenSynora:

```bash
ZENSYNORA_TELEGRAM_API_URL=http://localhost:8081 zensynora webui
```

Most people never need this. The tests use it too, to run the bot against a
local fake server.

## Troubleshooting

| Problem | What to do |
|---|---|
| The bot works for a few seconds, then *conflict* — and the log shows ZenSynora starting every few seconds | The `myclaw` service (`zensynora gateway`) was crash-looping and grabbing the bot (fixed 2026-10-18: the gateway now leaves Telegram to the Web UI). If you only use the Web UI: `sudo systemctl disable --now myclaw`. `zensynora doctor` warns about it. |
| An agent says it needs a "bot token" or "chat id" to message you | Agents use the `send_telegram_message` tool (since 2026-10-18) — the bot must be running. Disable home-made Telegram tools an agent created earlier (Tools page, or ask an agent: `disable_skill telegram_integration`). |
| The bot does not answer | Send `/whoami`. If it says you are not allowed, add the number to **Allowed Telegram users** and Save. Check that the status in Settings → Channels says *Running as @YourBot*. |
| Status: *Another ZenSynora (zensynora gateway?) is already running this bot …* (sidebar: *conflict*) | Two copies of the bot are running — for example `zensynora gateway` and the Web UI, or two computers with the same token. Stop one (or turn off **Run the bot inside the Web UI**), then click **Restart bot**. |
| Status: *No bot token yet* | Paste the token from @BotFather in **Telegram bot token** and Save. |
| `zensynora doctor` says *Telegram bot: nobody is allowed to use the bot* | Send `/whoami` to the bot and add the number to **Allowed Telegram users**. |
| `zensynora doctor` says *Telegram bot: enabled but no bot token* | Paste the token from @BotFather in Settings → Channels, or turn the bot off. |
| *Test bot* says the token works, but the status says *The token is wrong* | Fixed on 2026-10-18: a space or line break copied with the token. Update ZenSynora, or paste the token again without spaces and Save. |
| Status: *Error: …* | Click **Test bot**. *The token is wrong — copy it again from @BotFather.*: send `/token` to @BotFather, paste the token and Save. *Cannot reach Telegram — check the internet connection.*: this computer cannot reach Telegram. |
| The bot answers with a model error (rate-limited, server cannot be reached) | The bot works; the model does not. Open the **Models** page and click **Test** on the default model, or pick another one. |
| Nothing happens in a group | Make sure every person is allowed, and check privacy mode (see [Groups](#groups)). |
| "I can read text and files…" | You sent something the bot cannot use (a sticker, a location, a contact). Send text or a file. |
| The agent says it cannot open a PDF / Word / Excel file | Update ZenSynora: the readers (`pypdf`, `python-docx`, `openpyxl`) are installed with it since 2026-10-31. |
| Reminders are gone | The bot was restarted. Set them again, or use a job in the Web UI. |

More help: `zensynora doctor` has a **Telegram bot** check (category
*configuration*): *off*, *enabled but no bot token*, *nobody is allowed to use
the bot*, or how many users are allowed and where the bot runs (inside the
Web UI or with `zensynora gateway`). How the bot
works inside: [architecture diagrams §15](architecture_diagram.md#15-telegram-bot).
