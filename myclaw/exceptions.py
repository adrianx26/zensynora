"""
Centralized exception classes for the MyClaw framework.

This module provides a hierarchy of specific exceptions for better error
handling and debugging across all MyClaw components.

Exception Hierarchy:
-------------------
MyClawError (base)
├── ConfigurationError
│   ├── ConfigValidationError
│   └── ConfigNotFoundError
├── ProviderError
│   ├── ProviderNotFoundError
│   ├── ProviderTimeoutError
│   ├── ProviderConnectionError
│   ├── ProviderAuthenticationError
│   └── ProviderRateLimitError
├── ToolExecutionError
│   ├── RateLimitExceededError
│   ├── ToolNotFoundError
│   ├── ToolValidationError
│   └── ToolPermissionError
├── MemoryError
│   ├── MemoryConnectionError
│   ├── MemoryQueryError
│   └── MemoryCleanupError
├── KnowledgeBaseError
│   ├── KnowledgeNotFoundError
│   ├── KnowledgeParseError
│   └── KnowledgeSyncError
├── AgentRoutingError
│   ├── AgentNotFoundError
│   └── AgentNotAvailableError
├── SwarmError
│   ├── SwarmNotFoundError
│   ├── SwarmTimeoutError
│   ├── SwarmValidationError
│   └── SwarmConcurrencyError
└── ChannelError
    ├── ChannelNotFoundError
    ├── ChannelAuthenticationError
    └── ChannelWebhookError
"""

from typing import Optional, Any


class MyClawError(Exception):
    """Base exception for all MyClaw errors."""
    
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
    
    def __str__(self) -> str:
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message


# ============================================================================
# Configuration Errors
# ============================================================================

class ConfigurationError(MyClawError):
    """Raised when there is an error in configuration loading or validation."""
    pass


class ConfigValidationError(ConfigurationError):
    """Raised when configuration values fail validation."""
    pass


class ConfigNotFoundError(ConfigurationError):
    """Raised when a required configuration file or key is not found."""
    pass


# ============================================================================
# Provider Errors
# ============================================================================

class ProviderError(MyClawError):
    """Raised when an LLM provider encounters an error."""
    pass


class ProviderNotFoundError(ProviderError):
    """Raised when a requested provider is not available or not found."""
    pass


class ProviderTimeoutError(ProviderError):
    """Raised when a provider request times out."""
    
    def __init__(self, message: str, timeout_seconds: Optional[float] = None, 
                 provider: Optional[str] = None):
        details = {}
        if timeout_seconds is not None:
            details['timeout_seconds'] = timeout_seconds
        if provider:
            details['provider'] = provider
        super().__init__(message, details)


class ProviderConnectionError(ProviderError):
    """Raised when connection to a provider fails."""
    
    def __init__(self, message: str, provider: Optional[str] = None,
                 base_url: Optional[str] = None):
        details = {}
        if provider:
            details['provider'] = provider
        if base_url:
            details['base_url'] = base_url
        super().__init__(message, details)


class ProviderAuthenticationError(ProviderError):
    """Raised when provider authentication fails (invalid API key, etc.)."""
    
    def __init__(self, message: str, provider: Optional[str] = None):
        details = {}
        if provider:
            details['provider'] = provider
        super().__init__(message, details)


class ProviderRateLimitError(ProviderError):
    """Raised when provider rate limit is exceeded."""
    
    def __init__(self, message: str, provider: Optional[str] = None,
                 retry_after: Optional[int] = None):
        details = {}
        if provider:
            details['provider'] = provider
        if retry_after:
            details['retry_after_seconds'] = retry_after
        super().__init__(message, details)


# ============================================================================
# Tool Execution Errors
# ============================================================================

class ToolExecutionError(MyClawError):
    """Raised when a tool execution fails."""
    pass


