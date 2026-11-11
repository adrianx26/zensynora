"""2026-11-11: the Knowledge → Tune routes, the curate-duplicates routes and the agent tools."""

from __future__ import annotations

import json

import pytest

from tests.test_kb_tuning import _note, tuned  # noqa: F401  (fixtures / helpers)
from tests.test_v071_features import home  # noqa: F401


@pytest.fixture
def client(tuned):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import knowledge_tuning_api

    app = FastAPI()  # not myclaw.web.api: importing it here would fix its state to this test's home
    app.include_router(knowledge_tuning_api.router)
    return TestClient(app)


def test_tune_page_values_set_undo_and_guard(client):
    d = client.get("/api/knowledge/tuning").json()
    keys = {p["key"] for p in d["params"]}
    assert {"semantic_min_score", "cache_similarity", "fusion_sem_weight", "chunk_chars", "decay_half_life_days"} <= keys
    assert d["mode"] == "suggest" and d["decay_ai"] is False
    assert {"BAAI/bge-small-en-v1.5", "BAAI/bge-large-en-v1.5", "intfloat/multilingual-e5-large"} <= \
        {m["name"] for m in d["models"] if m["offered"]}
    r = client.put("/api/knowledge/tuning", json={"changes": {"cache_similarity": 0.9, "decay_half_life_days": 120,
                                                              "decay_categories": {"news": 30}},
                                                  "tuning_ai": "safe", "decay_ai": True}).json()
    assert set(r["applied"]) == {"cache_similarity", "decay_half_life_days", "decay_categories"}
    assert r["tuning"]["mode"] == "safe" and r["tuning"]["decay_ai"] is True
    assert client.put("/api/knowledge/tuning", json={"changes": {"cache_similarity": 3}}).json()["errors"]
    assert client.put("/api/knowledge/tuning", json={"tuning_ai": "anything"}).status_code == 400
    assert client.post("/api/knowledge/tuning/undo", json={}).json()["applied"]["cache_similarity"] == [0.9, 0.92]
    assert client.post("/api/knowledge/tuning/proposals/999", json={"approve": True}).status_code == 404
    from fastapi.testclient import TestClient

    plain = TestClient(client.app, headers={})     # not the Web UI: refused
    assert plain.put("/api/knowledge/tuning", json={"changes": {"cache_similarity": 0.9}}).status_code in (401, 403)


def test_test_bench_feedback_and_decay_digest(client):
    a = _note("Restic backup", "restic backup nightly")
    _note("Backup to NAS", "backup restic nas")
    r = client.post("/api/knowledge/tuning/test", json={"query": "restic backup"}).json()
    assert r["info"]["mode"] == "fts" and {x["permalink"] for x in r["results"]} >= {a}
    assert {"position", "word_rank", "decay", "vote", "score"} <= set(r["results"][0])
    last = r["results"][-1]["permalink"]
    assert client.post("/api/knowledge/tuning/feedback", json={"query": "restic backup", "permalink": last, "vote": 1}).status_code == 200
    assert client.post("/api/knowledge/tuning/test", json={"query": "restic backup"}).json()["results"][0]["permalink"] == last
    assert client.get("/api/knowledge/tuning/feedback").json()["labelled"] == 1
    assert "at least 5" in client.post("/api/knowledge/tuning/autotune").json()["message"]
    assert client.get("/api/knowledge/tuning/decay").json()["notes"] == 2


def test_ai_suggest_route_and_unavailable_model(client, monkeypatch):
    async def fake(config, messages, provider=None, model=None):
        return json.dumps({"summary": "s", "changes": [{"key": "decay_floor", "value": 0.5, "why": "w"}]}), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)
    r = client.post("/api/knowledge/tuning/ai?scope=decay").json()
    assert r["proposed"][0]["key"] == "decay_floor" and r["mode"] == "suggest"
    pid = r["proposed"][0]["id"]
    assert client.post(f"/api/knowledge/tuning/proposals/{pid}", json={"approve": True}).json()["applied"] == {"decay_floor": [0.3, 0.5]}

    async def down(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", down)
    assert client.post("/api/knowledge/tuning/ai").status_code == 503
    assert client.post("/api/knowledge/tuning/ai?scope=everything").status_code == 422


def test_relations_routes(client):
    from myclaw.knowledge.parser import Relation
    from myclaw.knowledge.storage import read_note, write_note

    _note("Redis")
    app = write_note("App", "App", [], [Relation("relates_to", "Redis")], [], "default", "x")
    d = client.get("/api/knowledge/relations").json()
    assert d["total"] == 1 and d["vague"] == 1 and d["links"][0]["strength"] == 1
    body = {"permalink": app, "type": "relates_to", "target": "Redis", "delta": 1}
    assert client.post("/api/knowledge/relations/strength", json=body).json()["strength"] == 2
    assert client.post("/api/knowledge/relations/strength", json={**body, "target": "Nope"}).status_code == 400
    assert client.get("/api/knowledge/relations?only=strong").json()["matching"] == 1
    assert client.post("/api/knowledge/relations/from-usage", json={"dry_run": True}).json()["count"] == 0
    r = client.post("/api/knowledge/relations/apply", json={"proposals": [
        {"kind": "type", "from": app, "type": "relates_to", "target": "Redis", "new_type": "uses"}]}).json()
    assert r["applied"] == 1
    rel = read_note(app).relations[0]
    assert (rel.relation_type, rel.strength) == ("uses", 2)        # the strength stays with the link


def test_curate_duplicates_dismiss_and_merge_identical(client):
    from myclaw.knowledge.quality import find_duplicates
    from myclaw.knowledge.storage import get_knowledge_dir, list_notes

    text = "The backup runs every night at 02:00 with restic to the NAS."
    a, b = _note("Backup A", text), _note("Backup B", text)
    from myclaw.knowledge.storage import write_note

    c = _note("Meeting notes", "one")
    d = write_note("meeting-notes-copy", "Meeting Notes!", [], [], [], "default", "two")
    pairs = find_duplicates("default", None, 100, True)["pairs"]
    assert len(pairs) == 2
    pre = client.post("/api/knowledge/duplicates/merge-identical", json={"dry_run": True}).json()
    assert len(pre["pairs"]) == 1 and pre["merged"] == 0 and len(list_notes("default")) == 4
    assert client.post("/api/knowledge/duplicates/dismiss", json={"a": c, "b": d}).json() == {"ok": True}
    assert client.post("/api/knowledge/duplicates/dismiss", json={"a": c, "b": c}).status_code == 400
    done = client.post("/api/knowledge/duplicates/merge-identical", json={"dry_run": False}).json()
    assert done["merged"] == 1 and not done["errors"]
    left = {n.permalink for n in list_notes("default")}
    assert len(left) == 3 and len({a, b} & left) == 1
    assert len(list((get_knowledge_dir("default") / ".trash").iterdir())) == 1     # kept, not deleted
    assert find_duplicates("default", None, 100, True)["pairs"] == []


def test_agent_tools_are_registered_with_their_arguments(tuned):  # noqa: F811
    import inspect

    from myclaw.tools import TOOLS

    for name in ("strengthen_relation", "kb_tuning_show", "kb_tuning_set"):
        assert name in TOOLS
    assert list(inspect.signature(TOOLS["kb_tuning_set"]["func"]).parameters) == ["key", "value", "reason"]
    assert "safe" in TOOLS["kb_tuning_show"]["func"]()
