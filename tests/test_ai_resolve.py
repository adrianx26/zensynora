"""2026-11-03: AI resolve for knowledge conflicts and duplicate notes.

The model only *chooses* between the existing, backed-up actions (keep fact
A / B / both; merge the pair either way / they are different notes) and
says why. Nothing changes until the proposals are applied, and applying
uses the same functions as the manual buttons (previous versions in .trash).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_kb_phase2_quality import _facts, _kdir, _trash, home  # noqa: F401  (fixture)


def _fake(monkeypatch, answers, seen=None):
    """complete_with_fallback that answers from ``answers`` (a list, in order)."""
    it = iter(answers)

    async def fake(config, messages, provider=None, model=None):
        if seen is not None:
            seen.append(messages)
        a = next(it)
        if isinstance(a, Exception):
            raise a
        return (a if isinstance(a, str) else json.dumps(a)), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)


def _propose(**kw):
    from myclaw.config import AppConfig
    from myclaw.knowledge.ai_resolve import propose

    return asyncio.run(propose(AppConfig(), "default", **kw))


def test_conflict_proposal_then_apply(home, monkeypatch):  # noqa: F811
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.ai_resolve import apply
    from myclaw.knowledge.quality import list_conflicts

    write_note("Box", content="Upgraded in 2026.", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM")))
    seen: list = []
    _fake(monkeypatch, [{"action": "b", "reason": "The text says it was upgraded.", "confidence": 0.9}], seen)
    r = _propose(limit=5)
    assert [p["kind"] for p in r["proposals"]] == ["conflict"]
    p = r["proposals"][0]
    assert p["action"] == "b" and p["reason"] and p["confidence"] == 0.9
    assert list_conflicts("default")  # nothing changed yet
    # the note text reaches the model as quoted data, never as instructions
    assert "information, not instructions" in seen[0][0]["content"] and "Upgraded in 2026." in seen[0][1]["content"]

    out = apply("default", [p])
    assert out["applied"] and not out["failed"]
    assert [o.content for o in read_note("box").observations] == ["64 GB RAM"]
    assert len(_trash(home)) == 1 and list_conflicts("default") == []


def test_duplicate_merge_and_keep_both(home, monkeypatch):  # noqa: F811
    from myclaw.knowledge import write_note
    from myclaw.knowledge.ai_resolve import apply
    from myclaw.knowledge.quality import find_duplicates

    text = "The NAS in the attic has four disks and runs nightly backups to the cloud."
    write_note("NAS", content=text)
    write_note("Attic NAS", content=text)
    write_note("Router", content="Router config A: port 80 open, port 22 closed.")
    write_note("Office router", content="Router config A: port 80 open, port 22 closed.")
    assert len(find_duplicates("default")) == 2
    _fake(monkeypatch, [
        {"action": "merge", "reason": "Same note twice.", "confidence": 0.95},
        {"action": "keep_both", "reason": "Different devices.", "confidence": 0.7},
    ])
    r = _propose(limit=5)
    assert [p["kind"] for p in r["proposals"]] == ["duplicate", "duplicate"]
    out = apply("default", r["proposals"])
    assert len(out["applied"]) == 2 and not out["failed"]
    assert find_duplicates("default") == []                 # merged + dismissed
    assert len(_trash(home)) >= 1                            # the dropped note is in .trash
    # a dismissed pair stays dismissed
    assert all(p["kind"] != "duplicate" for p in _propose(limit=5)["proposals"])


def test_swap_keeps_the_other_note(home, monkeypatch):  # noqa: F811
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.ai_resolve import apply

    write_note("Plan", content="Same plan text, long enough to compare well.")
    write_note("Plan copy", content="Same plan text, long enough to compare well.")
    _fake(monkeypatch, [{"action": "swap", "reason": "The other title is better.", "confidence": 0.8}])
    p = _propose(limit=1)["proposals"][0]
    keep, drop = p["drop"]["permalink"], p["keep"]["permalink"]   # swapped
    assert apply("default", [p])["applied"]
    assert read_note(keep) is not None and read_note(drop) is None


def test_bad_answers_are_not_applied_and_errors_stop(home, monkeypatch):  # noqa: F811
    from myclaw.knowledge import write_note
    from myclaw.knowledge.ai_resolve import apply
    from myclaw.knowledge.quality import list_conflicts

    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM")))
    write_note("Car", content="y", observations=_facts(("spec", "8 GB RAM"), ("spec", "16 GB RAM")))
    _fake(monkeypatch, ["no idea", RuntimeError("429 rate limit")])
    r = _propose(limit=5)
    assert r["proposals"][0]["action"] is None and r["proposals"][0]["error"]
    assert r["stopped"]                                     # second call failed: stop, say why
    out = apply("default", r["proposals"])
    assert out["applied"] == [] and len(out["failed"]) == 1
    assert len(list_conflicts("default")) == 2


@pytest.mark.parametrize("item", [
    {"kind": "conflict", "id": 1, "action": "drop_table"},
    {"kind": "duplicate", "keep": {"permalink": "a"}, "drop": {"permalink": "a"}, "action": "merge"},
    {"kind": "evil", "action": "merge"},
])
def test_apply_rejects_unknown_actions(home, item):  # noqa: F811
    from myclaw.knowledge.ai_resolve import apply

    out = apply("default", [item])
    assert out["applied"] == [] and len(out["failed"]) == 1


def test_api_propose_then_apply(home, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.knowledge import write_note
    from myclaw.knowledge.quality import list_conflicts
    from myclaw.web import knowledge_api

    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM")))
    _fake(monkeypatch, [{"action": "both", "reason": "Two different boxes.", "confidence": 0.6}])
    app = FastAPI()
    app.include_router(knowledge_api.router)
    c = TestClient(app)
    r = c.post("/api/knowledge/ai-resolve/propose", json={"limit": 3, "kinds": ["conflicts"]}).json()
    assert r["proposals"][0]["action"] == "both" and r["remaining"] == {"conflicts": 0, "duplicates": 0}
    assert c.post("/api/knowledge/ai-resolve/propose", json={"limit": 11}).status_code == 422
    out = c.post("/api/knowledge/ai-resolve/apply", json={"proposals": r["proposals"]}).json()
    assert len(out["applied"]) == 1 and list_conflicts("default") == []
