import threading
from types import SimpleNamespace

import pytest

from myclaw.knowledge import decision
from myclaw.knowledge.decision import assess_memory
from myclaw.knowledge import recall


@pytest.fixture
def knowledge_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from myclaw.knowledge import semantic, storage

    storage.set_knowledge_root(None)
    monkeypatch.setattr(semantic, "_OPTIONS", dict(
        semantic._OPTIONS, semantic_search=False, kb_enabled=True))
    semantic.wait_idle(20)
    yield tmp_path
    semantic.wait_idle(20)


def test_memory_decision_reports_novelty_relevance_and_evidence():
    decision = assess_memory(
        "Which server has 32 GB RAM?",
        "The server has 32 GB RAM",
        ["The server has 32GB RAM."],
        verification_status="verified",
        source="research.md#observation-1",
    )

    assert not decision.is_new
    assert decision.relevant and decision.relevance_score > 0
    assert decision.evidence_sufficient
    assert decision.include and not decision.stale
    assert "exact normalized claim already exists" in decision.reasons


def test_memory_decision_preserves_uncertain_and_archived_evidence_signals():
    archived = assess_memory(
        "What was the old model?",
        "The previous model was Betelgeuse",
        status="archived",
        confidence=0.4,
        verification_status="inferred",
    )
    unrelated = assess_memory("What is the weather?", "The server uses Linux")

    assert archived.stale and archived.relevant and archived.include
    assert not archived.evidence_sufficient
    assert not unrelated.relevant and not unrelated.include
    assert unrelated.is_new


def test_local_decision_reranks_without_dropping_evidence(monkeypatch):
    candidates = [
        {"id": "first", "title": "First", "evidence": "First source-backed claim"},
        {"id": "second", "title": "Second", "evidence": "Second source-backed claim"},
    ]
    rules = {
        "first": assess_memory("query", "query first"),
        "second": assess_memory("query", "query second"),
    }
    config = SimpleNamespace(knowledge=SimpleNamespace(
        decision_mode="local", decision_provider="ollama", decision_model="small-local"))
    monkeypatch.setattr(decision, "_local_judge", lambda *_: [
        {"id": 0, "is_new": False, "needed_in_context": False,
         "enough_to_answer": False, "reason": "Not as relevant"},
        {"id": 1, "is_new": True, "needed_in_context": True,
         "enough_to_answer": True, "reason": "Directly answers the query"},
    ])

    ranked, status = decision.rank_candidates("query", candidates, rules, config=config)

    assert [candidate["id"] for candidate in ranked] == ["second", "first"]
    assert status["state"] == "ready"
    assert status["decisions"][1]["enough_to_answer"] is True


def test_local_decision_timeout_falls_back_to_all_rule_candidates(monkeypatch):
    candidates = [{"id": "one", "title": "One", "evidence": "Source"}]
    rules = {"one": assess_memory("query", "query evidence")}
    config = SimpleNamespace(knowledge=SimpleNamespace(
        decision_mode="local", decision_provider="ollama", decision_model="small-local"))

    def timeout(*_args):
        raise TimeoutError("local model did not respond")

    monkeypatch.setattr(decision, "_local_judge", timeout)
    ranked, status = decision.rank_candidates("query", candidates, rules, config=config)

    assert ranked == candidates
    assert status["state"] == "fallback"
    assert status["error"] == "local model did not respond"


def test_local_decision_replaces_failed_rules_and_preserves_candidate(monkeypatch):
    note = SimpleNamespace(
        permalink="source", title="Router", content="Router uses WireGuard",
        observations=[SimpleNamespace(content="Router uses WireGuard", status="active")],
        metadata={})
    monkeypatch.setattr(decision, "assess_memory", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        ValueError("invalid rule input")))
    monkeypatch.setitem(decision._OPTIONS, "mode", "rules")
    monkeypatch.setitem(decision._OPTIONS, "provider", "ollama")
    monkeypatch.setitem(decision._OPTIONS, "model", "small-local")
    monkeypatch.setattr(decision, "_local_judge", lambda *_: [
        {"id": 0, "is_new": True, "needed_in_context": True,
         "enough_to_answer": True, "reason": "Directly answers the query"},
    ])
    info = {}

    results = recall._fuse({"fts": [note]}, 1, query="router", info=info)

    assert results == [note]
    assert "deterministic decision failed" in info["decisions"]["source"]["reasons"][0]
    assert info["decision_model"]["state"] == "ready"


