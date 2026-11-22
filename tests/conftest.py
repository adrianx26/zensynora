"""Shared pytest hooks."""
import asyncio
from pathlib import Path

import pytest


def pytest_sessionfinish(session, exitstatus):
    # Pooled aiosqlite connections run on non-daemon threads; close them so
    # the pytest process can exit instead of hanging after the summary line.
    try:
        from myclaw.memory import AsyncSQLitePool

        if AsyncSQLitePool._states:
            asyncio.run(AsyncSQLitePool.close_all())
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_knowledge_state(request, tmp_path, monkeypatch):
    """Keep KB tests independent despite process-wide caches and workers."""
    name = Path(str(request.node.fspath)).name
    if not (name.startswith("test_kb") or name.startswith("test_knowledge")):
        yield
        return

    # Several callers use Path.home() while the knowledge storage layer checks
    # HOME first.  Keep both views pointed at the same disposable directory.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("MYCLAW_HOME", raising=False)

    from myclaw import config
    from myclaw.knowledge import (
        benchmarks, decay, recall, relevance, semantic, storage, sync, usage, watch,
    )

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".myclaw")
    monkeypatch.setattr(config, "CONFIG_FILE", config.CONFIG_DIR / "config.json")
    monkeypatch.setattr(config, "_cached_config", None)
    monkeypatch.setattr(config, "_config_mtime", None)

    def reset_runtime():
        config._cached_config = None
        config._config_mtime = None
        sync.clear_note_cache()
        sync.reset_sync_failures()
        benchmarks.clear_cache()
        semantic._stop_worker()
        if isinstance(getattr(semantic, "_WORKER", None), semantic._Worker):
            semantic._WORKER = semantic._Worker()
        semantic._reset_caches()
        semantic._OPTIONS.clear()
        semantic._OPTIONS.update({
            "semantic_search": True,
            "embedding_model": semantic.DEFAULT_MODEL,
            "kb_enabled": False,
            "min_score": 0.0,
            "fusion_fts_weight": semantic.FUSION_FTS_WEIGHT,
            "fusion_sem_weight": semantic.FUSION_SEM_WEIGHT,
            "fusion_keep_fts": semantic.FUSION_KEEP_FTS,
            "chunk_chars": 0,
            "chunk_overlap": semantic.CHUNK_OVERLAP,
        })
        semantic._APPLIED = False
        semantic._factory = None
        semantic._LAST.clear()
        semantic._LOGGED.clear()

        decay._OPTIONS.clear()
        decay._OPTIONS.update({
            "half_life_days": 0.0, "floor": 0.3, "categories": {}, "ai": False,
        })
        decay._LAST_USED.clear()

        usage.flush(2.0)
        usage._queue.clear()
        usage._wake.clear()
        usage._idle.set()
        usage._warned.clear()
        usage._schema_done.clear()
        usage._snapshot_done.clear()
        usage._inserted = 0
        usage.TURN.set(None)
        usage.REASON.set(None)
        usage._OPTIONS.clear()
        usage._OPTIONS.update({
            "usage_log": True, "kb_enabled": True,
            "retention_days": 90, "max_rows": 200_000,
        })

        relevance._CACHE.clear()
        watch.stop_background()
        watch._SELF.clear()
        watch._OPTIONS.clear()
        watch._OPTIONS.update({"watch_files": False, "kb_enabled": True})

        try:
            storage.set_knowledge_root(None)
        except Exception:
            # A test may intentionally replace the setter to verify that config
            # loading remains non-fatal.
            pass
        recall._OPTIONS.clear()
        recall._OPTIONS.update({"enabled": True, "timeout_seconds": 1.5})

    reset_runtime()
    try:
        yield
    finally:
        reset_runtime()


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
