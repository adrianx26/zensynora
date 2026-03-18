import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ..agent import Agent
from .. import tools as tool_module

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Per-user token-bucket rate limiter.

    Each user gets `max_tokens` tokens that refill at `refill_rate` per second.
    A message costs 1 token. If the bucket is empty the message is rejected.
    """
    def __init__(self, max_tokens: int = 10, refill_rate: float = 0.5):
        self._max = max_tokens
        self._rate = refill_rate
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, user_id: str) -> bool:
        now = time.time()
        tokens, last = self._buckets.get(user_id, (float(self._max), now))
        elapsed = now - last
        tokens = min(self._max, tokens + elapsed * self._rate)
        if tokens >= 1.0:
            self._buckets[user_id] = (tokens - 1.0, now)
            return True
        self._buckets[user_id] = (tokens, now)
        return False


class TelegramChannel:
    """Telegram channel with multi-agent routing, scheduling, and dynamic tools."""

    def __init__(self, config, registry: dict):
        self.token      = config.channels.telegram.token.get_secret_value()
        self.allow_from = config.channels.telegram.allowFrom
        self.registry   = registry  # Feature 2: named agents
        self._limiter   = _RateLimiter(max_tokens=10, refill_rate=0.5)
        # 7.2: Message queue with backpressure
        self._message_queue = asyncio.Queue(maxsize=100)
        self._worker_task = None

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

        if not self._limiter.allow(user_id):
            logger.warning(f"Rate limit exceeded for user {user_id} via telegram")
            await update.message.reply_text("⏳ Too many messages. Please wait a moment.")
            return

        # 7.2: Non-blocking put with backpressure
        try:
            self._message_queue.put_nowait((update, context))
        except asyncio.QueueFull:
            logger.warning("Message queue full, rejecting update")
            await update.message.reply_text("🚫 Service is busy. Please try again later.")
            return

    async def _process_messages(self):
        """Worker task that processes messages from the queue."""
        while True:
            try:
                update, context = await self._message_queue.get()
                await self._handle_update(update, context)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _handle_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle a message from the queue."""
        user_id = str(update.message.from_user.id)
        text    = update.message.text

        # Store chat_id so scheduled jobs can notify this user (Feature 5)
        tool_module.register_chat_id(user_id, update.effective_chat.id)

        agent, cleaned = self._route(text)
        
        # 7.3: Optimized typing indicator - only send for expected processing > 500ms
        # Estimate: commands typically take longer than simple responses
        is_command = cleaned.startswith('/') or len(cleaned) > 100
        if is_command:
            await update.message.chat.send_action("typing")

        try:
            loop     = asyncio.get_running_loop()
            response = await agent.think(cleaned, user_id=user_id)
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

    # ── Knowledge commands ─────────────────────────────────────────────────────

    async def knowledge_search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/knowledge_search <query> — search the knowledge base."""
        if not self._allowed(update):
            return
        
        if not context.args:
            await update.message.reply_text(
                "🔍 Search the knowledge base\n"
                "Usage: /knowledge_search <query>\n"
                "Example: /knowledge_search project phoenix"
            )
            return
        
        query = " ".join(context.args)
        user_id = str(update.message.from_user.id)
        
        try:
            result = tool_module.search_knowledge(query=query, limit=5, user_id=user_id)
            await update.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Knowledge search error: {e}")
            await update.message.reply_text(f"Error searching: {e}")

    async def knowledge_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/knowledge_list — list all knowledge notes."""
        if not self._allowed(update):
            return
        
        user_id = str(update.message.from_user.id)
        
        try:
            result = tool_module.list_knowledge(limit=20, user_id=user_id)
            await update.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Knowledge list error: {e}")
            await update.message.reply_text(f"Error listing: {e}")

    async def knowledge_read_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/knowledge_read <permalink> — read a specific knowledge note."""
        if not self._allowed(update):
            return
        
        if not context.args:
            await update.message.reply_text(
                "📖 Read a knowledge note\n"
                "Usage: /knowledge_read <permalink>\n"
                "Example: /knowledge_read project-phoenix"
            )
            return
        
        permalink = context.args[0]
        user_id = str(update.message.from_user.id)
        
        try:
            result = tool_module.read_knowledge(permalink=permalink, user_id=user_id)
            await update.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Knowledge read error: {e}")
            await update.message.reply_text(f"Error reading: {e}")

    async def knowledge_write_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/knowledge_write <title> | <content> — create a new knowledge note."""
        if not self._allowed(update):
            return
        
        if not context.args:
            await update.message.reply_text(
                "📝 Create a knowledge note\n"
                "Usage: /knowledge_write <title> | <content>\n"
                "Example: /knowledge_write Meeting Notes | Discussed Q2 roadmap..."
            )
            return
        
        text = " ".join(context.args)
        if "|" not in text:
            await update.message.reply_text("Error: Use | to separate title and content")
            return
        
        title, content = text.split("|", 1)
        title = title.strip()
        content = content.strip()
        user_id = str(update.message.from_user.id)
        
        try:
            result = tool_module.write_to_knowledge(
                title=title,
                content=content,
                user_id=user_id
            )
            await update.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Knowledge write error: {e}")
            await update.message.reply_text(f"Error writing: {e}")

    async def knowledge_sync_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/knowledge_sync — synchronize knowledge base with files."""
        if not self._allowed(update):
            return
        
        user_id = str(update.message.from_user.id)
        
        try:
            result = tool_module.sync_knowledge_base(user_id=user_id)
            await update.message.reply_text(result)
        except Exception as e:
            logger.error(f"Knowledge sync error: {e}")
            await update.message.reply_text(f"Error syncing: {e}")

    async def knowledge_tags_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/knowledge_tags — list all knowledge tags."""
        if not self._allowed(update):
            return
        
        user_id = str(update.message.from_user.id)
        
        try:
            result = tool_module.list_knowledge_tags(user_id=user_id)
            await update.message.reply_text(result)
        except Exception as e:
            logger.error(f"Knowledge tags error: {e}")
            await update.message.reply_text(f"Error listing tags: {e}")

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
        
        # Knowledge handlers
        app.add_handler(CommandHandler("knowledge_search", self.knowledge_search_command))
        app.add_handler(CommandHandler("knowledge_list",   self.knowledge_list_command))
        app.add_handler(CommandHandler("knowledge_read",   self.knowledge_read_command))
        app.add_handler(CommandHandler("knowledge_write",  self.knowledge_write_command))
        app.add_handler(CommandHandler("knowledge_sync",   self.knowledge_sync_command))
        app.add_handler(CommandHandler("knowledge_tags",   self.knowledge_tags_command))

        print("🦞 MyClaw Telegram gateway started.")
        print(f"   Agents: {', '.join(self.registry.keys())}")
        print("   Commands: /remind /jobs /cancel /agents")
        print("   Knowledge: /knowledge_search /knowledge_list /knowledge_read /knowledge_write /knowledge_sync /knowledge_tags")
        app.run_polling()

    def run_webhook(self, webhook_url: str, port: int = 8443):
        """Run Telegram bot in webhook mode instead of polling.
        
        This is more efficient for production deployments as it doesn't
        require constant polling and can handle higher loads.
        
        Args:
            webhook_url: The public URL where Telegram can send updates
            port: Port to run the webhook server on (default 8443)
        """
        app = Application.builder().token(self.token).build()
        
        # Inject JobQueue into tools module
        tool_module.set_job_queue(app.job_queue)
        
        # Handlers - same as run()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(CommandHandler("remind",  self.remind_command))
        app.add_handler(CommandHandler("jobs",    self.jobs_command))
        app.add_handler(CommandHandler("cancel",  self.cancel_command))
        app.add_handler(CommandHandler("agents",  self.agents_command))
        
        # Knowledge handlers
        app.add_handler(CommandHandler("knowledge_search", self.knowledge_search_command))
        app.add_handler(CommandHandler("knowledge_list",   self.knowledge_list_command))
        app.add_handler(CommandHandler("knowledge_read",   self.knowledge_read_command))
        app.add_handler(CommandHandler("knowledge_write",  self.knowledge_write_command))
        app.add_handler(CommandHandler("knowledge_sync",   self.knowledge_sync_command))
        app.add_handler(CommandHandler("knowledge_tags",   self.knowledge_tags_command))
        
        print("🦞 MyClaw Telegram gateway started in WEBHOOK mode.")
        print(f"   Webhook URL: {webhook_url}")
        print(f"   Port: {port}")
        print(f"   Agents: {', '.join(self.registry.keys())}")
        
        # Run in webhook mode
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url,
        )