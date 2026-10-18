"""Telegram bot channel (reviewed and fixed 2026-10-16).

Runs either inside the Web UI process (``channels.telegram.run_in_webui``) via
:meth:`TelegramChannel.start` / :meth:`TelegramChannel.stop`, or on its own with
``zensynora gateway`` (:meth:`TelegramChannel.run`, polling or webhook).

Message flow::

    Telegram update → allowFrom check → queue → workers (one message at a time
    per chat, several chats in parallel) → "@agent" routing → Agent.think
    → reply split into ≤ 4000-character messages

Fixed in this review:

* normal messages were queued but the worker was never started — the bot
  answered only /commands;
* long answers (> 4096 characters) and Markdown Telegram cannot parse made
  the reply fail;
* errors showed raw exception text; "typing…" vanished after 5 s;
* people not in ``allowFrom`` were ignored silently and could not find their
  id (new /start, /help, /whoami);
* ``@agent`` routing was case-sensitive and did not see agents created later;
* allowed users had a knowledge base separate from the Web UI
  (``channels.telegram.shared_knowledge``, default on).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Callable

from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Conflict, InvalidToken, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .. import tools as tool_module

logger = logging.getLogger(__name__)

MAX_MESSAGE = 4000          # Telegram's limit is 4096 characters
PLACEHOLDERS = {"your_user_id", "<your_user_id>", "123456789", "your-id", "user_id", ""}

COMMANDS = [
    ("help", "What this bot can do"),
    ("whoami", "Show your Telegram user id"),
    ("agents", "List the agents (address one with @name)"),
    ("remind", "Remind me: /remind <seconds> <text>"),
    ("jobs", "List scheduled reminders"),
    ("cancel", "Cancel a reminder: /cancel <id>"),
    ("knowledge_search", "Search the knowledge base"),
    ("knowledge_list", "List knowledge notes"),
    ("knowledge_read", "Read a note: /knowledge_read <permalink>"),
    ("knowledge_write", "Save a note: /knowledge_write <title> | <text>"),
    ("knowledge_sync", "Re-read the knowledge files"),
    ("knowledge_tags", "List knowledge tags"),
]

HELP = (
    "🤖 I'm your ZenSynora assistant.\n\n"
    "Just write to me — the default agent answers. Start a message with @name to ask "
    "a specific agent (see /agents).\n\n"
    "Commands:\n"
    + "\n".join(f"/{c} — {d}" for c, d in COMMANDS)
)


# Bots running in this process (used by the send_telegram_message tool).
ACTIVE: list["TelegramChannel"] = []


def allow_list_problem(allow_from: list[str]) -> str | None:
    """Plain-language problem with allowFrom, or None when it looks usable."""
    real = [a for a in (allow_from or []) if str(a).strip().lower() not in PLACEHOLDERS]
    if not real:
        return ("Nobody can use the Telegram bot yet: add your Telegram user id to "
                "channels.telegram.allowFrom (send /whoami to the bot to see it).")
    return None


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split a long answer at paragraph, line or word boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit))
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


class _RateLimiter:
    """Per-user token bucket: ``max_tokens`` messages, refilled at ``refill_rate``/s."""

    def __init__(self, max_tokens: int = 10, refill_rate: float = 0.5):
        self._max = max_tokens
        self._rate = refill_rate
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, user_id: str) -> bool:
        now = time.time()
        tokens, last = self._buckets.get(user_id, (float(self._max), now))
        tokens = min(self._max, tokens + (now - last) * self._rate)
        if tokens >= 1.0:
            self._buckets[user_id] = (tokens - 1.0, now)
            return True
        self._buckets[user_id] = (tokens, now)
        return False


class TelegramChannel:
    """Telegram bot with multi-agent routing, reminders and knowledge commands."""

    def __init__(self, config: Any, registry: dict | Callable[[], dict]):
        tg = config.channels.telegram
        self.config = config
        # Spaces or a line break pasted with the token made every request fail
        # ("token wrong") while the Test bot button (which trimmed it) worked.
        self.token = "".join(tg.token.get_secret_value().split())
        self.allow_from = [str(a).strip() for a in (tg.allowFrom or []) if str(a).strip()]
        self.shared_knowledge = bool(getattr(tg, "shared_knowledge", True))
        self._registry_src = registry
        self._limiter = _RateLimiter(max_tokens=10, refill_rate=0.5)
        self._queue: asyncio.Queue | None = None
        self._workers: list[asyncio.Task] = []
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._n_workers = max(1, min(int(getattr(tg, "max_workers", 4) or 4), 8))
        self._warned: set[str] = set()
        self._on_start: list[Callable[[], Any]] = []
        self.app: Application | None = None
        self._status: dict[str, Any] = {"state": "stopped", "bot_username": None, "error": None, "since": None}
        self._started_at: float | None = None
        self._first_conflict: float | None = None
        self._conflict_noted = False
        self._bot_username: str | None = None

    # ── registry / access ────────────────────────────────────────────────────
    @property
    def registry(self) -> dict:
        """The current agents (a callable source is asked every time, so agents
        added or rebuilt after a settings change are seen at once)."""
        src = self._registry_src
        return src() if callable(src) else src

    def on_start(self, fn: Callable[[], Any]) -> None:
        """Run ``fn`` (sync or async) on the bot's event loop when it starts."""
        self._on_start.append(fn)

    def _is_allowed(self, user: Any) -> bool:
        if user is None:
            return False
        allowed = {a.lower() for a in self.allow_from}
        if str(user.id) in allowed:
            return True
        name = (getattr(user, "username", None) or "").lower()
        return bool(name) and (f"@{name}" in allowed or name in allowed)

    def _allowed(self, update: Update) -> bool:
        user = update.effective_user
        ok = self._is_allowed(user)
        if ok and self.shared_knowledge:
            from ..knowledge.scope import share_main_knowledge

            share_main_knowledge(str(user.id))
        elif not ok and user is not None and str(user.id) not in self._warned:
            self._warned.add(str(user.id))
            logger.warning("Telegram: message from user %s (@%s) ignored — not in channels.telegram.allowFrom",
                           user.id, getattr(user, "username", "") or "-")
        return ok

    # ── routing ──────────────────────────────────────────────────────────────
    def _route(self, text: str) -> tuple[Any, str]:
        """``@name rest`` → (agent, rest). Any case; a mention of the bot itself is
        removed; an unknown @word stays part of the message for the default agent."""
        reg = self.registry
        text = (text or "").strip()
        for _ in range(2):
            m = re.match(r"^@([\w-]+)[\s,:]*(.*)$", text, re.DOTALL)
            if not m:
                break
            name, rest = m.group(1), m.group(2)
            if self._bot_username and name.lower() == self._bot_username.lower():
                text = rest.strip()
                continue
            match = next((k for k in reg if k.lower() == name.lower()), None)
            if match:
                return reg[match], rest.strip()
            break
        return reg["default"], text

    # ── sending ──────────────────────────────────────────────────────────────
    async def _send(self, message: Any, text: str, markdown: bool = False) -> None:
        """Reply in ≤ 4000-character parts; Markdown falls back to plain text."""
        for part in split_message(text) or ["(empty answer)"]:
            if markdown:
                try:
                    await message.reply_text(part, parse_mode="Markdown")
                    continue
                except BadRequest:
                    pass  # Telegram could not parse the Markdown — send it as it is
            await message.reply_text(part)

    @staticmethod
    async def _typing_once(chat: Any) -> None:
        try:
            await chat.send_action(ChatAction.TYPING)
        except TelegramError:
            pass

    async def _keep_typing(self, chat: Any) -> None:
        try:
            while True:
                await asyncio.sleep(4.5)  # the indicator disappears after ~5 s
                await self._typing_once(chat)
        except asyncio.CancelledError:
            pass

    # ── messages ─────────────────────────────────────────────────────────────
    @staticmethod
    def _text_of(update: Update) -> str | None:
        msg = update.effective_message
        if msg is None:
            return None
        return msg.text or msg.caption

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        msg = update.effective_message
        text = self._text_of(update)
        if not text:
            await msg.reply_text("I can only read text for now — add a caption or write your message.")
            return
        user_id = str(update.effective_user.id)
        if not self._limiter.allow(user_id):
            logger.warning("Telegram: rate limit for user %s", user_id)
            await msg.reply_text("⏳ Too many messages. Please wait a moment.")
            return
        if self._queue is None:
            await self._handle_update(update, text)  # not started via start(): answer directly
            return
        try:
            self._queue.put_nowait((update, text))
        except asyncio.QueueFull:
            logger.warning("Telegram: queue full, message rejected")
            await msg.reply_text("🚫 I'm busy right now. Please try again in a minute.")

    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            update, text = await self._queue.get()
            try:
                chat_id = update.effective_chat.id
                lock = self._chat_locks.setdefault(chat_id, asyncio.Lock())
                async with lock:  # one message at a time per chat, in order
                    await self._handle_update(update, text)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # a bad update must not kill the worker
                logger.exception("Telegram: error handling a message: %s", e)
            finally:
                self._queue.task_done()

    async def _handle_update(self, update: Update, text: str) -> None:
        msg = update.effective_message
        user_id = str(update.effective_user.id)
        tool_module.register_chat_id(user_id, update.effective_chat.id)  # for reminders
        agent, cleaned = self._route(text)
        if not cleaned:
            await msg.reply_text(f"What should @{agent.name} do? Write it after the name.")
            return
        await self._typing_once(update.effective_chat)
        typing = asyncio.create_task(self._keep_typing(update.effective_chat))
        try:
            response = await agent.think(cleaned, user_id=user_id)
        except Exception as e:
            from ..llm_errors import describe_llm_error

            logger.error("Telegram: agent %s failed: %s", getattr(agent, "name", "?"), e)
            short = re.sub(r"\s*\(Details:.*$", "", describe_llm_error(e), flags=re.DOTALL)  # details → log
            response = f"⚠️ Sorry, that did not work: {short}"
        finally:
            typing.cancel()
        prefix = f"@{agent.name}: " if getattr(agent, "name", "default") != "default" else ""
        await self._send(msg, prefix + (response or ""))

    # ── basic commands ───────────────────────────────────────────────────────
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._is_allowed(update.effective_user):
            await update.effective_message.reply_text(HELP)
        else:
            await self.whoami_command(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return await self.whoami_command(update, context)
        await update.effective_message.reply_text(HELP)

    async def whoami_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Answers anyone with their own id only — that is how the owner allows themself."""
        user = update.effective_user
        if user is None:
            return
        if self._is_allowed(user):
            text = f"Your Telegram user id is {user.id}. You are allowed to use this bot."
        else:
            text = (f"Your Telegram user id is {user.id}.\n"
                    "This bot is private. To use it, add this number to \"Allowed Telegram users\" "
                    "(Web UI → Settings → Channels) or to channels.telegram.allowFrom in "
                    "~/.myclaw/config.json.")
            self._allowed(update)  # logs the id once
        await update.effective_message.reply_text(text)

    # ── reminders ────────────────────────────────────────────────────────────
    async def remind_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/remind <seconds> <text> · /remind every <seconds> <text>"""
        if not self._allowed(update):
            return
        user_id = str(update.effective_user.id)
        tool_module.register_chat_id(user_id, update.effective_chat.id)
        args = context.args or []
        usage = "Usage:\n  /remind <seconds> <text>\n  /remind every <seconds> <text>"
        try:
            if args and args[0].lower() == "every" and len(args) >= 3:
                result = tool_module.schedule(task=" ".join(args[2:]), every=int(args[1]), user_id=user_id)
            elif len(args) >= 2:
                result = tool_module.schedule(task=" ".join(args[1:]), delay=int(args[0]), user_id=user_id)
            else:
                await update.effective_message.reply_text(usage)
                return
        except ValueError:
            await update.effective_message.reply_text("The time must be a whole number of seconds.\n" + usage)
            return
        await self._send(update.effective_message, f"✅ {result}")

    async def jobs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._allowed(update):
            await self._send(update.effective_message, tool_module.list_schedules())

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        if not context.args:
            await update.effective_message.reply_text("Usage: /cancel <job_id>  (see /jobs)")
            return
        await self._send(update.effective_message, tool_module.cancel_schedule(context.args[0]))

    async def agents_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            return
        lines = ["🤖 Agents:"]
        named = {a.name: a for a in self.config.agents.named}
        for name in self.registry:
            desc = (getattr(named.get(name), "description", "") or "").strip()
            lines.append(f"• @{name}" + (f" — {desc}" if desc else (" — the main agent" if name == "default" else "")))
        lines.append("\nStart a message with @name to ask that agent, e.g. @coder write a binary search.")
        await self._send(update.effective_message, "\n".join(lines))

    # ── knowledge ────────────────────────────────────────────────────────────
    async def _kb(self, update: Update, fn: Callable[..., str], **kwargs: Any) -> None:
        if not self._allowed(update):
            return
        try:
            result = await asyncio.to_thread(fn, user_id=str(update.effective_user.id), **kwargs)
        except Exception as e:
            logger.error("Telegram knowledge command failed: %s", e)
            result = f"⚠️ That did not work: {e}"
        await self._send(update.effective_message, result, markdown=True)

    async def knowledge_search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            if self._allowed(update):
                await update.effective_message.reply_text("Usage: /knowledge_search <words>")
            return
        await self._kb(update, tool_module.search_knowledge, query=" ".join(context.args), limit=5)

    async def knowledge_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._kb(update, tool_module.list_knowledge, limit=20)

    async def knowledge_read_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            if self._allowed(update):
                await update.effective_message.reply_text("Usage: /knowledge_read <permalink>  (see /knowledge_list)")
            return
        await self._kb(update, tool_module.read_knowledge, permalink=context.args[0])

    async def knowledge_write_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = " ".join(context.args or [])
        if "|" not in text:
            if self._allowed(update):
                await update.effective_message.reply_text(
                    "Usage: /knowledge_write <title> | <text>\nExample: /knowledge_write Wi-Fi | The router is in the hall")
            return
        title, content = (p.strip() for p in text.split("|", 1))
        await self._kb(update, tool_module.write_to_knowledge, title=title, content=content)

    async def knowledge_sync_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._kb(update, tool_module.sync_knowledge_base)

    async def knowledge_tags_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._kb(update, tool_module.list_knowledge_tags)

    # ── application ──────────────────────────────────────────────────────────
    def _build_app(self) -> Application:
        if not self.token:
            raise ValueError("No Telegram bot token — add it in Settings → Channels.")
        builder = Application.builder().token(self.token).post_init(self._post_init)
        api = os.environ.get("ZENSYNORA_TELEGRAM_API_URL", "").rstrip("/")
        if api:  # a self-hosted Bot API server (or a test server)
            builder = builder.base_url(f"{api}/bot").base_file_url(f"{api}/file/bot")
        app = builder.build()
        tool_module.set_job_queue(app.job_queue)  # reminders
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("whoami", self.whoami_command))
        app.add_handler(CommandHandler("remind", self.remind_command))
        app.add_handler(CommandHandler("jobs", self.jobs_command))
        app.add_handler(CommandHandler("cancel", self.cancel_command))
        app.add_handler(CommandHandler("agents", self.agents_command))
        app.add_handler(CommandHandler("knowledge_search", self.knowledge_search_command))
        app.add_handler(CommandHandler("knowledge_list", self.knowledge_list_command))
        app.add_handler(CommandHandler("knowledge_read", self.knowledge_read_command))
        app.add_handler(CommandHandler("knowledge_write", self.knowledge_write_command))
        app.add_handler(CommandHandler("knowledge_sync", self.knowledge_sync_command))
        app.add_handler(CommandHandler("knowledge_tags", self.knowledge_tags_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        media = (filters.PHOTO | filters.VOICE | filters.AUDIO | filters.VIDEO | filters.Document.ALL
                 | filters.Sticker.ALL | filters.VIDEO_NOTE)
        app.add_handler(MessageHandler(media, self.handle_message))  # caption = message
        app.add_error_handler(self._on_error)
        return app

    async def _post_init(self, app: Application) -> None:
        """Runs on the bot's event loop once it is initialised."""
        self._queue = asyncio.Queue(maxsize=100)
        self._workers = [asyncio.create_task(self._worker(), name=f"telegram-worker-{i}")
                         for i in range(self._n_workers)]
        try:
            me = await app.bot.get_me()
            self._bot_username = me.username
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        except TelegramError as e:
            logger.warning("Telegram: could not set the command menu: %s", e)
        for fn in self._on_start:
            try:
                res = fn()
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                logger.warning("Telegram: start-up hook failed: %s", e)
        problem = allow_list_problem(self.allow_from)
        if problem:
            logger.warning("Telegram: %s", problem)
        logger.info("Telegram bot @%s started (agents: %s)", self._bot_username, ", ".join(self.registry))

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if isinstance(err, Conflict):
            self._polling_error(err)
            return
        logger.error("Telegram error: %s", err)

    CONFLICT_GRACE = 90.0  # seconds

    def _polling_error(self, err: TelegramError) -> None:
        if isinstance(err, Conflict):
            # Right after a restart Telegram still holds the previous copy's open
            # request for a while and answers "conflict" — that is not a second
            # bot. The library keeps retrying; give up only if it lasts.
            now = time.time()
            if self._first_conflict is None:
                self._first_conflict = now
            started = self._started_at or now
            if now - started < self.CONFLICT_GRACE or now - self._first_conflict < self.CONFLICT_GRACE:
                if not self._conflict_noted:
                    self._conflict_noted = True
                    logger.info("Telegram: the previous connection is still open — retrying for up to %d s",
                                int(self.CONFLICT_GRACE))
                return
            if self._status["state"] == "conflict":
                return
            self._set("conflict", "Another program is already running this bot (zensynora gateway?). "
                                  "Telegram allows only one.")
            logger.error("Telegram: %s Stopping this copy.", self._status["error"])
            asyncio.get_running_loop().create_task(self.stop(keep_state=True))
        else:
            logger.warning("Telegram polling error: %s", err)

    def _set(self, state: str, error: str | None = None) -> None:
        self._status.update(state=state, error=error, since=time.time(), bot_username=self._bot_username)

    async def send_text(self, chat_id: int | str, text: str) -> None:
        """Send a message to a chat or user id (the person must have pressed
        Start in the bot once — Telegram forbids bots to write first)."""
        if self.app is None:
            raise RuntimeError("The bot is not running.")
        for part in split_message(text):
            await self.app.bot.send_message(chat_id=int(chat_id), text=part)

    def status(self) -> dict[str, Any]:
        return dict(self._status, bot_username=self._bot_username,
                    allow_from_problem=allow_list_problem(self.allow_from))

    # ── run inside an existing event loop (the Web UI) ───────────────────────
    async def start(self) -> None:
        """Start polling in the current event loop (returns at once)."""
        if self.app is not None:
            return
        self._set("starting")
        self._started_at, self._first_conflict, self._conflict_noted = time.time(), None, False
        try:
            self.app = self._build_app()
            await self.app.initialize()
            await self._post_init(self.app)  # the library runs post_init only in run_polling()
            await self.app.start()
            await self.app.updater.start_polling(error_callback=self._polling_error,
                                                 drop_pending_updates=False)
            if self._status["state"] == "starting":
                self._set("running")
                ACTIVE.append(self)
        except InvalidToken as e:
            self._set("error", "The bot token is wrong — copy it again from @BotFather.")
            logger.error("Telegram: the token was refused (%s)", str(e).replace(self.token, "***"))
            await self._cleanup()
        except Exception as e:
            self._set("error", f"Could not start the Telegram bot: {e}")
            logger.error("Telegram: %s", self._status["error"])
            await self._cleanup()

    async def stop(self, keep_state: bool = False) -> None:
        """Stop polling and the workers."""
        if self in ACTIVE:
            ACTIVE.remove(self)
        await self._cleanup()
        if not keep_state:
            self._set("stopped")

    async def _cleanup(self) -> None:
        app, self.app = self.app, None
        for t in self._workers:
            t.cancel()
        self._workers = []
        self._queue = None
        if app is None:
            return
        for step in ("updater.stop", "stop", "shutdown"):
            try:
                if step == "updater.stop":
                    if app.updater and app.updater.running:
                        await app.updater.stop()
                elif step == "stop":
                    if app.running:
                        await app.stop()
                else:
                    await app.shutdown()
            except Exception as e:  # best effort
                logger.debug("Telegram %s: %s", step, e)

    # ── run on its own (zensynora gateway) ───────────────────────────────────
    def run(self, webhook_url: str | None = None, port: int = 8443) -> None:
        """Blocking: polling (default) or webhook mode.

        Polling uses the same start()/stop() as the Web UI on a fresh event loop.
        (2026-10-18: the library's run_polling() looked for the "current" event
        loop, which start-up code had already closed — the gateway crashed with
        "There is no current event loop" and systemd restarted it every few
        seconds.)
        """
        print(f"🦞 ZenSynora Telegram bot{' (webhook mode)' if webhook_url else ''} starting…")
        print(f"   Agents: {', '.join(self.registry)}")
        problem = allow_list_problem(self.allow_from)
        if problem:
            print(f"   ⚠️  {problem}")
        if webhook_url:
            asyncio.set_event_loop(asyncio.new_event_loop())
            app = self.app = self._build_app()
            self._set("running")
            app.run_webhook(listen="0.0.0.0", port=port, url_path="webhook", webhook_url=webhook_url)
            self._set("stopped")
            return
        try:
            asyncio.run(self._serve())
        except KeyboardInterrupt:
            pass
        state = self._status["state"]
        if state in ("error", "conflict"):
            raise SystemExit(f"Telegram bot stopped: {self._status['error']}")

    async def _serve(self) -> None:
        await self.start()
        if self._status["state"] != "running":
            return
        print(f"   Running as @{self._bot_username}. Send /help to the bot. Ctrl+C stops it.")
        try:
            while self.app is not None:  # stop() (or a lasting conflict) clears it
                await asyncio.sleep(1)
        finally:
            if self.app is not None:
                await self.stop()

    def run_webhook(self, webhook_url: str, port: int = 8443) -> None:
        """Kept for compatibility: same as ``run(webhook_url, port)``."""
        self.run(webhook_url=webhook_url, port=port)


# ── Tool for agents (2026-10-18) ─────────────────────────────────────────────
# Agents had no way to write to the user on Telegram and built their own
# "telegram_integration" tools with placeholder tokens. This uses the bot that
# is already running and only writes to people in Allowed Telegram users.
async def send_telegram_message(text: str, to: str = "") -> str:
    """Send a Telegram message through ZenSynora's own bot to an allowed user.

    text: the message
    to: numeric Telegram user id from Allowed Telegram users (empty = all allowed users)
    """
    if not ACTIVE:
        return ("Error: the Telegram bot is not running. Turn it on in the Web UI → Settings → "
                "Channels (do not create your own Telegram tool — none is needed).")
    ch = ACTIVE[0]
    allowed = [a for a in ch.allow_from if a.isdigit()]
    if to:
        to = str(to).strip().lstrip("@")
        if to not in allowed:
            return f"Error: {to} is not in Allowed Telegram users ({', '.join(allowed) or 'none'})."
        targets = [to]
    else:
        targets = allowed
    if not targets:
        return "Error: no numeric user id in Allowed Telegram users."
    if not (text or "").strip():
        return "Error: the message is empty."
    sent, failed = [], []
    for chat in targets:
        try:
            await ch.send_text(chat, text)
            sent.append(chat)
        except Exception as e:
            failed.append(f"{chat} ({str(e)[:120]})")
    out = f"Sent to {', '.join(sent)}." if sent else "Nothing sent."
    if failed:
        out += (f" Failed: {'; '.join(failed)}. The person must have pressed Start in the bot once.")
    return out
