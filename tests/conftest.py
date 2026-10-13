"""Shared pytest hooks."""
import asyncio


def pytest_sessionfinish(session, exitstatus):
    # Pooled aiosqlite connections run on non-daemon threads; close them so
    # the pytest process can exit instead of hanging after the summary line.
    try:
        from myclaw.memory import AsyncSQLitePool

        asyncio.run(AsyncSQLitePool.close_all())
    except Exception:
        pass
