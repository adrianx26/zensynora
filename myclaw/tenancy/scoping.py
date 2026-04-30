"""Tenancy scoping helpers — bridge ``UserContext`` to storage callsites.

Sprint 6 added the ``UserContext`` primitive but didn't wire it into the
storage layers. This module provides the connector functions storage
callers use to derive a user identity:

* :func:`effective_user_id` — resolve the user_id for a request. Prefers
  the explicit argument; falls back to ``current_user().user_id`` when the
  caller didn't specify one. Returns ``"default"`` when neither is set —
  preserves the historical single-user behavior.

* :func:`require_authenticated_user` — strict variant. Raises if no
  authenticated principal is bound. Use at API boundaries that must not
  serve anonymous traffic.

* :func:`scope_audit_event` — return a dict of audit-log fields keyed by
  the active user. Audit emitters merge this into every record so the
  log can be filtered by tenant after the fact.

Why a separate module: ``context.py`` owns the *primitive* (a contextvar
+ a UserContext dataclass). This file owns the *policy* — what each
storage layer should do when the contextvar is unset, what fields to
attach to audit events, etc. Splitting them keeps the primitive
swap-able (we could replace contextvar with a Redis-backed shared store)
without touching every callsite.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from .context import UserContext, current_user


#: User id used when neither an explicit argument nor a ``UserContext``
#: provides one. Matches the historical Memory default so legacy callers
#: keep working unchanged.
DEFAULT_USER_ID: str = "default"


def effective_user_id(explicit: Optional[str] = None) -> str:
    """Resolve the user id for a storage operation.

    Resolution order:

    1. ``explicit`` argument when truthy.
    2. ``current_user().user_id`` when a context is bound.
    3. :data:`DEFAULT_USER_ID`.

    The order means: code can still pass a user_id arg (fully explicit),
    middleware can bind a context (typical web request), and code that
    does neither still works (CLI, tests, single-user installs).
    """
    if explicit:
        return explicit
    user = current_user()
    if user is not None:
        return user.user_id
    return DEFAULT_USER_ID


def require_authenticated_user() -> UserContext:
    """Return the active :class:`UserContext` or raise.

    Use at API/handler boundaries where serving anonymous traffic would
    leak data across tenants. Internal services that legitimately run
    without a user (background jobs) should use :func:`effective_user_id`
    with an explicit fallback instead.
    """
    user = current_user()
    if user is None:
        raise PermissionError(
            "No authenticated user bound to the current request. "
            "Wrap the handler in a `user_scope(...)` or call "
            "`set_current_user(...)` in your middleware."
        )
    return user


def _hash_user_id(user_id: str) -> str:
    """Stable short hash for log correlation without exposing the raw id."""
    h = hashlib.sha256(user_id.encode("utf-8", errors="replace")).hexdigest()
    return f"user:{h[:10]}"


def scope_audit_event(
    explicit_user_id: Optional[str] = None,
    *,
    hash_user_id: bool = True,
) -> Dict[str, Any]:
    """Return audit-log fields describing the active tenant.

    The audit logger merges this dict into every record. Hashing the
    user id by default matches the Sprint 2 PII scrubber's posture —
    correlation across requests still works, but the raw id never lands
    on disk in audit logs.

    Set ``hash_user_id=False`` for environments that need the raw id for
    compliance reporting.
    """
    user = current_user()
    user_id = explicit_user_id or (user.user_id if user else DEFAULT_USER_ID)
    fields: Dict[str, Any] = {
        "user_id": _hash_user_id(user_id) if hash_user_id else user_id,
    }
    if user is not None:
        if user.tenant_id:
            fields["tenant_id"] = user.tenant_id
        if user.scopes:
            # Sort for deterministic log output — easier diffing.
            fields["scopes"] = sorted(user.scopes)
    return fields
