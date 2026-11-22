import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from myclaw.config import AppConfig
from myclaw.knowledge import researcher


@pytest.fixture
def queue_paths(monkeypatch, tmp_path):
    gap_file = tmp_path / "knowledge_gaps.jsonl"
    research_log = tmp_path / "research_log.jsonl"
    monkeypatch.setattr(researcher, "GAP_FILE", gap_file)
    monkeypatch.setattr(researcher, "RESEARCH_LOG", research_log)
    return gap_file, research_log


def _write_gaps(path, *gaps):
    path.write_text(
        "".join(json.dumps(gap) + "\n" for gap in gaps),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_queue_preserves_failed_gap_and_retries_with_stable_id(
    queue_paths, monkeypatch
):
    gap_file, _ = queue_paths
    _write_gaps(
        gap_file,
        {"query": "How do I use A&B?", "timestamp": "2026-01-01T00:00:00Z"},
        {"query": "how   do i use a&b?", "timestamp": "2026-01-02T00:00:00Z"},
    )
    worker = researcher.GapResearcher(AppConfig())

    async def fail(query, user_id="default"):
        worker._last_error = "provider unavailable"
        return False

    monkeypatch.setattr(worker, "research_gap", fail)
    await worker.run_batch()

    queued = [json.loads(line) for line in gap_file.read_text(encoding="utf-8").splitlines()]
    assert len(queued) == 1
    assert queued[0]["status"] == "failed"
    assert queued[0]["attempts"] == 1
    assert queued[0]["last_error"] == "provider unavailable"
    first_id = queued[0]["id"]
    queued[0]["next_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    _write_gaps(gap_file, queued[0])

    restarted_worker = researcher.GapResearcher(AppConfig())
    monkeypatch.setattr(restarted_worker, "research_gap", fail)
    await restarted_worker.run_batch()
    retried = json.loads(gap_file.read_text(encoding="utf-8"))
    assert retried["id"] == first_id
    assert retried["attempts"] == 2


@pytest.mark.asyncio
async def test_queue_removes_only_after_success(queue_paths, monkeypatch):
    gap_file, _ = queue_paths
    _write_gaps(gap_file, {"query": "durable topic"})
    worker = researcher.GapResearcher(AppConfig())
    outcomes = iter((False, True))

    async def research(query, user_id="default"):
        result = next(outcomes)
        worker._last_error = "temporary failure"
        return result

    monkeypatch.setattr(worker, "research_gap", research)
    await worker.run_batch()
    assert gap_file.exists() and gap_file.read_text(encoding="utf-8")

    queued = json.loads(gap_file.read_text(encoding="utf-8"))
    queued["next_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    _write_gaps(gap_file, queued)
    await worker.run_batch()
    assert gap_file.read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_research_encodes_query_offloads_io_and_labels_note(
    queue_paths, monkeypatch
):
    main_thread = threading.get_ident()
    seen = {}

    def fake_get(url, **kwargs):
        seen["thread"] = threading.get_ident()
        seen["url"] = url
        return SimpleNamespace(status=200, content=("useful text" * 2_000).encode())

    class FakeProvider:
        async def chat(self, **kwargs):
            seen["messages"] = kwargs["messages"]
            return "A concise result", {}

    def fake_write_note(**kwargs):
        seen["note"] = kwargs
        seen["write_thread"] = threading.get_ident()
        return "research-result"

    monkeypatch.setattr(researcher, "http_get", fake_get)
    monkeypatch.setattr(researcher, "get_provider", lambda config: FakeProvider())
    monkeypatch.setattr(researcher, "write_note", fake_write_note)

    assert await researcher.GapResearcher(AppConfig()).research_gap("A&B / C++")
    assert seen["thread"] != main_thread
    assert seen["write_thread"] != main_thread
    assert parse_qs(urlparse(seen["url"]).query)["q"] == ["A&B / C++ detailed information"]
    assert len(seen["messages"][1]["content"].encode("utf-8")) < 15_000
    assert seen["note"]["metadata"] == {
        "source": "automated-web-research",
        "research_query": "A&B / C++",
        "researched_at": seen["note"]["metadata"]["researched_at"],
    }
    assert seen["note"]["origin"] == "agent"


@pytest.mark.asyncio
async def test_fetch_timeout_preserves_failure(queue_paths, monkeypatch):
    worker = researcher.GapResearcher(AppConfig())
    worker.config = AppConfig()
    monkeypatch.setattr(researcher, "FETCH_TIMEOUT_SECONDS", 0.01)

    def slow_fetch(query):
        threading.Event().wait(0.1)

    monkeypatch.setattr(worker, "_fetch_search_content", slow_fetch)
    assert not await worker.research_gap("slow")
    assert "timeout" in worker._last_error.lower()


@pytest.mark.asyncio
async def test_default_worker_requires_explicit_opt_in(monkeypatch):
    created = 0
    real = researcher.GapResearcher

    class TrackingResearcher(real):
        def __init__(self, config):
            nonlocal created
            created += 1
            super().__init__(config)

    monkeypatch.setattr(researcher, "load_config", lambda: AppConfig())
    monkeypatch.setattr(researcher, "GapResearcher", TrackingResearcher)
    await researcher.start_researcher_job()
    assert created == 0

    explicit = AppConfig(intelligence={"research_enabled": True})
    monkeypatch.setattr(researcher, "load_config", lambda: explicit)
    await researcher.start_researcher_job()
    assert created == 1


def test_concurrent_gap_appends_are_not_lost(queue_paths):
    gap_file, _ = queue_paths
    threads = [
        threading.Thread(
            target=researcher.GapResearcher.append_gap,
            args=({"query": f"gap {index}", "user_id": "default"},),
        )
        for index in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    queued = [
        json.loads(line)
        for line in gap_file.read_text(encoding="utf-8").splitlines()
    ]
    assert len({gap["query"] for gap in queued}) == 12


def test_completed_query_is_scoped_to_user(queue_paths):
    gap_file, research_log = queue_paths
    research_log.write_text(
        json.dumps({
            "query": "same topic",
            "user_id": "alice",
            "note_id": "research-same-topic",
        }) + "\n",
        encoding="utf-8",
    )
    _write_gaps(
        gap_file,
        {"query": "same topic", "user_id": "alice"},
        {"query": "same topic", "user_id": "bob"},
    )

    gaps = researcher.GapResearcher(AppConfig())._get_unprocessed_gaps()
    assert [gap["user_id"] for gap in gaps] == ["bob"]
