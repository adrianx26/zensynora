import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ..agent import Agent
from .. import tools as tool_module

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


class TelegramChannel:
    """Telegram channel with multi-agent routing, scheduling, and dynamic tools."""

    def __init__(self, config, registry: dict):
        self.token      = config.channels.telegram.token.get_secret_value()
        self.allow_from = config.channels.telegram.allowFrom
        self.registry   = registry  # Feature 2: named agents

    # ── Routing ───────────────────────────────────────────────────────────────

    def _route(self, text: str) -> tuple:
        """Parse @agentname prefix. Returns (agent, cleaned_text)."""
        if text.startswith("@"):
            parts = text.split(None, 1)
            name    = parts[0][1:]   # strip the @
            cleaned = parts[1] if len(parts) > 1 else ""
            agent   = self.registry.get(name) or self.registry["default"]
            return agent, cleaned
        return self.registry["default"], text

    def _allowed(self, update: Update) -> bool:
        return bool(update.message and
                    str(update.message.from_user.id) in self.allow_from)

    # ── Message handler ───────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return

        user_id = str(update.message.from_user.id)
        text    = update.message.text

        # Store chat_id so scheduled jobs can notify this user (Feature 5)
        tool_module.register_chat_id(user_id, update.effective_chat.id)

        agent, cleaned = self._route(text)
        await update.message.chat.send_action("typing")

        try:
            loop     = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                _executor,
                lambda: agent.think(cleaned, user_id=user_id)
            )
            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Message handling error: {e}")
            await update.message.reply_text(f"Error: {e}")

    # ── Feature 1: Scheduler commands ────────────────────────────────────────

    async def remind_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Schedule a reminder.
        Usage: /remind <seconds> <message>
               /remind every <seconds> <message>
        """
        if not self._allowed(update):
            return

        user_id = str(update.message.from_user.id)
        tool_module.register_chat_id(user_id, update.effective_chat.id)

        args = context.args
        if not args:
            await update.message.reply_text(
                "Usage:\n"
                "  /remind <seconds> <message>\n"
                "  /remind every <seconds> <message>"
            )
            return

        try:
            if args[0].lower() == "every" and len(args) >= 3:
                every  = int(args[1])
                task   = " ".join(args[2:])
                result = tool_module.schedule(task=task, every=every, user_id=user_id)
            else:
                delay  = int(args[0])
                task   = " ".join(args[1:])
                result = tool_module.schedule(task=task, delay=delay, user_id=user_id)
            await update.message.reply_text(f"✅ {result}")
        except (ValueError, IndexError):
            await update.message.reply_text("Error: seconds must be a whole number.")

    async def jobs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/jobs — list all active scheduled jobs."""
        if not self._allowed(update):
            return
        await update.message.reply_text(tool_module.list_schedules())

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/cancel <job_id> — cancel a scheduled job."""
        if not self._allowed(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /cancel <job_id>")
            return
        await update.message.reply_text(tool_module.cancel_schedule(context.args[0]))

    # ── Feature 2: Agent listing ──────────────────────────────────────────────

    async def agents_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/agents — list available named agents."""
        if not self._allowed(update):
            return
        names = ", ".join(self.registry.keys())
        await update.message.reply_text(
            f"🤖 Available agents: {names}\n\n"
            f"To use a specific agent, prefix your message with @name\n"
            f"Example: @coder write a binary search function"
        )

    # ── Bot startup ───────────────────────────────────────────────────────────

    def run(self):
        app = Application.builder().token(self.token).build()

        # Inject JobQueue into tools module (enables Feature 1 + 5)
        tool_module.set_job_queue(app.job_queue)

        # Handlers
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(CommandHandler("remind",  self.remind_command))
        app.add_handler(CommandHandler("jobs",    self.jobs_command))
        app.add_handler(CommandHandler("cancel",  self.cancel_command))
        app.add_handler(CommandHandler("agents",  self.agents_command))

        print("🦞 MyClaw Telegram gateway started.")
        print(f"   Agents: {', '.join(self.registry.keys())}")
        print("   Commands: /remind /jobs /cancel /agents")
        app.run_polling()