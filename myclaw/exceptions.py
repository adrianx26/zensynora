"""
Centralized exception classes for the ZenSynora (MyClaw) framework.

QUALITY FIX (2026-04-23): Standardized exception hierarchy with ZenSynoraError
as the base. All modules should use these specific exceptions instead of bare
`except Exception:` blocks.
"""


# Backwards-compatible alias
class ZenSynoraError(Exception):
    """Base exception for all ZenSynora errors."""

    pass


# Legacy alias for backwards compatibility
MyClawError = ZenSynoraError


class ConfigurationError(ZenSynoraError):
    """Raised when there is an error in configuration loading or validation."""

    pass


class ProviderError(ZenSynoraError):
    """Raised when an LLM provider encounters an error."""

    pass


class SecurityError(ZenSynoraError):
    """Raised when a security check fails (auth, sandbox escape, etc.)."""

    pass


class ToolExecutionError(ZenSynoraError):
    """Raised when a tool execution fails."""

    pass


class RateLimitExceededError(ToolExecutionError):
    """Raised when a tool execution exceeds its rate limit."""

    pass


class MemoryError(ZenSynoraError):
    """Raised when there is an error in memory operations."""

    pass


class KnowledgeBaseError(ZenSynoraError):
    """Raised when there is an error in knowledge base operations."""

    pass


class AgentRoutingError(ZenSynoraError):
    """Raised when an agent routing fails."""

    pass


class SwarmError(ZenSynoraError):
    """Raised when a swarm operation fails."""

    pass


class SwarmTimeoutError(SwarmError):
    """Raised when a swarm operation times out."""

    pass
