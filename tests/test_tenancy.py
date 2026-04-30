"""Tests for the multi-tenant user-context primitives."""

from __future__ import annotations

import asyncio

import pytest

from myclaw.tenancy import UserContext, current_user, set_current_user, user_scope
from myclaw.tenancy.context import async_user_scope, require_scope


# ── Default state ─────────────────────────────────────────────────────────


def test_no_user_outside_a_scope():
    assert current_user() is None


def test_user_context_is_immutable():
    u = UserContext("alice", scopes={"a"})
    with pytest.raises(Exception):
        u.user_id = "bob"  # type: ignore[misc]


def test_has_scope_helper():
    u = UserContext("alice", scopes={"kb.read"})
    assert u.has_scope("kb.read")
    assert not u.has_scope("kb.write")


# ── Sync scope ────────────────────────────────────────────────────────────


def test_user_scope_sets_and_reverts():
    assert current_user() is None
    with user_scope(UserContext("alice", scopes={"a"})):
        u = current_user()
        assert u is not None and u.user_id == "alice"
    assert current_user() is None


def test_user_scope_nests_correctly():
    with user_scope(UserContext("outer")):
        with user_scope(UserContext("inner")):
            assert current_user().user_id == "inner"
        assert current_user().user_id == "outer"
    assert current_user() is None


def test_user_scope_reverts_on_exception():
    with pytest.raises(RuntimeError, match="boom"):
        with user_scope(UserContext("alice")):
            raise RuntimeError("boom")
    assert current_user() is None


def test_set_current_user_with_token_reset():
    token = set_current_user(UserContext("alice"))
    try:
        assert current_user().user_id == "alice"
    finally:
        # Manual reset — what FastAPI deps do.
        from myclaw.tenancy.context import _CURRENT_USER
        _CURRENT_USER.reset(token)
    assert current_user() is None


# ── Async scope and isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_user_scope_isolates_concurrent_tasks():
    """Each asyncio task gets its own contextvar copy — the canonical
    multi-tenancy invariant. If this test fails, the abstraction is broken
    and concurrent requests will see each other's identities."""

    seen = {}

    async def do_work(name: str, expected: str) -> None:
        # Within this task, we should always see what *this task* set,
        # never what a sibling task set.
        async with async_user_scope(UserContext(name)):
            await asyncio.sleep(0)  # yield to other tasks
            seen[name] = current_user().user_id
            await asyncio.sleep(0)
            assert current_user().user_id == expected

    await asyncio.gather(
        do_work("alice", "alice"),
        do_work("bob", "bob"),
        do_work("carol", "carol"),
    )
    assert seen == {"alice": "alice", "bob": "bob", "carol": "carol"}


# ── require_scope ─────────────────────────────────────────────────────────


def test_require_scope_returns_user_when_scope_present():
    with user_scope(UserContext("alice", scopes={"kb.read"})):
        assert require_scope("kb.read").user_id == "alice"


def test_require_scope_raises_when_not_authenticated():
    with pytest.raises(PermissionError, match="No authenticated user"):
        require_scope("anything")


def test_require_scope_raises_when_scope_missing():
    with user_scope(UserContext("alice", scopes={"kb.read"})):
        with pytest.raises(PermissionError, match="lacks required scope"):
            require_scope("admin")