def test_local_model_can_be_warmed_as_rules_fallback(monkeypatch):
    config = SimpleNamespace(knowledge=SimpleNamespace(
        decision_mode="rules", decision_provider="ollama", decision_model="small-local"))
    started = []

    class Thread:
        def __init__(self, *, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            started.append((self.target, self.args))

    monkeypatch.setattr(decision, "_STATE", {"state": "not_run", "latency_ms": None, "error": None})
    monkeypatch.setattr(decision, "threading", SimpleNamespace(Thread=Thread))

    status = decision.warm(config)

    assert status["mode"] == "rules"
    assert status["state"] == "warming"
    assert len(started) == 1


@pytest.mark.parametrize(("provider", "payload"), [
    ("ollama", {"message": {"content": '{"ready":true}'}}),
    ("lmstudio", {"choices": [{"message": {"content": '{"ready":true}'}}]}),
])
def test_local_model_warmup_requires_valid_readiness_json(provider, payload):
    decision._validate_warm_response(provider, payload)

    with pytest.raises(ValueError, match="did not return valid readiness JSON"):
        decision._validate_warm_response(provider, {})


def test_extraction_link_hints_use_four_way_recall(monkeypatch, caplog):
    from myclaw.knowledge import extract

    note = SimpleNamespace(title="Server specifications")
    seen = {}

    def fake_recall(query, user_id, limit, info, boolean):
        seen.update(query=query, user_id=user_id, limit=limit, boolean=boolean)
        info.update(partial=True, strategies={"fts": "ok", "semantic": "timeout"})
        return [note]

    monkeypatch.setattr(recall, "parallel_recall", fake_recall)

    assert extract.existing_titles("Tell me about server specifications", "default") == [
        "Server specifications"]
    assert seen == {
        "query": "Tell OR about OR server OR specifications",
        "user_id": "default",
        "limit": 12,
        "boolean": True,
    }
    assert "partial four-way retrieval" in caplog.text


def test_decision_model_endpoint_must_be_loopback_only():
    assert decision.is_loopback_url("http://localhost:11434")
    assert decision.is_loopback_url("http://127.0.0.1:1234/v1")
    assert not decision.is_loopback_url("https://example.com/v1")
    assert not decision.is_loopback_url("http://192.168.1.4:1234/v1")


def test_parallel_recall_runs_each_stream_and_fuses_results(monkeypatch):
    barrier = threading.Barrier(4)
    shared = SimpleNamespace(permalink="shared", title="Shared")
    graph_only = SimpleNamespace(permalink="graph", title="Graph")

    def stream(items):
        def run():
            barrier.wait(timeout=1)
            return items
        return run

    monkeypatch.setattr(recall, "_streams", lambda *_: {
        "fts": stream([shared]),
        "semantic": stream([shared]),
        "graph": stream([shared, graph_only]),
        "temporal": stream([shared]),
    })
    monkeypatch.setitem(recall._OPTIONS, "enabled", False)
    monkeypatch.setitem(recall._OPTIONS, "timeout_seconds", 2.0)
    info = {}

    results = recall.parallel_recall("query", limit=2, info=info)

    assert [item.permalink for item in results] == ["shared", "graph"]
    assert set(info["strategies"].values()) == {"ok"}
    assert info["search_mode"] == "parallel-hybrid"


def test_parallel_recall_keeps_relevant_results_from_all_four_streams(monkeypatch):
    notes = {
        name: SimpleNamespace(
            permalink=name,
            title=f"Query {name} evidence",
            observations=[],
            content=f"Relevant query evidence from {name}.",
            metadata={"verification_status": "verified"},
        )
        for name in ("fts", "semantic", "graph", "temporal")
    }
    monkeypatch.setattr(recall, "_streams", lambda *_: {
        name: (lambda note=note: [note]) for name, note in notes.items()
    })
    monkeypatch.setitem(recall._OPTIONS, "timeout_seconds", 2.0)
    info = {}

    results = recall.parallel_recall("query evidence", limit=4, info=info)

    assert {note.permalink for note in results} == set(notes)
    assert set(info["strategies_by_note"]) == set(notes)
    assert all(info["strategies_by_note"][name] == [name] for name in notes)


def test_parallel_recall_keeps_other_streams_when_one_fails(monkeypatch):
    note = SimpleNamespace(permalink="ok", title="Available")
    monkeypatch.setattr(recall, "_streams", lambda *_: {
        "fts": lambda: [note],
        "semantic": lambda: (_ for _ in ()).throw(RuntimeError("model unavailable")),
        "graph": lambda: [],
        "temporal": lambda: [],
    })
    monkeypatch.setitem(recall._OPTIONS, "enabled", True)
    monkeypatch.setitem(recall._OPTIONS, "timeout_seconds", 2.0)
    info = {}

    results = recall.parallel_recall("query", limit=1, info=info)

    assert [item.permalink for item in results] == ["ok"]
    assert info["strategies"]["semantic"] == "error"


def test_parallel_recall_timeout_does_not_leave_a_process_blocking_worker(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(recall, "_streams", lambda *_: {
        "fts": lambda: [],
        "semantic": lambda: (release.wait(2), [])[1],
        "graph": lambda: [],
        "temporal": lambda: [],
    })
    monkeypatch.setitem(recall._OPTIONS, "timeout_seconds", 0.1)
    info = {}

    assert recall.parallel_recall("query", limit=1, info=info) == []

    worker = next(thread for thread in threading.enumerate()
                  if thread.name == "kb-recall-semantic")
    assert info["strategies"]["semantic"] == "timeout"
    assert worker.daemon
    release.set()
    worker.join(timeout=1)


def test_recall_decisions_detect_duplicate_claims_across_notes():
    first = SimpleNamespace(
        permalink="first", title="Server A",
        observations=[SimpleNamespace(content="The server uses Linux", status="active")])
    second = SimpleNamespace(
        permalink="second", title="Server B",
        observations=[SimpleNamespace(content="The server uses Linux", status="active",
                                      verification_status="verified",
                                      provenance_path="research.md")])
    info = {}

    results = recall._fuse(
        {"fts": [first], "graph": [second]}, 2, query="Which server uses Linux?", info=info)

    decisions = info["decisions"]
    assert decisions["first"]["is_new"] is False
    assert decisions["second"]["is_new"] is False
    assert decisions["first"]["relevant"] is True
    assert decisions["second"]["evidence_sufficient"] is True
    assert [note.permalink for note in results] == ["second", "first"]


def test_parallel_recall_reaches_related_notes_through_graph(knowledge_home, monkeypatch):
    from myclaw.knowledge import Relation, semantic, write_note

    write_note("Network", "The network topology includes a private router.")
    write_note("Router", "The router listens on port 51820.",
               relations=[Relation("part_of", "Network")])
    monkeypatch.setattr(semantic, "_OPTIONS", dict(
        semantic._OPTIONS, semantic_search=False, kb_enabled=True))
    info = {}

    results = recall.parallel_recall("router port 51820", limit=3, info=info)

    assert "router" in [note.permalink for note in results]
    assert "network" in [note.permalink for note in results]
    assert info["strategies"]["fts"] == "ok"
    assert info["strategies"]["graph"] == "ok"


def test_temporal_recall_accepts_legacy_note_shapes(knowledge_home, monkeypatch):
    from myclaw.knowledge import decay, write_note
    from myclaw.knowledge import storage

    write_note("Legacy", "A legacy note with no observation metadata.")
    note = SimpleNamespace(permalink="legacy", title="Legacy", tags=[])
    monkeypatch.setattr(storage, "_batch_read_notes", lambda *_: [note])
    monkeypatch.setattr(decay, "note_factor", lambda *args, **kwargs: (0.5, 10, 5))

    assert recall._temporal_search("legacy", "default", 3) == [note]


@pytest.mark.parametrize(
    ("observation_dates", "metadata_date", "expected_date"),
    [
        ([None, "unknown", "invalid"], None, "2022-01-01"),
        ([None, "2020-01-01", "2021-01-01"], "2019-01-01", "2021-01-01"),
        (["invalid", "2020-01-01"], "2023-01-01", "2023-01-01"),
        (["1970-01-01T00:00:00+00:00"], None, "1970-01-01T00:00:00+00:00"),
    ],
)
def test_temporal_recall_uses_latest_valid_capture(
        knowledge_home, monkeypatch, observation_dates, metadata_date, expected_date):
    from myclaw.knowledge import decay, storage, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Router", "Router settings.",
               observations=[Observation("spec", "Router settings", captured_at="2022-01-01")])
    note = SimpleNamespace(
        permalink="router", title="Router", tags=[], metadata={"captured_at": metadata_date},
        observations=[SimpleNamespace(content="Router settings", captured_at=date)
                      for date in observation_dates],
    )
    captured = []
    monkeypatch.setattr(storage, "_batch_read_notes", lambda *_: [note])
    monkeypatch.setattr(
        decay, "note_factor",
        lambda _tags, captured_at, now: (captured.append(captured_at) or 0.5, 10, 5))

    assert recall._temporal_search("router", "default", 3) == [note]
    assert captured == [recall._date_timestamp(expected_date)]


def test_temporal_recall_uses_matching_observation_capture_time(knowledge_home, monkeypatch):
    from myclaw.knowledge import decay, storage, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Router old", "Historical router settings.",
               observations=[Observation("spec", "Router uses WireGuard",
                                         captured_at="2020-01-01")])
    write_note("Router fresh", "Current router settings.",
               observations=[Observation("spec", "Router uses WireGuard",
                                         captured_at="2026-01-01")])
    old = SimpleNamespace(
        permalink="router-old", title="Router settings", tags=[], updated_at=None,
        observations=[SimpleNamespace(content="Router uses WireGuard", captured_at="2020-01-01",
                                      status="active")])
    fresh = SimpleNamespace(
        permalink="router-fresh", title="Router settings", tags=[], updated_at=None,
        observations=[SimpleNamespace(content="Router uses WireGuard", captured_at="2026-01-01",
                                      status="active")])
    monkeypatch.setattr(storage, "_batch_read_notes", lambda *_: [old, fresh])
    monkeypatch.setattr(
        decay, "note_factor",
        lambda _tags, captured_at, now: (0.9 if str(captured_at).startswith("2026") else 0.2,
                                         1, 180))

    results = recall._temporal_search("router WireGuard", "default", 3)

    assert [note.permalink for note in results] == ["router-fresh", "router-old"]
    historical = recall._temporal_search("router WireGuard 2020", "default", 1)
    assert [note.permalink for note in historical] == ["router-old"]


