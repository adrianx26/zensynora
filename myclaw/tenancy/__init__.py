"""Multi-tenancy primitives.

Storage layers (Memory, KnowledgeDB, audit log, vector store) need a
consistent way to know "who is this request for". This module owns that
contract: a context-var that the request middleware sets and everyone
else reads.

Importing this module never touches storage layers — it's purely a
context primitive. Wiring it into Memory/KnowledgeDB row filters is a
follow-up task tracked in the architecture docs.
"""

from .context import (
    UserContext,
    current_user,
    set_current_user,
    user_scope,
)
from .scoping import (
    DEFAULT_USER_ID,
    effective_user_id,
    require_authenticated_user,
    scope_audit_event,
)

__all__ = [
    "UserContext",
    "current_user",
    "set_current_user",
    "user_scope",
    "DEFAULT_USER_ID",
    "effective_user_id",
    "require_authenticated_user",
    "scope_audit_event",
]
