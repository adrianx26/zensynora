"""
Advanced Context Window Manager

Provides enhanced context window capabilities:
- Support for 128k+ token contexts
- Intelligent context summarization
- Sliding window context management
- Token tracking and optimization
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)


CONTEXT_WINDOW_DEFAULTS = {
    "small": 4096,
    "medium": 16384,
    "large": 32768,
    "xlarge": 65536,
    "xxlarge": 128000,
    "max": 200000,
}


@dataclass
class ContextWindow:
    """Configuration for context window."""

    max_tokens: int
    model: str
    actual_limit: int = 0

    def __post_init__(self):
        if self.actual_limit == 0:
            self.actual_limit = self._get_limit_for_model(self.model)

    def _get_limit_for_model(self, model: str) -> int:
        model_lower = model.lower()

        if "gpt-4o" in model_lower or "4o" in model_lower:
            return 128000
        if "gpt-4-turbo" in model_lower or "turbo" in model_lower:
            return 128000
        if "claude-3" in model_lower or "claude-3-5" in model_lower:
            return 200000
        if "claude-3-5" in model_lower:
            return 200000
        if "gemini" in model_lower and "1.5" in model_lower:
            return 200000
        if "llama" in model_lower and "70b" in model_lower:
            return 128000
        if "mixtral" in model_lower or "8x7b" in model_lower:
            return 32768

        return self.max_tokens

    def effective_limit(self) -> int:
        """Get effective token limit."""
        model_limit = self._get_limit_for_model(self.model)
        return min(self.max_tokens, model_limit)


@dataclass
class MessageToken:
    """A message with token tracking."""

    role: str
    content: str
    tokens: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tokens": self.tokens,
            "timestamp": self.timestamp.isoformat(),
        }


class TokenCounter:
    """Token counting utility.

    PERF FIX (2026-04-23): Uses tiktoken for OpenAI models when available.
    Falls back to a better heuristic (~3 chars/token) instead of the old
    inaccurate ~4 chars/token. Added per-provider tokenizer mapping.
    """

    CHARS_PER_TOKEN = 3  # Better average for English text

    # Mapping of provider/model patterns to tokenizer names
    _TOKENIZER_MAP = {
        "gpt-4o": "o200k_base",
        "gpt-4": "cl100k_base",
        "gpt-3.5": "cl100k_base",
        "claude": None,  # No public tokenizer; use heuristic
        "gemini": None,
        "llama": None,
    }

    @classmethod
    def _get_tiktoken_encoder(cls, model: str = ""):
        """Lazy-load tiktoken encoder if available."""
        try:
            import tiktoken

            model_lower = model.lower()
            for pattern, enc_name in cls._TOKENIZER_MAP.items():
                if enc_name and pattern in model_lower:
                    return tiktoken.get_encoding(enc_name)
            # Default fallback
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None

    @classmethod
    def estimate_tokens(cls, text: str, model: str = "") -> int:
        """Estimate token count for text.

        Uses tiktoken for OpenAI models when available.
        Falls back to ~3 characters per token heuristic.
        """
        if not text:
            return 0

        encoder = cls._get_tiktoken_encoder(model)
        if encoder is not None:
            try:
                return len(encoder.encode(text))
            except Exception:
                pass

        return len(text) // cls.CHARS_PER_TOKEN

    @classmethod
    def estimate_messages_tokens(cls, messages: List[Dict[str, str]], model: str = "") -> int:
        """Estimate tokens for a message list.

        OpenAI uses approximately:
        - 3 tokens per message for framing
        - plus content tokens
        """
        if not messages:
            return 0

        encoder = cls._get_tiktoken_encoder(model)

        total = 0
        for msg in messages:
            total += 3  # message framing
            if "role" in msg:
                total += 1 if encoder else len(msg["role"])
            if "name" in msg:
                total += 1 if encoder else len(msg["name"])
            if "content" in msg:
                content = msg["content"] or ""
                if encoder is not None:
                    try:
                        total += len(encoder.encode(content))
                    except Exception:
                        total += len(content) // cls.CHARS_PER_TOKEN
                else:
                    total += len(content) // cls.CHARS_PER_TOKEN
            if "tool_calls" in msg:
                total += cls.estimate_tokens(str(msg["tool_calls"]), model=model)
            if "tool_call_id" in msg:
                total += len(msg["tool_call_id"])

        return total


@dataclass
class ContextSummary:
    """Summary of context for compression."""

    original_tokens: int
    summarized_tokens: int
    messages_kept: int
    compression_ratio: float
    topic: str = ""
    key_points: List[str] = field(default_factory=list)


class AdvancedContextManager:
    """Manages large context windows with optimization.

    Features:
    - Automatic context summarization
    - Sliding window management
    - Token budget tracking
    - Priority-based message retention
    """

    def __init__(
        self, max_tokens: int = 128000, model: str = "gpt-4o", summarizer: Optional[Callable] = None
    ):
        self.max_tokens = max_tokens
        self.model = model
        self._summarizer = summarizer
        self._messages: List[MessageToken] = []
        self._system_prompt_tokens = 0
        self._token_usage = 0

    def set_system_prompt(self, prompt: str, token_count: Optional[int] = None):
        """Set the system prompt and track tokens."""
        self._system_prompt_tokens = token_count or TokenCounter.estimate_tokens(prompt)

    def add_message(self, role: str, content: str):
        """Add a message to context."""
        tokens = TokenCounter.estimate_tokens(content)
        self._messages.append(MessageToken(role=role, content=content, tokens=tokens))
        self._token_usage += tokens

    def get_messages(self) -> List[Dict[str, str]]:
        """Get messages in provider format."""
        return [{"role": msg.role, "content": msg.content} for msg in self._messages]

    def get_token_count(self) -> int:
        """Get total token count."""
        return self._system_prompt_tokens + self._token_usage

    def get_token_budget(self) -> int:
        """Get remaining token budget."""
        return self.max_tokens - self.get_token_count()

    def fit_within_limit(self, additional_tokens: int = 0) -> bool:
        """Check if context fits within limit."""
        return self.get_token_count() + additional_tokens <= self.max_tokens

    def optimize_context(
        self, preserve_recent: int = 10, preserve_system: bool = True
    ) -> ContextSummary:
        """Optimize context to fit within limit.

        Uses summarization and sliding window to reduce context size.

        Args:
            preserve_recent: Number of recent messages to preserve
            preserve_system: Whether to preserve system prompt

        Returns:
            ContextSummary with optimization details
        """
        original_tokens = self.get_token_count()

        if self.fit_within_limit():
            return ContextSummary(
                original_tokens=original_tokens,
                summarized_tokens=original_tokens,
                messages_kept=len(self._messages),
                compression_ratio=1.0,
            )

        messages_to_summarize = len(self._messages) - preserve_recent
        if messages_to_summarize < 0:
            messages_to_summarize = 0

        summarized_content = ""

        if messages_to_summarize > 0 and self._summarizer:
            old_messages = self._messages[: -preserve_recent or None]
            old_text = "\n\n".join(f"{m.role}: {m.content}" for m in old_messages)

            try:
                summarized_content = self._summarizer(old_text)
            except Exception as e:
                logger.error(f"Summarization error: {e}")
                summarized_content = f"[{messages_to_summarize} messages summarized]"

        if summarized_content:
            self._messages = [
                MessageToken(
                    role="system",
                    content=f"Previous context summary: {summarized_content}",
                    tokens=TokenCounter.estimate_tokens(summarized_content),
                )
            ] + self._messages[-preserve_recent:]
        else:
            self._messages = self._messages[-preserve_recent:]

        self._token_usage = sum(m.tokens for m in self._messages)

        new_tokens = self.get_token_count()

        return ContextSummary(
            original_tokens=original_tokens,
            summarized_tokens=new_tokens,
            messages_kept=len(self._messages),
            compression_ratio=new_tokens / original_tokens if original_tokens > 0 else 1.0,
        )

    def clear(self):
        """Clear all messages."""
        self._messages.clear()
        self._token_usage = 0


def create_context_manager(model: str, max_tokens: Optional[int] = None) -> AdvancedContextManager:
    """Create a context manager for the given model.

    Args:
        model: Model name
        max_tokens: Optional max tokens (auto-detected if None)

    Returns:
        Configured AdvancedContextManager
    """
    if max_tokens is None:
        max_tokens = CONTEXT_WINDOW_DEFAULTS.get("xxlarge", 128000)

    return AdvancedContextManager(max_tokens=max_tokens, model=model)


def get_model_context_limit(model: str) -> int:
    """Get the context limit for a model.

    Args:
        model: Model name

    Returns:
        Maximum token limit
    """
    model_lower = model.lower()

    if "gpt-4o" in model_lower:
        return 128000
    if "claude" in model_lower:
        return 200000
    if "gemini" in model_lower:
        return 200000
    if "llama" in model_lower:
        return 128000

    return 4096


__all__ = [
    "ContextWindow",
    "MessageToken",
    "TokenCounter",
    "ContextSummary",
    "AdvancedContextManager",
    "create_context_manager",
    "get_model_context_limit",
    "CONTEXT_WINDOW_DEFAULTS",
]
