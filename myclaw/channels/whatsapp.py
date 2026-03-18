import asyncio
import hashlib
import hmac
import logging
import json
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn
import requests
from ..agent import Agent
from .. import tools as tool_module

logger = logging.getLogger(__name__)




class _MessageDeduplicator:
    """LRU cache to prevent processing duplicate webhook deliveries.

    WhatsApp may retry webhook POSTs if the server doesn't respond quickly
    enough.  We track recently-seen message IDs and silently skip duplicates.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def is_duplicate(self, message_id: str) -> bool:
        now = time.time()
        # Evict expired entries
        expired = [k for k, ts in self._seen.items() if now - ts > self._ttl]
        for k in expired:
            del self._seen[k]
        # Check
        if message_id in self._seen:
            return True
        # Record
        self._seen[message_id] = now
        # Evict oldest if over capacity
        while len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False


class _RateLimiter:
    """Per-user token-bucket rate limiter.

    Each user gets `max_tokens` tokens that refill at `refill_rate` per second.
    A message costs 1 token.  If the bucket is empty the message is rejected.
    """

    def __init__(self, max_tokens: int = 10, refill_rate: float = 0.5):
        self._max = max_tokens
        self._rate = refill_rate  # tokens per second
        self._buckets: dict[str, tuple[float, float]] = {}  # user -> (tokens, last_ts)

    def allow(self, user_id: str) -> bool:
        now = time.time()
        tokens, last = self._buckets.get(user_id, (float(self._max), now))
        # Refill
        elapsed = now - last
        tokens = min(self._max, tokens + elapsed * self._rate)
        if tokens >= 1.0:
            self._buckets[user_id] = (tokens - 1.0, now)
            return True
        self._buckets[user_id] = (tokens, now)
        return False


class _Metrics:
    """Simple in-memory metrics collector for monitoring."""

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._started = time.time()

    def increment(self, key: str, value: int = 1):
        self._counters[key] = self._counters.get(key, 0) + value

    def get(self, key: str) -> int:
        return self._counters.get(key, 0)

    def all(self) -> dict:
        uptime = time.time() - self._started
        return {
            "uptime_seconds": round(uptime, 2),
            "messages_received": self.get("messages_received"),
            "messages_sent": self.get("messages_sent"),
            "commands_executed": self.get("commands_executed"),
            "media_processed": self.get("media_processed"),
            "errors": self.get("errors"),
            "rate_limited": self.get("rate_limited"),
            "duplicates_skipped": self.get("duplicates_skipped"),
        }


class WhatsAppChannel:
    """WhatsApp channel with multi-agent routing, scheduling, and dynamic tools."""

    def __init__(self, config, registry: dict):
        self.phone_number_id = config.channels.whatsapp.phone_number_id
        self.business_account_id = config.channels.whatsapp.business_account_id
        self.access_token = config.channels.whatsapp.access_token.get_secret_value()
        self.verify_token = config.channels.whatsapp.verify_token.get_secret_value()
        self.app_secret = getattr(config.channels.whatsapp, 'app_secret', None)
        if self.app_secret and hasattr(self.app_secret, 'get_secret_value'):
            self.app_secret = self.app_secret.get_secret_value()
        self.allow_from = config.channels.whatsapp.allowFrom
        self.registry = registry
        self._dedup = _MessageDeduplicator()
        self._limiter = _RateLimiter(max_tokens=10, refill_rate=0.5)
        self._metrics = _Metrics()

        # Initialize FastAPI app for webhook
        self.app = FastAPI()
        self._setup_routes()

        # Base URL for WhatsApp API
        self.api_base_url = f"https://graph.facebook.com/v18.0/{self.phone_number_id}"

    # ── Webhook signature verification ────────────────────────────────────────

    def _verify_signature(self, payload: bytes, signature_header: str | None) -> bool:
        """Validate X-Hub-Signature-256 from Meta webhook.

        Returns True if signature is valid or if app_secret is not configured
        (opt-in security).
        """
        if not self.app_secret:
            return True  # Signature verification not configured — allow
        if not signature_header:
            logger.warning("Missing X-Hub-Signature-256 header")
            return False
        # Header format: "sha256=<hex>"
        if not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(
            self.app_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        received = signature_header[7:]  # strip "sha256="
        return hmac.compare_digest(expected, received)

    def _setup_routes(self):
        """Setup FastAPI routes for webhook verification and message handling."""

        @self.app.get("/webhook")
        async def verify_webhook(request: Request):
            return self._verify_webhook(request)

        @self.app.post("/webhook")
        async def handle_webhook(request: Request):
            # Read raw body for signature verification
            body = await request.body()
            sig = request.headers.get("X-Hub-Signature-256")
            if not self._verify_signature(body, sig):
                logger.warning("Invalid webhook signature — rejecting request")
                raise HTTPException(status_code=403, detail="Invalid signature")
            data = json.loads(body)
            await self._handle_webhook_data(data)
            return {"status": "ok"}

        @self.app.get("/health")
        async def health_check():
            return {
                "status": "ok",
                "channel": "whatsapp",
                "agents": list(self.registry.keys()),
                "phone_number_id": self.phone_number_id,
                "metrics": self._metrics.all(),
            }

    def _verify_webhook(self, request: Request):
        """Verify the webhook with Facebook's challenge."""
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == self.verify_token:
            logger.info("WhatsApp webhook verified successfully")
            return PlainTextResponse(challenge)
        else:
            logger.warning("WhatsApp webhook verification failed")
            raise HTTPException(status_code=403, detail="Forbidden")

    async def _handle_webhook_data(self, data: dict):
        """Handle incoming webhook events from WhatsApp."""
        try:
            logger.debug(f"Received webhook data: {json.dumps(data, indent=2)}")

            if data.get("entry"):
                for entry in data["entry"]:
                    if entry.get("changes"):
                        for change in entry["changes"]:
                            value = change.get("value", {})
                            if value.get("messages"):
                                contacts = value.get("contacts", [{}])
                                wa_id = contacts[0].get("wa_id", "") if contacts else ""
                                for message in value["messages"]:
                                    await self._handle_message(wa_id, message)
        except Exception as e:
            logger.error(f"Error handling webhook: {e}")

    async def _handle_message(self, user_id: str, message: dict):
        """Handle incoming messages from WhatsApp."""
        # ── Deduplication ─────────────────────────────────────────────────────
        msg_id = message.get("id", "")
        if msg_id and self._dedup.is_duplicate(msg_id):
            logger.debug(f"Duplicate message {msg_id} — skipping")
            self._metrics.increment("duplicates_skipped")
            return

        # ── Access control ────────────────────────────────────────────────────
        if user_id not in self.allow_from:
            logger.warning(f"Message from disallowed user: {user_id}")
            return

        # ── Rate limiting ─────────────────────────────────────────────────────
        if not self._limiter.allow(user_id):
            logger.warning(f"Rate limit exceeded for user {user_id}")
            self._metrics.increment("rate_limited")
            await self._send_message(user_id, "⏳ Too many messages. Please wait a moment.")
            return

        # ── Track received ────────────────────────────────────────────────────
        self._metrics.increment("messages_received")
        
        # ── Mark as read (send read receipt) ─────────────────────────────────
        await self._mark_as_read(msg_id)

        msg_type = message.get("type", "")

        # ── Text messages ─────────────────────────────────────────────────────
        if msg_type == "text":
            text = message["text"]["body"]

            # Store user_id for notifications
            tool_module.register_chat_id(user_id, user_id)

            # Check for /command prefix
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                command = parts[0] if parts else ""
                args = parts[1].split() if len(parts) > 1 else []
                await self._handle_command(user_id, command, args)
                return

            # Route to appropriate agent
            agent, cleaned = self._route(text)

            try:
                # Send typing indicator
                await self._send_typing_indicator(user_id)

                # Get agent response
                response = await agent.think(cleaned, user_id=user_id)

                # Send response back to user
                await self._send_message(user_id, response)
            except Exception as e:
                logger.error(f"Error handling message from {user_id}: {e}")
                await self._send_message(user_id, f"Error: {e}")

        # ── Media messages (image, document, audio, video, sticker) ───────────
        elif msg_type in ("image", "document", "audio", "video", "sticker"):
            await self._handle_media_message(user_id, message, msg_type)

        # ── Location messages ─────────────────────────────────────────────────
        elif msg_type == "location":
            loc = message.get("location", {})
            lat, lon = loc.get("latitude", "?"), loc.get("longitude", "?")
            name = loc.get("name", "")
            text = f"[Location: {lat}, {lon}]"
            if name:
                text += f" ({name})"
            tool_module.register_chat_id(user_id, user_id)
            agent = self.registry["default"]
            try:
                response = await agent.think(text, user_id=user_id)
                await self._send_message(user_id, response)
            except Exception as e:
                logger.error(f"Error handling location from {user_id}: {e}")

        # ── Unsupported types ─────────────────────────────────────────────────
        else:
            logger.warning(f"Received unsupported message type: {msg_type}")
            await self._send_message(
                user_id,
                f"Sorry, I don't support '{msg_type}' messages yet. "
                "Send /help to see what I can do."
            )

    # ── Media handling ────────────────────────────────────────────────────────

    async def _handle_media_message(self, user_id: str, message: dict, msg_type: str):
        """Download media metadata and forward a description to the agent."""
        media = message.get(msg_type, {})
        mime = media.get("mime_type", "unknown")
        media_id = media.get("id", "")
        caption = media.get("caption", "")
        filename = media.get("filename", "")

        description = f"[{msg_type.upper()}] mime={mime}"
        if filename:
            description += f" file={filename}"
        if caption:
            description += f" caption: {caption}"

        tool_module.register_chat_id(user_id, user_id)
        agent = self.registry["default"]
        try:
            response = await agent.think(description, user_id=user_id)
            await self._send_message(user_id, response)
        except Exception as e:
            logger.error(f"Error handling {msg_type} from {user_id}: {e}")
            await self._send_message(user_id, f"Error processing {msg_type}: {e}")

    # ── Read receipts ─────────────────────────────────────────────────────────

    async def _mark_as_read(self, message_id: str):
        """Mark a message as read (send read receipt)."""
        url = f"{self.api_base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        
        try:
            response = await asyncio.to_thread(
                requests.post, url, headers=headers, json=data
            )
            response.raise_for_status()
            logger.debug(f"Marked message {message_id} as read: {response.text}")
        except Exception as e:
            logger.error(f"Error marking message as read: {e}")

    def _route(self, text: str) -> tuple:
        """Parse @agentname prefix. Returns (agent, cleaned_text)."""
        if text.startswith("@"):
            parts = text.split(None, 1)
            name = parts[0][1:]   # strip the @
            cleaned = parts[1] if len(parts) > 1 else ""
            agent = self.registry.get(name) or self.registry["default"]
            return agent, cleaned
        return self.registry["default"], text

    async def _send_typing_indicator(self, user_id: str):
        """Send typing indicator to WhatsApp user."""
        url = f"{self.api_base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": user_id,
            "type": "reaction",
            "reaction": {
                "message_id": "",
                "emoji": ""
            }
        }
        try:
            # Note: WhatsApp doesn't have a typing indicator like Telegram,
            # so we'll just send a quick "typing..." message or use reaction
            await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                json=data
            )
        except Exception as e:
            logger.error(f"Error sending typing indicator to {user_id}: {e}")

    async def _send_message(self, user_id: str, text: str):
        """Send a text message to a WhatsApp user."""
        url = f"{self.api_base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        # Split message into chunks if it exceeds WhatsApp's maximum length (4096 characters)
        chunks = self._split_message(text)
        
        for chunk in chunks:
            data = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": user_id,
                "type": "text",
                "text": {
                    "body": chunk
                }
            }
            
            try:
                response = await asyncio.to_thread(
                    requests.post,
                    url,
                    headers=headers,
                    json=data
                )
                response.raise_for_status()
                self._metrics.increment("messages_sent")
                logger.debug(f"Message sent to {user_id}: {response.text}")
            except Exception as e:
                self._metrics.increment("errors")
                logger.error(f"Error sending message to {user_id}: {e}")

    def _split_message(self, text: str, max_length: int = 4096) -> list:
        """Split a long message into chunks that fit WhatsApp's character limit."""
        chunks = []
        while len(text) > max_length:
            # Find the last newline or space before max_length to split cleanly
            split_index = text.rfind("\n", 0, max_length)
            if split_index == -1:
                split_index = text.rfind(" ", 0, max_length)
                if split_index == -1:
                    split_index = max_length
            
            chunks.append(text[:split_index].strip())
            text = text[split_index:].strip()
        
        if text:
            chunks.append(text.strip())
        
        return chunks

    async def _send_command_response(self, user_id: str, response: str):
        """Send command response with appropriate formatting."""
        await self._send_message(user_id, response)

    # ── Interactive messages (buttons & lists) ─────────────────────────────────

    async def _send_interactive_buttons(
        self,
        user_id: str,
        body: str,
        buttons: list[dict],
        header: str | None = None,
        footer: str | None = None,
    ):
        """Send interactive buttons message.
        
        buttons: [{"id": "btn1", "title": "Label 1"}, ...]
        """
        url = f"{self.api_base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        action = {"buttons": [{"id": b["id"], "title": b["title"][:20]} for b in buttons[:3]]}
        msg = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": user_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": action,
            },
        }
        if header:
            msg["interactive"]["header"] = {"type": "text", "text": header[:60]}
        if footer:
            msg["interactive"]["footer"] = {"text": footer[:60]}
        
        try:
            response = await asyncio.to_thread(
                requests.post, url, headers=headers, json=msg
            )
            response.raise_for_status()
            self._metrics.increment("messages_sent")
            logger.debug(f"Buttons sent to {user_id}: {response.text}")
        except Exception as e:
            self._metrics.increment("errors")
            logger.error(f"Error sending buttons to {user_id}: {e}")

    async def _send_interactive_list(
        self,
        user_id: str,
        body: str,
        sections: list[dict],
        title: str | None = None,
        button: str = "Select an option",
    ):
        """Send interactive list message.
        
        sections: [{"title": "Section 1", "rows": [{"id": "opt1", "title": "Option 1", "description": "..."}, ...]}, ...]
        """
        url = f"{self.api_base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        msg = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": user_id,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body[:1024]},
                "action": {
                    "button": button[:20],
                    "sections": [
                        {
                            "title": s["title"][:20],
                            "rows": [
                                {"id": r["id"], "title": r["title"][:24], "description": r.get("description", "")[:72]}
                                for r in s.get("rows", [])[:10]
                            ]
                        }
                        for s in sections[:10]
                    ],
                },
            },
        }
        if title:
            msg["interactive"]["header"] = {"type": "text", "title": title[:60]}
        
        try:
            response = await asyncio.to_thread(
                requests.post, url, headers=headers, json=msg
            )
            response.raise_for_status()
            self._metrics.increment("messages_sent")
            logger.debug(f"List sent to {user_id}: {response.text}")
        except Exception as e:
            self._metrics.increment("errors")
            logger.error(f"Error sending list to {user_id}: {e}")

    # ── Template messages (proactive) ────────────────────────────────────────────

    async def send_template_message(
        self,
        user_id: str,
        template_name: str,
        language: str = "en_US",
        components: list[dict] | None = None,
    ):
        """Send a WhatsApp template message (for proactive outreach).
        
        Template must be pre-approved in WhatsApp Business Manager.
        """
        url = f"{self.api_base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        msg = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            msg["template"]["components"] = components
        
        try:
            response = await asyncio.to_thread(
                requests.post, url, headers=headers, json=msg
            )
            response.raise_for_status()
            self._metrics.increment("messages_sent")
            logger.debug(f"Template '{template_name}' sent to {user_id}: {response.text}")
        except Exception as e:
            self._metrics.increment("errors")
            logger.error(f"Error sending template to {user_id}: {e}")

    # ── Business Profile (optional) ───────────────────────────────────────────────

    async def get_business_profile(self, user_id: str) -> dict | None:
        """Fetch a user's WhatsApp business profile info (if available)."""
        # Note: The Business Profile API is limited; this is a placeholder
        # for future implementation if Meta exposes more profile data
        url = f"https://graph.facebook.com/v18.0/{user_id}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }
        params = {"fields": "name,profile"}
        
        try:
            response = await asyncio.to_thread(
                requests.get, url, headers=headers, params=params
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching business profile for {user_id}: {e}")
            return None

    # ── Command handlers ────────────────────────────────────────────────────────
    async def _handle_command(self, user_id: str, command: str, args: list):
        """Handle WhatsApp commands (prefix with /)."""
        self._metrics.increment("commands_executed")
        
        if command == "help":
            await self._help_command(user_id)
        elif command == "remind":
            await self._remind_command(user_id, args)
        elif command == "jobs":
            await self._jobs_command(user_id)
        elif command == "cancel":
            await self._cancel_command(user_id, args)
        elif command == "agents":
            await self._agents_command(user_id)
        elif command == "knowledge_search":
            await self._knowledge_search_command(user_id, args)
        elif command == "knowledge_list":
            await self._knowledge_list_command(user_id)
        elif command == "knowledge_read":
            await self._knowledge_read_command(user_id, args)
        elif command == "knowledge_write":
            await self._knowledge_write_command(user_id, args)
        elif command == "knowledge_sync":
            await self._knowledge_sync_command(user_id)
        elif command == "knowledge_tags":
            await self._knowledge_tags_command(user_id)
        else:
            await self._send_message(
                user_id,
                f"Unknown command: /{command}\nSend /help to see available commands."
            )

    async def _help_command(self, user_id: str):
        """Show all available commands."""
        agents = ", ".join(self.registry.keys())
        await self._send_message(user_id, (
            "🦞 *MyClaw WhatsApp Bot*\n\n"
            "*Chat Commands:*\n"
            "  Just type a message to chat with the default agent.\n"
            "  @agentname <message> — route to a specific agent\n\n"
            "*Scheduling:*\n"
            "  /remind <seconds> <message>\n"
            "  /remind every <seconds> <message>\n"
            "  /jobs — list scheduled jobs\n"
            "  /cancel <job_id> — cancel a job\n\n"
            "*Agents:*\n"
            "  /agents — list available agents\n"
            f"  Available: {agents}\n\n"
            "*Knowledge Base:*\n"
            "  /knowledge_search <query>\n"
            "  /knowledge_list\n"
            "  /knowledge_read <permalink>\n"
            "  /knowledge_write <title> | <content>\n"
            "  /knowledge_sync\n"
            "  /knowledge_tags\n\n"
            "*Other:*\n"
            "  /help — show this message"
        ))

    async def _remind_command(self, user_id: str, args: list):
        """Schedule a reminder.
        Usage: /remind <seconds> <message>
               /remind every <seconds> <message>
        """
        if not args:
            await self._send_message(user_id, (
                "Usage:\n"
                "  /remind <seconds> <message>\n"
                "  /remind every <seconds> <message>"
            ))
            return

        try:
            if args[0].lower() == "every" and len(args) >= 3:
                every = int(args[1])
                task = " ".join(args[2:])
                result = tool_module.schedule(task=task, every=every, user_id=user_id)
            else:
                delay = int(args[0])
                task = " ".join(args[1:])
                result = tool_module.schedule(task=task, delay=delay, user_id=user_id)
            await self._send_message(user_id, f"✅ {result}")
        except (ValueError, IndexError):
            await self._send_message(user_id, "Error: seconds must be a whole number.")

    async def _jobs_command(self, user_id: str):
        """List all active scheduled jobs."""
        await self._send_message(user_id, tool_module.list_schedules())

    async def _cancel_command(self, user_id: str, args: list):
        """Cancel a scheduled job."""
        if not args:
            await self._send_message(user_id, "Usage: /cancel <job_id>")
            return
        await self._send_message(user_id, tool_module.cancel_schedule(args[0]))

    async def _agents_command(self, user_id: str):
        """List available named agents."""
        names = ", ".join(self.registry.keys())
        await self._send_message(user_id, (
            f"🤖 Available agents: {names}\n\n"
            f"To use a specific agent, prefix your message with @name\n"
            f"Example: @coder write a binary search function"
        ))

    async def _knowledge_search_command(self, user_id: str, args: list):
        """Search the knowledge base."""
        if not args:
            await self._send_message(user_id, (
                "🔍 Search the knowledge base\n"
                "Usage: /knowledge_search <query>\n"
                "Example: /knowledge_search project phoenix"
            ))
            return
        
        query = " ".join(args)
        
        try:
            result = tool_module.search_knowledge(query=query, limit=5, user_id=user_id)
            await self._send_message(user_id, result)
        except Exception as e:
            logger.error(f"Knowledge search error: {e}")
            await self._send_message(user_id, f"Error searching: {e}")

    async def _knowledge_list_command(self, user_id: str):
        """List all knowledge notes."""
        try:
            result = tool_module.list_knowledge(limit=20, user_id=user_id)
            await self._send_message(user_id, result)
        except Exception as e:
            logger.error(f"Knowledge list error: {e}")
            await self._send_message(user_id, f"Error listing: {e}")

    async def _knowledge_read_command(self, user_id: str, args: list):
        """Read a specific knowledge note."""
        if not args:
            await self._send_message(user_id, (
                "📖 Read a knowledge note\n"
                "Usage: /knowledge_read <permalink>\n"
                "Example: /knowledge_read project-phoenix"
            ))
            return
        
        permalink = args[0]
        
        try:
            result = tool_module.read_knowledge(permalink=permalink, user_id=user_id)
            await self._send_message(user_id, result)
        except Exception as e:
            logger.error(f"Knowledge read error: {e}")
            await self._send_message(user_id, f"Error reading: {e}")

    async def _knowledge_write_command(self, user_id: str, args: list):
        """Create a new knowledge note."""
        if not args:
            await self._send_message(user_id, (
                "📝 Create a knowledge note\n"
                "Usage: /knowledge_write <title> | <content>\n"
                "Example: /knowledge_write Meeting Notes | Discussed Q2 roadmap..."
            ))
            return
        
        text = " ".join(args)
        if "|" not in text:
            await self._send_message(user_id, "Error: Use | to separate title and content")
            return
        
        title, content = text.split("|", 1)
        title = title.strip()
        content = content.strip()
        
        try:
            result = tool_module.write_to_knowledge(
                title=title,
                content=content,
                user_id=user_id
            )
            await self._send_message(user_id, result)
        except Exception as e:
            logger.error(f"Knowledge write error: {e}")
            await self._send_message(user_id, f"Error writing: {e}")

    async def _knowledge_sync_command(self, user_id: str):
        """Synchronize knowledge base with files."""
        try:
            result = tool_module.sync_knowledge_base(user_id=user_id)
            await self._send_message(user_id, result)
        except Exception as e:
            logger.error(f"Knowledge sync error: {e}")
            await self._send_message(user_id, f"Error syncing: {e}")

    async def _knowledge_tags_command(self, user_id: str):
        """List all knowledge tags."""
        try:
            result = tool_module.list_knowledge_tags(user_id=user_id)
            await self._send_message(user_id, result)
        except Exception as e:
            logger.error(f"Knowledge tags error: {e}")
            await self._send_message(user_id, f"Error listing tags: {e}")

    def _register_notification_callback(self):
        """Register a notification callback so scheduled jobs can send WhatsApp messages."""
        channel = self  # capture reference for closure

        async def _whatsapp_notify(user_id: str, message: str):
            await channel._send_message(user_id, message)

        tool_module.set_notification_callback(_whatsapp_notify)

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        """Run the WhatsApp channel using FastAPI with uvicorn."""
        self._register_notification_callback()
        print("🦞 MyClaw WhatsApp gateway started.")
        print(f"   Agents: {', '.join(self.registry.keys())}")
        print("   Commands: /remind /jobs /cancel /agents")
        print("   Knowledge: /knowledge_search /knowledge_list /knowledge_read /knowledge_write /knowledge_sync /knowledge_tags")
        print(f"   Webhook server running on http://{host}:{port}")
        print(f"   Webhook URL: http://{host}:{port}/webhook")
        
        uvicorn.run(
            self.app,
            host=host,
            port=port,
            log_level="info"
        )

    def run_webhook(self, webhook_url: str, port: int = 8443):
        """Run the WhatsApp channel in webhook mode with HTTPS support."""
        print("🦞 MyClaw WhatsApp gateway started in WEBHOOK mode.")
        print(f"   Webhook URL: {webhook_url}")
        print(f"   Port: {port}")
        print(f"   Agents: {', '.join(self.registry.keys())}")
        
        uvicorn.run(
            self.app,
            host="0.0.0.0",
            port=port,
            log_level="info"
        )
