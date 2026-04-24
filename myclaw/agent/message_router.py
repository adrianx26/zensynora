"""
MessageRouter — routes incoming messages to the appropriate handler.

Extracted from Agent (Phase 4.7 decomposition).
"""

from ._common import Any


class MessageRouter:
    """Routes messages based on content type, provider availability, and config."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def route(self, message: str, user_id: str) -> dict:
        """Determine how to handle an incoming message.

        Returns a dict with routing decision:
            {"handler": "think" | "delegate" | "tool", ...}
        """
        # TODO: extract routing logic from Agent.think() and Agent.stream_think()
        return {"handler": "think"}