class RateLimitExceededError(ToolExecutionError):
    """Raised when a tool execution exceeds its rate limit."""
    
    def __init__(self, message: str, tool_name: Optional[str] = None,
                 limit: Optional[int] = None, window_seconds: Optional[int] = None):
        details = {}
        if tool_name:
            details['tool_name'] = tool_name
        if limit:
            details['limit'] = limit
        if window_seconds:
            details['window_seconds'] = window_seconds
        super().__init__(message, details)


class ToolNotFoundError(ToolExecutionError):
    """Raised when a requested tool does not exist."""
    
    def __init__(self, message: str, tool_name: Optional[str] = None,
                 available_tools: Optional[list] = None):
        details = {}
        if tool_name:
            details['tool_name'] = tool_name
        if available_tools:
            details['available_tools'] = available_tools
        super().__init__(message, details)


class ToolValidationError(ToolExecutionError):
    """Raised when tool input validation fails."""
    
    def __init__(self, message: str, tool_name: Optional[str] = None,
                 validation_errors: Optional[dict] = None):
        details = {}
        if tool_name:
            details['tool_name'] = tool_name
        if validation_errors:
            details['validation_errors'] = validation_errors
        super().__init__(message, details)


class ToolPermissionError(ToolExecutionError):
    """Raised when tool execution is not allowed (blocked command, path traversal, etc.)."""
    
    def __init__(self, message: str, tool_name: Optional[str] = None,
                 reason: Optional[str] = None):
        details = {}
        if tool_name:
            details['tool_name'] = tool_name
        if reason:
            details['reason'] = reason
        super().__init__(message, details)


# ============================================================================
# Memory Errors
# ============================================================================

class MemoryError(MyClawError):
    """Raised when there is an error in memory operations."""
    pass


class MemoryConnectionError(MemoryError):
    """Raised when memory database connection fails."""
    
    def __init__(self, message: str, db_path: Optional[str] = None):
        details = {}
        if db_path:
            details['db_path'] = db_path
        super().__init__(message, details)


class MemoryQueryError(MemoryError):
    """Raised when a memory database query fails."""
    
    def __init__(self, message: str, query: Optional[str] = None,
                 db_path: Optional[str] = None):
        details = {}
        if query:
            details['query'] = query
        if db_path:
            details['db_path'] = db_path
        super().__init__(message, details)


class MemoryCleanupError(MemoryError):
    """Raised when memory cleanup operation fails."""
    pass


class MemoryValidationError(MemoryError):
    """Raised when memory validation fails."""
    
    def __init__(self, message: str, column: Optional[str] = None,
                 allowed_values: Optional[list] = None):
        details = {}
        if column:
            details['column'] = column
        if allowed_values:
            details['allowed_values'] = allowed_values
        super().__init__(message, details)


# ============================================================================
# Knowledge Base Errors
# ============================================================================

class KnowledgeBaseError(MyClawError):
    """Raised when there is an error in knowledge base operations."""
    pass


class KnowledgeNotFoundError(KnowledgeBaseError):
    """Raised when a knowledge entry is not found."""
    
    def __init__(self, message: str, permalink: Optional[str] = None,
                 user_id: Optional[str] = None):
        details = {}
        if permalink:
            details['permalink'] = permalink
        if user_id:
            details['user_id'] = user_id
        super().__init__(message, details)


class KnowledgeParseError(KnowledgeBaseError):
    """Raised when knowledge parsing fails (invalid markdown, frontmatter, etc.)."""
    
    def __init__(self, message: str, file_path: Optional[str] = None,
                 parse_error: Optional[str] = None):
        details = {}
        if file_path:
            details['file_path'] = file_path
        if parse_error:
            details['parse_error'] = parse_error
        super().__init__(message, details)


class KnowledgeSyncError(KnowledgeBaseError):
    """Raised when knowledge synchronization fails."""
    
    def __init__(self, message: str, file_path: Optional[str] = None,
                 sync_direction: Optional[str] = None):
        details = {}
        if file_path:
            details['file_path'] = file_path
        if sync_direction:
            details['sync_direction'] = sync_direction  # 'to_db', 'to_file'
        super().__init__(message, details)