def test_reflection_persists_profile_pages_with_source_provenance(knowledge_home):
    from myclaw.knowledge import distill, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Preferences", "Personal preferences",
               observations=[Observation("preference", "I prefer concise answers")])
    result = distill.distill()
    pages = distill.list_pages(query="concise answers")

    assert result["ok"] and result["pages"] >= 1
    profile = next(page for page in pages["pages"] if page["key"] == "profile:user")
    assert "concise answers" in profile["content"].lower()
    assert "{'id':" not in profile["content"]
    assert profile["sources"] == ["preferences"]


def test_reflection_leaves_unaffected_pages_unchanged(knowledge_home):
    from myclaw.knowledge import distill, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Preferences", "Personal preferences",
               observations=[Observation("preference", "I prefer concise answers")])
    assert distill.distill()["ok"]
    conn = distill._open("default")
    try:
        before = tuple(conn.execute(
            "SELECT checksum, updated_at FROM memory_pages WHERE page_key='profile:user'").fetchone())
    finally:
        conn.close()

    write_note("Ethernet", "Networking reference",
               observations=[Observation("concept", "Ethernet frames carry payload data")])
    assert distill.distill()["ok"]
    conn = distill._open("default")
    try:
        after = tuple(conn.execute(
            "SELECT checksum, updated_at FROM memory_pages WHERE page_key='profile:user'").fetchone())
    finally:
        conn.close()

    assert after == before


