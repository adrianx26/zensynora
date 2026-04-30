"""Tests for the Sprint 11 tenancy scoping helpers + Memory wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from myclaw.tenancy import (
    DEFAULT_USER_ID,
    UserContext,
    effective_user_id,
    require_authenticated_user,
    scope_audit_event,
    user_scope,
)


# ── effective_user_id ────────────────────────────────────────────────────


def test_effective_returns_default_when_unscoped():
    assert effective_user_id() == DEFAULT_USER_ID


def test_effective_returns_explicit_when_provided():
    """Explicit beats context — useful for background jobs that pin a user."""
    with user_scope(UserContext("ctx-bob")):
        assert effective_user_id("alice") == "alice"


def test_effective_falls_back_to_context():
    with user_scope(UserContext("ctx-bob")):
        assert effective_user_id() == "ctx-bob"


def test_effective_empty_string_treated_as_unset():
    """Empty / falsy explicit ⇒ same as not passing it.

    This guards against handlers that pass a possibly-blank header value
    straight through and expect the context to win. Without this, a blank
    header would silently override the authenticated user."""
    with user_scope(UserContext("ctx-bob")):
        assert effective_user_id("") == "ctx-bob"
        assert effective_user_id(None) == "ctx-bob"


# ── require_authenticated_user ───────────────────────────────────────────


def test_require_raises_when_unscoped():
    with pytest.raises(PermissionError, match="No authenticated user"):
        require_authenticated_user()


def test_require_returns_active_context():
    with user_scope(UserContext("alice", scopes={"kb.read"})):
        u = require_authenticated_user()
    assert u.user_id == "alice"
    assert u.has_scope("kb.read")


# ── scope_audit_event ─────────────────────────────────────────────────────


def test_scope_audit_event_default_user_when_unscoped():
    fields = scope_audit_event()
    # Default user is hashed too; we don't accidentally leak DEFAULT_USER_ID
    # by writing it raw into audit logs.
    assert fields["user_id"].startswith("user:")


def test_scope_audit_event_includes_tenant_and_scopes():
    with user_scope(UserContext("alice", scopes={"a", "b"}, tenant_id="acme")):
        fields = scope_audit_event()
    assert fields["tenant_id"] == "acme"
    assert fields["scopes"] == ["a", "b"]  # sorted for determinism


def test_scope_audit_event_explicit_overrides_context():
    with user_scope(UserContext("ctx-bob")):
        fields = scope_audit_event("alice")
    # The explicit id is what gets hashed, not the context.
    fields_explicit_alice = scope_audit_event("alice")
    assert fields["user_id"] == fields_explicit_alice["user_id"]


def test_scope_audit_event_can_disable_hashing():
    """Compliance environments may need raw ids in audit logs."""
    with user_scope(UserContext("alice")):
        fields = scope_audit_event(hash_user_id=False)
    assert fields["user_id"] == "alice"


def test_scope_audit_event_omits_optional_fields_when_absent():
    """No tenant_id / no scopes ⇒ those keys aren't in the dict."""
    with user_scope(UserContext("alice")):
        fields = scope_audit_event()
    assert "tenant_id" not in fields
    assert "scopes" not in fields


# ── Memory wiring — uses effective_user_id when no explicit user_id ──────


def test_memory_default_user_id_when_unscoped():
    """Without a UserContext, Memory keeps the historical 'default' user."""
    from myclaw.memory import Memory
    mem = Memory()
    assert mem.user_id == "default"
    assert "memory_default.db" in str(mem.db)


def test_memory_picks_up_user_context():
    """Inside a user_scope, Memory() with no args isolates per-tenant."""
    from myclaw.memory import Memory
    with user_scope(UserContext("alice")):
        mem = Memory()
    assert mem.user_id == "alice"
    assert "memory_alice.db" in str(mem.db)


def test_memory_explicit_user_id_wins_over_context():
    """Explicit ``user_id="bob"`` overrides the active context (background
    jobs that legitimately serve another user)."""
    from myclaw.memory import Memory
    with user_scope(UserContext("alice")):
        mem = Memory(user_id="bob")
    assert mem.user_id == "bob"


def test_memory_user_id_attribute_publicly_accessible():
    """Sprint 11 added the ``user_id`` attribute. Lock it as part of the
    Memory public surface so audit/diagnostics code can read it."""
    from myclaw.memory import Memory
    mem = Memory(user_id="alice")
    assert mem.user_id == "alice"