# ============================================================================
# Agent Routing Errors
# ============================================================================

class AgentRoutingError(MyClawError):
    """Raised when an agent routing fails."""
    pass


class AgentNotFoundError(AgentRoutingError):
    """Raised when a requested agent does not exist."""
    
    def __init__(self, message: str, agent_name: Optional[str] = None,
                 available_agents: Optional[list] = None):
        details = {}
        if agent_name:
            details['agent_name'] = agent_name
        if available_agents:
            details['available_agents'] = available_agents
        super().__init__(message, details)


class AgentNotAvailableError(AgentRoutingError):
    """Raised when an agent exists but is not available (disabled, busy, etc.)."""
    
    def __init__(self, message: str, agent_name: Optional[str] = None,
                 reason: Optional[str] = None):
        details = {}
        if agent_name:
            details['agent_name'] = agent_name
        if reason:
            details['reason'] = reason
        super().__init__(message, details)


# ============================================================================
# Swarm Errors
# ============================================================================

class SwarmError(MyClawError):
    """Raised when a swarm operation fails."""
    pass


class SwarmNotFoundError(SwarmError):
    """Raised when a swarm is not found."""
    
    def __init__(self, message: str, swarm_id: Optional[str] = None):
        details = {}
        if swarm_id:
            details['swarm_id'] = swarm_id
        super().__init__(message, details)


class SwarmTimeoutError(SwarmError):
    """Raised when a swarm operation times out."""
    
    def __init__(self, message: str, swarm_id: Optional[str] = None,
                 timeout_seconds: Optional[float] = None):
        details = {}
        if swarm_id:
            details['swarm_id'] = swarm_id
        if timeout_seconds:
            details['timeout_seconds'] = timeout_seconds
        super().__init__(message, details)


class SwarmValidationError(SwarmError):
    """Raised when swarm configuration validation fails."""
    
    def __init__(self, message: str, swarm_id: Optional[str] = None,
                 validation_errors: Optional[dict] = None):
        details = {}
        if swarm_id:
            details['swarm_id'] = swarm_id
        if validation_errors:
            details['validation_errors'] = validation_errors
        super().__init__(message, details)


class SwarmConcurrencyError(SwarmError):
    """Raised when swarm concurrency limit is reached."""
    
    def __init__(self, message: str, swarm_id: Optional[str] = None,
                 max_concurrent: Optional[int] = None, current_count: Optional[int] = None):
        details = {}
        if swarm_id:
            details['swarm_id'] = swarm_id
        if max_concurrent:
            details['max_concurrent'] = max_concurrent
        if current_count:
            details['current_count'] = current_count
        super().__init__(message, details)


# ============================================================================
# Channel Errors
# ============================================================================

class ChannelError(MyClawError):
    """Raised when a channel operation fails."""
    pass


class ChannelNotFoundError(ChannelError):
    """Raised when a requested channel is not found or not configured."""
    
    def __init__(self, message: str, channel_name: Optional[str] = None,
                 available_channels: Optional[list] = None):
        details = {}
        if channel_name:
            details['channel_name'] = channel_name
        if available_channels:
            details['available_channels'] = available_channels
        super().__init__(message, details)


class ChannelAuthenticationError(ChannelError):
    """Raised when channel authentication fails."""
    
    def __init__(self, message: str, channel_name: Optional[str] = None,
                 auth_type: Optional[str] = None):
        details = {}
        if channel_name:
            details['channel_name'] = channel_name
        if auth_type:
            details['auth_type'] = auth_type
        super().__init__(message, details)


class ChannelWebhookError(ChannelError):
    """Raised when channel webhook handling fails."""
    
    def __init__(self, message: str, channel_name: Optional[str] = None,
                 webhook_url: Optional[str] = None, status_code: Optional[int] = None):
        details = {}
        if channel_name:
            details['channel_name'] = channel_name
        if webhook_url:
            details['webhook_url'] = webhook_url
        if status_code:
            details['status_code'] = status_code
        super().__init__(message, details)