def test_distillation_adds_pages_table_to_existing_index(knowledge_home):
    from myclaw.knowledge import distill, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Preferences", "Personal preferences",
               observations=[Observation("preference", "I prefer concise answers")])
    assert distill.distill()["ok"]
    conn = distill._open("default")
    try:
        conn.execute("DROP TABLE memory_pages")
        conn.commit()
    finally:
        conn.close()
    assert distill.list_pages()["pages"] == []
    assert distill.status()["pages"] == 0

    write_note("Router", "A networking reference.",
               observations=[Observation("spec", "The router uses WireGuard")])
    result = distill.distill()

    assert result["ok"] and result["pages"] >= 1
    assert distill.list_pages()["total"] >= 1


def test_topic_pages_are_source_linked_and_used_in_distilled_context(knowledge_home):
    from myclaw.knowledge import Relation, distill, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Network", "The private network.",
               observations=[
                   Observation("spec", "The router uses 2.5 Gbps ethernet"),
                   Observation("spec", "The VPN endpoint uses WireGuard")])
    write_note("Router", "The edge router.",
               observations=[Observation("spec", "The router address is 192.0.2.1")],
               relations=[Relation("part_of", "Network")])

    assert distill.distill()["ok"]
    pages = distill.list_pages(query="router ethernet VPN")["pages"]
    topic = next(page for page in pages if page["key"].startswith("scenario:"))
    context = distill.context_block("default", "router ethernet VPN")

    assert {"network", "router"}.issubset(set(topic["sources"]))
    assert "topic page" in context and "[sources: network, router]" in context


