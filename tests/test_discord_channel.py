"""Tests for the Discord channel adapter — focuses on the chunking helper
since the bot itself needs a live token to exercise."""

import pytest

from myclaw.channels.discord import (
    DiscordChannel,
    chunk_for_discord,
    is_discord_available,
)


# ── chunk_for_discord ────────────────────────────────────────────────────


def test_short_text_returned_unchanged():
    assert chunk_for_discord("hello") == ["hello"]


def test_text_at_limit_unchanged():
    text = "a" * 2000
    assert chunk_for_discord(text) == [text]


def test_chunks_at_paragraph_boundaries():
    para = "a" * 1500
    text = para + "\n\n" + para
    chunks = chunk_for_discord(text)
    assert len(chunks) == 2
    assert all(len(c) <= 2000 for c in chunks)


def test_long_paragraph_falls_back_to_lines_then_hard_slice():
    """A single 5000-character line must still be sliced into ≤2000 chunks."""
    text = "x" * 5000
    chunks = chunk_for_discord(text)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text


def test_custom_limit_respected():
    text = "abcdef" * 100  # 600 chars
    chunks = chunk_for_discord(text, limit=200)
    assert all(len(c) <= 200 for c in chunks)


# ── DiscordChannel constructor ────────────────────────────────────────────


def test_constructor_rejects_empty_token():
    async def handler(_msg, _user_id):
        return "ok"
    with pytest.raises(ValueError):
        DiscordChannel(token="", agent_handler=handler)


def test_constructor_works_without_discord_py(monkeypatch):
    """Importing and constructing must NOT require the optional dep —
    only ``run()`` does. Validates the install-anywhere posture."""
    async def handler(_msg, _user_id):
        return "ok"
    # Constructor should succeed regardless.
    ch = DiscordChannel(token="fake-token", agent_handler=handler)
    assert ch is not None


@pytest.mark.asyncio
async def test_run_raises_clearly_when_dep_missing(monkeypatch):
    import myclaw.channels.discord as dc_mod
    monkeypatch.setattr(dc_mod, "_DISCORD_AVAILABLE", False)
    async def handler(_m, _u):
        return ""
    ch = DiscordChannel(token="fake", agent_handler=handler)
    with pytest.raises(RuntimeError, match="discord.py is not installed"):
        await ch.run()


def test_is_available_helper_does_not_crash():
    # Just verify the boolean is callable; result depends on environment.
    assert isinstance(is_discord_available(), bool)
