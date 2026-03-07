from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from ..agent import Agent

class TelegramChannel:
    def __init__(self, config, agent: Agent):
        self.token = config["channels"]["telegram"]["token"]
        self.allow_from = config["channels"]["telegram"].get("allowFrom", [])
        self.agent = agent

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or str(update.message.from_user.id) not in self.allow_from:
            return
        text = update.message.text
        response = self.agent.think(text)
        await update.message.reply_text(response)

    def run(self):
        app = Application.builder().token(self.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        print("🦞 Telegram gateway pornit...")
        app.run_polling()