def test_reflection_proposes_evidence_ranked_merges_and_safe_redundant_work(knowledge_home, monkeypatch):
    from myclaw.knowledge import distill, write_note
    from myclaw.knowledge.parser import Observation

    monkeypatch.setattr(distill, "_embedder", lambda **_kwargs: None)
    observation = Observation(
        "spec", "The router uses WireGuard", captured_at="2026-11-14",
        confidence=0.95, verification_status="verified",
        provenance_path="vendor-manual.md#network")
    write_note("Router reference", "The router uses WireGuard.", observations=[observation])
    write_note("Router copy", "The router uses WireGuard.", observations=[observation])
    note_dir = knowledge_home / ".myclaw" / "knowledge" / "default"
    originals = {path.name: path.read_bytes() for path in note_dir.glob("*.md")}
    assert distill.distill()["ok"]

    proposals = distill.consolidation_proposals()

    assert len(proposals["merge_proposals"]) == 1
    candidate = proposals["merge_proposals"][0]
    assert candidate["review_status"] == "review"
    assert candidate["shared_facts"] == 1
    assert candidate["verified_shared_facts"] == 1
    assert candidate["priority_is_confidence"] is False
    assert set(candidate["source_checksums"]) == {"router-reference", "router-copy"}
    assert proposals["redundant_work"][0]["count"] == 2
    assert proposals["redundant_work"][0]["safe_to_skip"] is True
    assert {path.name: path.read_bytes() for path in note_dir.glob("*.md")} == originals


def test_retain_recall_reflect_round_trip(knowledge_home, monkeypatch):
    from myclaw.knowledge import distill, semantic, write_note
    from myclaw.knowledge.parser import Observation

    permalink = write_note(
        "Home server", "The primary home server.",
        observations=[Observation(
            "spec", "mindy01 has 32 GB RAM", captured_at="2026-11-14T10:00:00+00:00",
            confidence=0.9, verification_status="verified", status="active")])
    monkeypatch.setattr(semantic, "_OPTIONS", dict(
        semantic._OPTIONS, semantic_search=False, kb_enabled=True))
    recalled = recall.parallel_recall("How much RAM does the home server have?")
    reflected = distill.distill()
    context = distill.context_block("default", "How much RAM does the home server have?")

    assert permalink in [note.permalink for note in recalled]
    assert reflected["ok"]
    assert "32 GB RAM" in context


def test_live_writes_and_extraction_stamp_captured_at(knowledge_home):
    from myclaw.knowledge import extract, read_note
    from myclaw.tools.kb import write_to_knowledge

    write_to_knowledge("Live Note", "Captured today.", observations="spec | 16 GB RAM")
    live = read_note("live-note")
    assert live.observations[0].captured_at not in (None, "", "unknown")

    write_to_knowledge("Old Note", "Undated research.", observations="fact | Legacy spec", captured_at="unknown")
    old = read_note("old-note")
    assert old.observations[0].captured_at is None or old.observations[0].captured_at == "unknown"

    saved = extract.save_items(
        [{"title": "Extracted Note", "content": "Extracted live.", "tags": "ops",
          "facts": [{"category": "fact", "text": "Service runs on port 8080"}]}],
        user_id="default", extra_tags=[], cap=5, log_prefix="[test]", source="chat 2026-11-14",
    )
    assert saved == 1
    ext = read_note("extracted-note")
    assert ext.observations[0].captured_at not in (None, "", "unknown")


