"""
Centralized exception classes for the MyClaw framework.
"""

class MyClawError(Exception):
    """Base exception for all MyClaw errors."""
    pass

class ConfigurationError(MyClawError):
    """Raised when there is an error in configuration loading or validation."""
    pass

class ProviderError(MyClawError):
    """Raised when an LLM provider encounters an error."""
    pass

class ToolExecutionError(MyClawError):
    """Raised when a tool execution fails."""
    pass

class RateLimitExceededError(ToolExecutionError):
    """Raised when a tool execution exceeds its rate limit."""
    pass

class MemoryError(MyClawError):
    """Raised when there is an error in memory operations."""
    pass

class KnowledgeBaseError(MyClawError):
    """Raised when there is an error in knowledge base operations."""
    pass

class AgentRoutingError(MyClawError):
    """Raised when an agent routing fails."""
    pass

class SwarmError(MyClawError):
    """Raised when a swarm operation fails."""
    pass

class SwarmTimeoutError(SwarmError):
    """Raised when a swarm operation times out."""
    pass
