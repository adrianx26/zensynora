"""Shared pytest hooks."""
import asyncio

import pytest


def pytest_sessionfinish(session, exitstatus):
    # Pooled aiosqlite connections run on non-daemon threads; close them so
    # the pytest process can exit instead of hanging after the summary line.
    try:
        from myclaw.memory import AsyncSQLitePool

        asyncio.run(AsyncSQLitePool.close_all())
    except Exception:
        pass


UI_HEADERS = {"X-ZenSynora-UI": "1"}


@pytest.fixture(autouse=True)
def _web_ui_header(monkeypatch):
    """2026-10-28: state-changing guarded routes need the header the Web UI sends.

    Test clients behave like the Web UI unless a test passes ``headers=...``
    itself (``headers={}`` = a plain client such as curl, which gets 403).
    """
    try:
        from fastapi.testclient import TestClient
    except ImportError:  # pragma: no cover
        return
    original = TestClient.__init__

    def init(self, *args, headers=None, **kwargs):
        original(self, *args, headers=dict(UI_HEADERS) if headers is None else headers, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", init)
