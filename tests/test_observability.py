"""Tests for tracing module + PII scrubber."""

import pytest

from myclaw.observability import (
    init_tracing,
    is_tracing_enabled,
    span,
    traced,
    traced_async,
)
from myclaw.logging_config import scrub_pii, PIIScrubFilter
import logging


# ── Tracing: no-op behavior when disabled ─────────────────────────────────


def test_tracing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ZENSYNORA_TRACING_ENABLED", raising=False)
    # Tracing should be disabled unless explicitly enabled.
    assert is_tracing_enabled() is False


def test_span_is_noop_when_disabled():
    # Should not raise even when tracing is off.
    with span("test.op", attr1="value"):
        pass


@traced("sync.op")
def _sync_op(x):
    return x * 2


@traced_async("async.op")
async def _async_op(x):
    return x * 3


def test_traced_decorator_is_pass_through_when_disabled():
    assert _sync_op(5) == 10


@pytest.mark.asyncio
async def test_traced_async_decorator_is_pass_through_when_disabled():
    assert await _async_op(5) == 15


def test_init_tracing_idempotent():
    a = init_tracing(enabled=False)
    b = init_tracing(enabled=False)
    assert a == b


# ── PII scrubber ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("inp,expected_fragment", [
    ("Email me at alice@example.com", "<email:redacted>"),
    ("Call +1-555-123-4567", "<phone:redacted>"),
    ("token: sk-abcdefghij1234567890qrstuv", "<apikey:redacted>"),
    ("eyJabc.eyJdef.signABC123_xyz-abcDEFG", "<jwt:redacted>"),
])
def test_scrub_pii_redacts(inp, expected_fragment):
    out = scrub_pii(inp)
    assert expected_fragment in out
    # Original sensitive token should be gone.
    assert "alice@example.com" not in out
    assert "sk-abcdefghij1234567890qrstuv" not in out


def test_scrub_pii_hashes_user_id():
    out = scrub_pii("processing for user_id=alice123")
    assert "alice123" not in out
    assert "user_id=user:" in out  # hashed form


def test_scrub_pii_handles_empty():
    assert scrub_pii("") == ""
    assert scrub_pii(None) is None  # type: ignore[arg-type]


def test_pii_scrub_filter_redacts_log_message(caplog):
    logger = logging.getLogger("myclaw.test_scrub")
    logger.addFilter(PIIScrubFilter())
    with caplog.at_level(logging.INFO, logger="myclaw.test_scrub"):
        logger.info("contact alice@example.com about token sk-1234567890abcdefghijklmnop")
    text = caplog.text
    assert "alice@example.com" not in text
    assert "<email:redacted>" in text
    assert "<apikey:redacted>" in text
