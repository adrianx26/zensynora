"""Tests for the prompt template registry."""

from pathlib import Path

import pytest

from myclaw.prompts import PromptRegistry, PromptTemplate


@pytest.fixture
def registry(tmp_path: Path) -> PromptRegistry:
    """Fresh registry backed by a temp file (no shared state across tests)."""
    return PromptRegistry(path=tmp_path / "prompts.jsonl")


# ── Registration & versioning ─────────────────────────────────────────────


def test_register_starts_at_version_one(registry: PromptRegistry):
    tpl = registry.register("greeting", "Hello {{ name }}")
    assert tpl.version == 1
    assert tpl.name == "greeting"


def test_re_registering_increments_version(registry: PromptRegistry):
    a = registry.register("greeting", "v1")
    b = registry.register("greeting", "v2")
    c = registry.register("greeting", "v3")
    assert (a.version, b.version, c.version) == (1, 2, 3)


def test_get_returns_latest_by_default(registry: PromptRegistry):
    registry.register("p", "v1")
    registry.register("p", "v2")
    assert registry.get("p").body == "v2"
    assert registry.get("p").version == 2


def test_get_specific_version(registry: PromptRegistry):
    registry.register("p", "v1")
    registry.register("p", "v2")
    assert registry.get("p", version=1).body == "v1"
    assert registry.get("p", version=99) is None


def test_get_unknown_returns_none(registry: PromptRegistry):
    assert registry.get("nonexistent") is None


def test_invalid_name_rejected(registry: PromptRegistry):
    with pytest.raises(ValueError):
        registry.register("bad name with spaces", "body")


# ── Persistence ───────────────────────────────────────────────────────────


def test_persistence_roundtrip(tmp_path: Path):
    path = tmp_path / "p.jsonl"
    r1 = PromptRegistry(path=path)
    r1.register("foo", "v1", description="first", tags=["bar"])
    r1.register("foo", "v2")

    # Re-open: same underlying file, fresh in-memory registry.
    r2 = PromptRegistry(path=path)
    assert r2.list_versions("foo") == [1, 2]
    assert r2.get("foo").body == "v2"
    assert r2.get("foo", version=1).description == "first"


# ── Rendering ─────────────────────────────────────────────────────────────


def test_render_basic(registry: PromptRegistry):
    """Test rendering using whichever engine is available.

    Jinja2 (preferred) uses ``{{ var }}``; the stdlib fallback uses ``$var``.
    We pick the syntax based on what's importable so this test exercises the
    actual rendering path without depending on optional packages.
    """
    try:
        import jinja2  # noqa: F401
        tpl = registry.register("greet", "Hello {{ name }}!")
    except ImportError:
        tpl = registry.register("greet", "Hello $name!")
    out = tpl.render(name="Alice")
    assert "Alice" in out


def test_render_jinja_specific():
    """Jinja2 features {{ var }} only work when jinja2 is installed."""
    pytest.importorskip("jinja2")
    tpl = PromptTemplate(name="t", version=1, body="{{ x }} + {{ y }}")
    assert tpl.render(x=2, y=3) == "2 + 3"


def test_render_missing_variable_raises():
    """Missing variables must be loud, not silent."""
    pytest.importorskip("jinja2")
    tpl = PromptTemplate(name="t", version=1, body="{{ name }}")
    with pytest.raises(Exception):
        tpl.render()


# ── Listing ──────────────────────────────────────────────────────────────


def test_list_names_and_versions(registry: PromptRegistry):
    registry.register("a", "v1")
    registry.register("b", "v1")
    registry.register("a", "v2")
    assert registry.list_names() == ["a", "b"]
    assert registry.list_versions("a") == [1, 2]
    assert registry.list_versions("b") == [1]


def test_all_latest_returns_one_per_name(registry: PromptRegistry):
    registry.register("a", "v1")
    registry.register("a", "v2")
    registry.register("b", "v1")
    latest = {t.name: t.version for t in registry.all_latest()}
    assert latest == {"a": 2, "b": 1}
