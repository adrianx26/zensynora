"""Internal helpers for the Agent orchestrator.

This package exists to keep ``myclaw/agent.py`` thin. Each module here
implements one phase of the request lifecycle as a free function that
takes the ``Agent`` as its first argument. ``Agent`` keeps thin wrappers
that delegate, so the public API is unchanged.

Why free functions instead of proper classes? Speed of refactor. The
original methods reach into ~30 different ``self.X`` attributes; rather
than thread every dependency through a class constructor, we accept a
parameter named ``agent`` and translate ``self.X`` → ``agent.X`` 1:1.
A future iteration can tighten the surface once the behavior is stable.

Naming: this package is ``agent_internals``, not ``agent``, because
``myclaw/agent.py`` is also a file at the same level. A package named
``agent`` would shadow it (this was the pre-existing bug fixed in the
2026-04-30 sprint when we deleted the empty stub package).
"""

from .router import route_message
from .context_builder import build_message_context
from .tool_executor import execute_tools
from .classes import (
    MessageRouter,
    ContextBuilder,
    ToolExecutor,
    ResponseHandler,
)

__all__ = [
    # Free functions (Sprint 5)
    "route_message",
    "build_message_context",
    "execute_tools",
    # Class-based DI (Sprint 9)
    "MessageRouter",
    "ContextBuilder",
    "ToolExecutor",
    "ResponseHandler",
]