def test_windowed_temporal_search_falls_back_to_updated_at_and_ranks_captured_first(knowledge_home):
    from datetime import datetime

    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Observation

    captured_date = datetime.now().astimezone().date().isoformat()
    write_note("Router Captured", "Router WireGuard setup.",
               observations=[Observation("spec", "WireGuard port 51820",
                                         captured_at=f"{captured_date}T10:00:00+00:00")])
    write_note("Router Updated Only", "Router WireGuard fallback.",
               observations=[Observation("spec", "WireGuard fallback port")])
    write_note("May Activity", "Hiked Bucegi mountains and configured NAS.",
               observations=[Observation("fact", "Hiked Bucegi", captured_at="2026-05-12")])

    hits = recall._temporal_search(f"WireGuard {captured_date}", "default", 5)
    permalinks = [note.permalink for note in hits]
    assert "router-captured" in permalinks
    assert "router-updated-only" in permalinks
    assert permalinks.index("router-captured") < permalinks.index("router-updated-only")

    may_hits = recall._temporal_search("what I do in May?", "default", 5)
    assert [note.permalink for note in may_hits] == ["may-activity"]


def test_backfill_captured_at_only_uses_verifiable_dates(knowledge_home):
    from myclaw.knowledge import quality, read_note, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Weather Bucharest 2026-10-20", "Sunny 18C.",
               observations=[Observation("fact", "High 18C")])
    undated_path = knowledge_home / ".myclaw" / "knowledge" / "default" / "undated-research.md"
    undated_path.write_text("# Undated Research\n\n- [fact] Historical note\n", encoding="utf-8")
    from myclaw.knowledge import sync_knowledge
    sync_knowledge("default")

    preview = quality.backfill_captured_at("default", dry_run=True)
    assert [c["permalink"] for c in preview["candidates"]] == ["weather-bucharest-2026-10-20"]
    assert preview["candidates"][0]["captured_at"] == "2026-10-20"
    assert preview["skipped_unknown"] == 1

    applied = quality.backfill_captured_at("default", dry_run=False)
    assert applied["updated"] == 1
    assert read_note("weather-bucharest-2026-10-20").observations[0].captured_at == "2026-10-20"
    assert read_note("undated-research").observations[0].captured_at in (None, "unknown")


def test_access_tracking_and_merge_preserves_combined_use_count(knowledge_home):
    from myclaw.knowledge import quality, sync_knowledge, write_note
    from myclaw.knowledge.db import KnowledgeDB
    from myclaw.knowledge.parser import Observation
    from myclaw.tools.kb import read_knowledge

    write_note("Alpha", "Alpha router note.", observations=[Observation("spec", "32 GB RAM")])
    write_note("Beta", "Beta router note.", observations=[Observation("spec", "32 GB RAM")])

    read_knowledge("alpha")
    read_knowledge("alpha")
    read_knowledge("beta")

    with KnowledgeDB("default") as db:
        ea = db.get_entity_by_permalink("alpha")
        eb = db.get_entity_by_permalink("beta")
        assert ea.created_at is not None and ea.last_accessed is not None and ea.use_count == 2
        assert eb.created_at is not None and eb.last_accessed is not None and eb.use_count == 1

    quality.merge_notes("default", "alpha", "beta", dry_run=False)
    with KnowledgeDB("default") as db:
        merged = db.get_entity_by_permalink("alpha")
        assert merged.use_count == 3
        assert merged.last_accessed is not None

    # Full rebuild preserves combined use_count and last_accessed from frontmatter metadata
    sync_knowledge("default", force=True)
    with KnowledgeDB("default") as db:
        rebuilt = db.get_entity_by_permalink("alpha")
        assert rebuilt.use_count == 3
        assert rebuilt.last_accessed is not None
