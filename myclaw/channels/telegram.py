import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ..agent import Agent

logger = logging.getLogger(__name__)

# Thread pool for running blocking LLM calls
_executor = ThreadPoolExecutor(max_workers=4)


class TelegramChannel:
    """Telegram channel with async message handling."""
    
    def __init__(self, config, agent: Agent):
        self.token = config["channels"]["telegram"]["token"]
        self.allow_from = config["channels"]["telegram"].get("allowFrom", [])
        self.agent = agent

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages asynchronously."""
        if not update.message or str(update.message.from_user.id) not in self.allow_from:
            return
        
        text = update.message.text
        
        # Show typing indicator while processing
        await update.message.chat.send_action("typing")
        
        try:
            # Run blocking LLM call in thread pool to not block the event loop
            loop = asyncio.get_running_loop()
            user_id = str(update.message.from_user.id)
            response = await loop.run_in_executor(
                _executor,
                lambda: self.agent.think(text, user_id=user_id)
            )
            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            await update.message.reply_text(f"Eroare: {e}")

    def run(self):
        """Start the Telegram bot."""
        app = Application.builder() \
            .token(self.token) \
            .build()
        
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        print("🦞 Telegram gateway pornit...")
        app.run_polling()