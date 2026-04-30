"""Discord channel adapter.

Lightweight bridge between a Discord bot and the ZenSynora gateway.
Optional dependency: ``discord.py``. The adapter imports cleanly without
it; calling ``run()`` raises ``RuntimeError`` with install instructions.

Design:

* The bot listens for **direct mentions** (``@bot help me``) and
  ``/ask`` slash commands. We don't auto-respond to every message in a
  channel — that's noisy and gets bots banned.
* Each Discord channel maps to a ZenSynora user_id, namespaced as
  ``discord:<channel_id>``. Threads inherit their parent channel's
  user_id so conversation memory stays continuous.
* Long agent responses are split at paragraph boundaries to fit the
  2000-character message limit Discord enforces.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional

logger = logging.getLogger(__name__)

# ── Optional dependency ──────────────────────────────────────────────────

try:  # pragma: no cover - import guard
    import discord
    from discord.ext import commands
    _DISCORD_AVAILABLE = True
except Exception:
    discord = None  # type: ignore[assignment]
    commands = None  # type: ignore[assignment]
    _DISCORD_AVAILABLE = False


def is_discord_available() -> bool:
    return _DISCORD_AVAILABLE


# Type alias for the agent-call callback the channel hands inbound messages to.
AgentHandler = Callable[[str, str], Awaitable[str]]
#                          message  user_id   ⇒ reply text


_DISCORD_MESSAGE_LIMIT = 2000


def chunk_for_discord(text: str, limit: int = _DISCORD_MESSAGE_LIMIT) -> List[str]:
    """Split ``text`` into chunks ≤ ``limit`` chars, preferring paragraph
    breaks then newlines, and falling back to hard slicing only when a
    single line exceeds the limit (rare for chat-style replies).

    This is exposed publicly because the same logic is useful for any
    Discord-shaped sink — webhooks, transcripts, etc.
    """
    if len(text) <= limit:
        return [text]
    # Prefer paragraph splits; if a paragraph still doesn't fit, split on
    # newlines; if a line still doesn't fit, hard-slice.
    chunks: List[str] = []
    buf = ""
    for para in text.split("\n\n"):
        candidate = (buf + "\n\n" + para) if buf else para
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(para) <= limit:
            buf = para
            continue
        # Long paragraph — split on lines, then hard slice.
        for line in para.split("\n"):
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            cand2 = (buf + "\n" + line) if buf else line
            if len(cand2) <= limit:
                buf = cand2
            else:
                chunks.append(buf)
                buf = line
    if buf:
        chunks.append(buf)
    return chunks


class DiscordChannel:
    """Discord bot adapter. Constructable without ``discord.py`` installed;
    only ``run()`` requires the dep so config validation can run anywhere.

    Args:
        token: Discord bot token (from the developer portal).
        agent_handler: Async callable ``(message, user_id) -> reply``.
        command_prefix: Legacy text-command prefix. Slash commands work
            independently of this.
        intents: Optional pre-configured ``discord.Intents``. The default
            requests just what's needed for mentions + slash commands.
    """

    def __init__(
        self,
        token: str,
        agent_handler: AgentHandler,
        command_prefix: str = "!",
        intents: Optional[Any] = None,
    ) -> None:
        if not token:
            raise ValueError("Discord bot token cannot be empty")
        self._token = token
        self._agent_handler = agent_handler
        self._command_prefix = command_prefix
        self._intents = intents
        self._bot: Optional[Any] = None  # discord.ext.commands.Bot

    def _build_bot(self) -> Any:
        if not _DISCORD_AVAILABLE:
            raise RuntimeError(
                "discord.py is not installed. "
                "Install with `pip install discord.py`."
            )
        intents = self._intents
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True  # required to read mentions

        bot = commands.Bot(command_prefix=self._command_prefix, intents=intents)

        @bot.event
        async def on_ready():
            logger.info("Discord bot ready as %s", bot.user)

        @bot.event
        async def on_message(message):
            # Ignore self and other bots.
            if message.author == bot.user or message.author.bot:
                return
            # Only respond to direct mentions or DMs (avoid channel spam).
            mentioned = bot.user in message.mentions
            is_dm = isinstance(message.channel, discord.DMChannel)
            if not (mentioned or is_dm):
                # Still let registered commands run.
                await bot.process_commands(message)
                return

            content = message.content
            if mentioned:
                # Strip the mention itself before forwarding.
                content = content.replace(f"<@{bot.user.id}>", "").strip()
                content = content.replace(f"<@!{bot.user.id}>", "").strip()
            if not content:
                return

            user_id = f"discord:{message.channel.id}"
            try:
                reply = await self._agent_handler(content, user_id)
            except Exception as e:
                logger.warning("Discord handler failed", exc_info=e)
                reply = f"Sorry — agent error: {type(e).__name__}"

            for chunk in chunk_for_discord(reply or "(empty response)"):
                await message.channel.send(chunk)

            await bot.process_commands(message)

        @bot.command(name="ask")
        async def ask(ctx, *, question: str = ""):
            """``!ask <question>`` — explicit command form."""
            if not question:
                await ctx.send("Usage: `!ask <your question>`")
                return
            user_id = f"discord:{ctx.channel.id}"
            try:
                reply = await self._agent_handler(question, user_id)
            except Exception as e:
                logger.warning("Discord !ask failed", exc_info=e)
                reply = f"Sorry — agent error: {type(e).__name__}"
            for chunk in chunk_for_discord(reply or "(empty response)"):
                await ctx.send(chunk)

        return bot

    async def run(self) -> None:
        """Start the bot. Blocks until shutdown.

        For embedding inside an existing event loop, prefer
        ``await self.start()`` (Discord's own pattern via ``bot.start``).
        """
        if not _DISCORD_AVAILABLE:
            raise RuntimeError(
                "discord.py is not installed. "
                "Install with `pip install discord.py`."
            )
        self._bot = self._build_bot()
        await self._bot.start(self._token)

    async def stop(self) -> None:
        if self._bot is not None:
            try:
                await self._bot.close()
            except Exception as e:
                logger.debug("Discord bot close failed", exc_info=e)
