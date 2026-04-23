"""
ContextBuilder — assembles conversation context from memory + knowledge base.

Extracted from Agent (Phase 4.7 decomposition).
"""

from typing import Any, List, Dict


class ContextBuilder:
    """Builds the full context window for an LLM call."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def build(
        self,
        user_message: str,
        history: List[Dict[str, str]],
        user_id: str,
    ) -> List[Dict[str, str]]:
        """Assemble messages list with system prompt, KB context, and history."""
        # TODO: extract context assembly from Agent._build_context()
        return history
