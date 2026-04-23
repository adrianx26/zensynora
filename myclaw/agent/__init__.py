"""
ZenSynora Agent Subpackage

QUALITY FIX (2026-04-23): Begin decomposition of the monolithic Agent class
(1,665 lines) into focused, testable sub-components:

    - MessageRouter:   routes incoming messages to the right handler
    - ContextBuilder:  assembles conversation context from memory + KB
    - ToolExecutor:    executes tool calls with sandboxing and audit logging
    - ResponseHandler: formats and streams responses back to the user

Phase 1 = create module structure. No behavior changes yet.
"""

from .message_router import MessageRouter
from .context_builder import ContextBuilder
from .tool_executor import ToolExecutor
from .response_handler import ResponseHandler

__all__ = ["MessageRouter", "ContextBuilder", "ToolExecutor", "ResponseHandler"]
