"""
ResponseHandler — formats and streams responses back to the user.

Extracted from Agent (Phase 4.7 decomposition).
"""

from typing import Any, AsyncIterator


class ResponseHandler:
    """Handles response formatting, streaming, and final delivery."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def stream(
        self,
        response_chunks: AsyncIterator[str],
    ) -> AsyncIterator[str]:
        """Yield formatted response chunks to the caller."""
        # TODO: extract streaming logic from Agent.stream_think()
        async for chunk in response_chunks:
            yield chunk
