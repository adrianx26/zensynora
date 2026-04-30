"""Per-request user identity, propagated via :class:`contextvars.ContextVar`.

Why ``ContextVar`` instead of thread-local:

* Asyncio tasks each get an independent copy. Threads do too.
* The middleware that sets the user ID need not coordinate with handlers
  to clean up — leaving scope reverts the value automatically.
* Storage callers ask ``current_user()`` without taking the user_id as
  a parameter, so multi-tenancy can be retrofitted to existing APIs
  without breaking signatures.

Example::

    from myclaw.tenancy import user_scope, current_user

    async def write_note(title, content):
        user = current_user()           # the active principal
        # ... persist with user.user_id as a row tag

    # In middleware (FastAPI dependency, channel adapter, etc.):
    async with user_scope(UserContext("alice", scopes={"kb.write"})):
        await some_handler()
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator, Optional, Set


@dataclass(frozen=True)
class UserContext:
    """Immutable per-request identity. Pass-through from the auth layer.

    Frozen so it can't be mutated mid-request — anything that wants to
    "modify" the user must enter a new ``user_scope``.
    """
    user_id: str
    scopes: Set[str] = field(default_factory=set)
    tenant_id: Optional[str] = None  # multi-tenant deployments

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


# Module-private — callers go through the helpers below.
_CURRENT_USER: contextvars.ContextVar[Optional[UserContext]] = contextvars.ContextVar(
    "myclaw_current_user", default=None
)


def current_user() -> Optional[UserContext]:
    """Return the active :class:`UserContext`, or ``None`` if unscoped."""
    return _CURRENT_USER.get()


def set_current_user(user: Optional[UserContext]) -> contextvars.Token:
    """Set the current user. Returns a token suitable for ``reset()``.

    Prefer :func:`user_scope` over this — the context manager guarantees
    the reset happens. Use this only when integrating with frameworks
    that already manage their own scope (FastAPI Depends, etc.).
    """
    return _CURRENT_USER.set(user)


@contextlib.contextmanager
def user_scope(user: Optional[UserContext]) -> Iterator[Optional[UserContext]]:
    """Temporarily bind ``user`` as the active context.

    Exits restore whatever was previously bound (typically ``None``).
    Works in both sync and async code; for async-with use
    :func:`async_user_scope`.
    """
    token = _CURRENT_USER.set(user)
    try:
        yield user
    finally:
        _CURRENT_USER.reset(token)


@contextlib.asynccontextmanager
async def async_user_scope(
    user: Optional[UserContext],
) -> AsyncIterator[Optional[UserContext]]:
    """Async-with version of :func:`user_scope`."""
    token = _CURRENT_USER.set(user)
    try:
        yield user
    finally:
        _CURRENT_USER.reset(token)


def require_scope(scope: str) -> UserContext:
    """Convenience guard: return the current user iff it has ``scope``.

    Raises ``PermissionError`` otherwise. Designed to be called from inside
    storage methods so RBAC can be enforced at the data layer rather than
    only at the API surface.
    """
    user = current_user()
    if user is None:
        raise PermissionError(f"No authenticated user; cannot grant scope {scope!r}")
    if not user.has_scope(scope):
        raise PermissionError(
            f"User {user.user_id!r} lacks required scope {scope!r}"
        )
    return user
