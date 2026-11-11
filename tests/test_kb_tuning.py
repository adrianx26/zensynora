"""2026-11-11: tuning (safe limits, proposals, history, undo), temporal decay, search
feedback and re-ranking, chunking / fusion settings, bigger embedding models."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta

import pytest

from tests.test_v071_features import home  # noqa: F401  (fixture)


@pytest.fixture
def tuned(home, request):  # noqa: F811
    """Module options back to their defaults after the test."""
    from myclaw.config import load_config
    from myclaw.knowledge import decay, relevance, semantic

    def reset():
        decay.set_options(half_life_days=0.0, floor=0.3, categories={}, ai=False)
        decay._LAST_USED.clear()
        relevance._CACHE.clear()
        semantic.set_options(semantic_search=False, min_score=0.0, fusion=(1.0, 0.6, 2), chunking=(0, 150))

    reset()
    request.addfinalizer(reset)
    load_config(force_reload=True)
    return home


def _note(title, body="text", tags=()):
    from myclaw.knowledge.storage import write_note

    return write_note(title, title, [], [], list(tags), "default", body)


def _age(permalink, days):
    from myclaw.knowledge.db import KnowledgeDB

    with KnowledgeDB("default") as db:
        conn = db._get_connection()
        conn.execute("UPDATE entities SET updated_at = ? WHERE permalink = ?",
                     ((datetime.now() - timedelta(days=days)).isoformat(), permalink))
        conn.commit()


def _search(q, **kw):
    from myclaw.knowledge.storage import search_notes

    info = dict(kw.pop("info", {}))
    return [n.permalink for n in search_notes(q, "default", info=info, **kw)], info


# ── tuning ──────────────────────────────────────────────────────────────────
def test_user_sets_any_valid_value_and_can_undo(tuned):
    from myclaw.config import load_config
    from myclaw.knowledge import semantic, tuning

    r = tuning.set_values({"fusion_sem_weight": 2.5, "cache_similarity": 0.8, "decay_floor": 7, "nope": 1})
    assert r["applied"] == {"fusion_sem_weight": [0.6, 2.5], "cache_similarity": [0.92, 0.8]}   # outside safe: fine for you
    assert set(r["errors"]) == {"decay_floor", "nope"}
    cfg = load_config()
    assert cfg.knowledge.fusion_sem_weight == 2.5 and cfg.semantic_cache.similarity_threshold == 0.8
    assert semantic.fusion() == (1.0, 2.5, 2)                       # live at once
    from myclaw import provider

    assert provider._semantic_cache_cfg["similarity_threshold"] == 0.8
    hist = tuning.describe()["history"]
    assert hist[0]["actor"] == "user" and hist[0]["changes"]["fusion_sem_weight"] == [0.6, 2.5]
    tuning.undo()
    assert load_config().knowledge.fusion_sem_weight == 0.6 and semantic.fusion() == (1.0, 0.6, 2)
    assert load_config().semantic_cache.similarity_threshold == 0.92


def test_ai_and_agents_only_propose_by_default_and_are_bounded_in_safe_mode(tuned):
    from myclaw.config import load_config
    from myclaw.knowledge import tuning

    assert tuning.mode() == "suggest"
    r = tuning.set_values({"semantic_min_score": 0.5}, actor="agent:Mindy", reason="many unrelated matches")
    assert not r["applied"] and r["proposed"][0]["value"] == 0.5
    assert load_config().knowledge.semantic_min_score == 0.0        # nothing changed
    assert "Proposed" in tuning.kb_tuning_set("cache_similarity", "0.9", "repeated questions")
    assert "say why" in tuning.kb_tuning_set("cache_similarity", "0.9", "")
    assert "only the user" in tuning.kb_tuning_set("embedding_model", "BAAI/bge-large-en-v1.5", "x")
    assert len(tuning.describe()["proposals"]) == 2
    pid = next(p["id"] for p in tuning.describe()["proposals"] if p["key"] == "semantic_min_score")
    assert tuning.decide(pid, True)["applied"] == {"semantic_min_score": [0.0, 0.5]}
    pid = tuning.describe()["proposals"][0]["id"]
    assert tuning.decide(pid, False)["rejected"] and tuning.describe()["proposals"] == []
    assert load_config().semantic_cache.similarity_threshold == 0.92

    # safe mode: inside the safe range → applied and logged; outside → still a proposal
    from myclaw.config import CONFIG_FILE, load_encrypted_or_plain, save_config

    raw = load_encrypted_or_plain(CONFIG_FILE)
    raw.setdefault("knowledge", {})["tuning_ai"] = "safe"
    save_config(raw)
    load_config(force_reload=True)
    assert "✅" in tuning.kb_tuning_set("cache_similarity", "0.9", "repeated questions")
    assert load_config().semantic_cache.similarity_threshold == 0.9
    assert "Proposed" in tuning.kb_tuning_set("cache_similarity", "0.6", "more reuse")      # unsafe
    assert load_config().semantic_cache.similarity_threshold == 0.9
    r = tuning.set_values({"embedding_model": "BAAI/bge-large-en-v1.5", "chunk_chars": 0,
                           "decay_categories": {"News": 30, "person": 0}}, actor="ai", reason="r")
    assert set(r["applied"]) == {"decay_categories"} and r["proposed"][0]["key"] == "embedding_model"
    assert load_config().knowledge.decay_categories == {"news": 30.0, "person": 0.0}
    last = tuning.describe()["history"][0]
    assert last["actor"] == "ai" and "cache_similarity" in tuning.kb_tuning_show()


def test_ai_suggest_reads_measurements_and_keeps_to_the_rules(tuned, monkeypatch):
    from myclaw.config import load_config
    from myclaw.knowledge import tuning

    _note("Weather today", "rain", ["news"])
    seen: list = []

    async def fake(config, messages, provider=None, model=None):
        seen.append(messages)
        return json.dumps({"summary": "Old news is rarely used.", "changes": [
            {"key": "decay_half_life_days", "value": 180, "why": "most notes are older than a year"},
            {"key": "decay_categories", "value": {"news": 30, "invented": 5}, "why": "news go stale"},
            {"key": "cache_similarity", "value": 0.5, "why": "not in this scope"},
            {"key": "rm -rf", "value": 1}]}), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)
    out = asyncio.run(tuning.ai_suggest(load_config(), "default", scope="decay"))
    assert not out["applied"] and {p["key"] for p in out["proposed"]} == {"decay_half_life_days", "decay_categories"}
    cats = next(p for p in out["proposed"] if p["key"] == "decay_categories")
    assert cats["value"] == {"news": 30.0} and cats["reason"] == "news go stale"        # no invented names
    assert "not instructions" in seen[0][0]["content"] and '"tags"' in seen[0][1]["content"]
    assert "rain" not in seen[0][1]["content"]                                           # no note text sent
    assert tuning.describe()["decay_reasons"][0]["summary"] == "Old news is rarely used."

    async def down(*a, **k):
        raise RuntimeError("429")

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", down)
    with pytest.raises(tuning.AIUnavailable):
        asyncio.run(tuning.ai_suggest(load_config(), "default", scope="decay"))


# ── decay ───────────────────────────────────────────────────────────────────
def test_decay_off_changes_nothing_and_on_ranks_stale_notes_lower(tuned):
    from myclaw.knowledge import decay, usage

    old = _note("Router setup old", "router router router setup guide")
    new = _note("Router notes", "router setup")
    ever = _note("Router reference", "router router router router setup manual", ["evergreen"])
    used = _note("Router backup", "router router router setup backup")
    for p in (old, ever, used):
        _age(p, 400)
    before, info = _search("router setup")
    assert "explain" not in info and set(before) == {old, new, ever, used}
    assert decay.fact_factor("status", 400) is None

    usage.record("read", "context", base="default", notes=[used], agent="a")   # still used → stays fresh
    usage.flush()
    decay.set_options(half_life_days=90, floor=0.3)
    after, info = _search("router setup", info={"explain": True})
    rows = {r["permalink"]: r for r in info["explain"]}
    assert rows[old]["decay"] == 0.3 and rows[old]["age_days"] == 400
    assert rows[new]["decay"] == rows[ever]["decay"] == rows[used]["decay"] == 1.0
    assert after[-1] == old and set(after) == set(before)                 # lower, never hidden
    assert decay.fact_factor("status", 90) == 0.5
    decay.set_options(categories={"status": 0, "howto": 30})
    assert decay.fact_factor("status", 4000) == 1.0 and decay.half_life_for(["howto", "x"]) == 30
    assert decay.half_life_for(["howto", "evergreen"]) == 0
    d = decay.digest("default")
    assert d["notes"] == 4 and d["ages"][-1] == {"age": "over a year", "notes": 3, "used_last_90_days": 1}
    assert d["tags"][0] == {"name": "evergreen", "count": 1, "median_age_days": 400}


def test_weekly_decay_review_is_opt_in_and_runs_once_a_week(tuned, monkeypatch):
    from myclaw.knowledge import decay, tuning

    calls = []

    async def fake(config, base="default", scope="all", actor="ai"):
        calls.append((scope, actor))
        return {"applied": {}}

    monkeypatch.setattr(tuning, "ai_suggest", fake)
    assert asyncio.run(decay.weekly_review(None)) is None and not calls     # off by default
    decay.set_options(ai=True)
    assert asyncio.run(decay.weekly_review(None)) == {"applied": {}}
    assert asyncio.run(decay.weekly_review(None)) is None and len(calls) == 1
    tuning.mark("decay_review_ts", time.time() - 8 * 86400)
    asyncio.run(decay.weekly_review(None))
    assert len(calls) == 2 and calls[0][0] == "decay"


# ── feedback / re-rank ──────────────────────────────────────────────────────
def test_thumbs_reorder_the_same_query_only(tuned):
    from myclaw.knowledge import relevance

    a = _note("Backup plan", "backup backup backup restic nightly")
    b = _note("Backup to NAS", "backup restic")
    c = _note("Restic install", "restic backup tool")
    first, _ = _search("restic backup")
    other, _ = _search("backup")
    relevance.vote("default", "Restic  BACKUP!", first[-1], 1)
    relevance.vote("default", "restic backup", first[0], -1)
    again, info = _search("restic backup", info={"explain": True})
    assert again[0] == first[-1] and again[-1] == first[0]
    assert {r["vote"] for r in info["explain"]} == {1, -1, 0}
    from myclaw.knowledge import decay

    _age(first[-1], 900)
    decay.set_options(half_life_days=30, floor=0.1)                       # your 👍 also wins against decay
    assert _search("restic backup")[0][0] == first[-1]
    decay.set_options(half_life_days=0)
    assert _search("backup")[0] == other                                  # another query: untouched
    fb = relevance.feedback("default")
    assert fb["queries"] == 1 and fb["labelled"] == 1 and len(fb["items"]) == 2
    relevance.vote("default", "restic backup", first[-1], 0)
    relevance.vote("default", "restic backup", first[0], 0)
    assert _search("restic backup")[0] == first and {a, b, c} == set(first)
    assert "at least 5" in relevance.autotune("default")["message"]


def test_autotune_measures_marked_searches(tuned):
    from myclaw.knowledge import relevance, semantic

    semantic.set_embedder_factory(lambda name: semantic.HashEmbedder(name))
    try:
        semantic.set_options(semantic_search=True, min_score=0.3)
        topics = ["nginx proxy", "postgres backup", "python venv", "docker compose", "ssh keys", "wifi router"]
        for t in topics:
            _note(f"{t} guide", f"how to set up {t} step by step")
        semantic.backfill_embeddings("default")
        for t in topics:
            relevance.vote("default", f"{t} setup", f"{t.replace(' ', '-')}-guide", 1)
        out = relevance.autotune("default")
        assert out["labelled"] == 6 and 0 < out["current"]["mrr"] <= 1 and out["best"]["mrr"] >= out["current"]["mrr"]
        assert set(out["changes"]) <= {"fusion_fts_weight", "fusion_sem_weight", "fusion_keep_fts", "semantic_min_score"}
    finally:
        semantic.set_embedder_factory(None)


# ── chunking / fusion / models ──────────────────────────────────────────────
def test_fusion_settings_change_the_mixed_order(tuned):
    from myclaw.knowledge import semantic

    assert semantic.fuse([1, 2, 3], [9, 8, 1]) == [1, 2, 3, 9, 8]
    semantic.set_options(fusion=(1.0, 1.2, 0))
    assert semantic.fuse([1, 2, 3], [9, 8, 1]) == [1, 9, 8, 2, 3]
    assert semantic.fuse([1, 2, 3], [9, 8, 1], w_fts=1.0, w_sem=0.6, keep_fts=2) == [1, 2, 3, 9, 8]


def test_a_chunking_change_re_embeds_every_note(tuned):
    from myclaw.knowledge import semantic
    from myclaw.knowledge.db import KnowledgeDB

    semantic.set_embedder_factory(lambda name: semantic.HashEmbedder(name))
    try:
        semantic.set_options(semantic_search=True)
        _note("Long", "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12)))
        semantic.backfill_embeddings("default")

        def state():
            with KnowledgeDB("default") as db:
                c = db._get_connection()
                return (c.execute("SELECT model, chunks FROM note_embed_state").fetchone(),
                        len(semantic._stale_ids(c, semantic.model_name())))

        (tag, chunks), stale = state()
        assert tag == semantic.model_name() == semantic.state_tag() and stale == 0
        semantic.set_options(chunking=(400, 50))
        assert semantic.chunking() == (400, 50) and state()[1] == 1          # waits for re-embedding
        semantic.backfill_embeddings("default")
        (tag2, chunks2), stale = state()
        assert tag2.endswith("|400/50") and chunks2 > chunks and stale == 0
        semantic.set_options(chunking=(0, 150))
        assert state()[1] == 1 and semantic.state_tag() == semantic.model_name()
    finally:
        semantic.set_embedder_factory(None)


def test_bigger_models_are_offered_and_e5_gets_its_prefixes(tuned):
    from myclaw.knowledge import semantic

    assert semantic.MODELS["BAAI/bge-large-en-v1.5"].dim == 1024
    e5 = semantic.model_info("intfloat/multilingual-e5-large")
    assert (e5.query_prefix, e5.passage_prefix) == ("query: ", "passage: ")
    assert semantic.model_info("intfloat/e5-base-v2").query_prefix == "query: "            # typed by hand
    assert semantic.model_info("BAAI/bge-small-en-v1.5").query_prefix == ""
    assert semantic.model_info("some/unknown-model").passage_prefix == ""


# ── 2026-11-12 (independent review) ─────────────────────────────────────────
def test_nan_and_infinity_are_refused_everywhere(tuned):
    from myclaw.knowledge import tuning

    assert "Error" in tuning.kb_tuning_set("decay_categories", '{"news": NaN}', "why")
    assert "Error" in tuning.kb_tuning_set("decay_categories", '{"news": Infinity}', "why")
    assert tuning.set_values({"decay_floor": float("nan"), "cache_similarity": float("inf")})["errors"].keys() == {
        "decay_floor", "cache_similarity"}
    assert tuning.describe()["proposals"] == []
    import json as _json

    _json.dumps(tuning.describe(), allow_nan=False)


def test_undo_puts_back_the_exact_old_values(tuned):
    from myclaw.config import CONFIG_FILE, load_config, load_encrypted_or_plain, save_config
    from myclaw.knowledge import tuning

    raw = load_encrypted_or_plain(CONFIG_FILE) if CONFIG_FILE.exists() else {}
    raw.setdefault("knowledge", {}).update(semantic_min_score=0.123456, decay_categories={"My Tag": 45.5})
    save_config(raw)
    load_config(force_reload=True)
    tuning.set_values({"semantic_min_score": 0.5, "decay_categories": {"news": 30}})
    tuning.undo()
    k = load_config().knowledge
    assert k.semantic_min_score == 0.123456 and k.decay_categories == {"My Tag": 45.5}


def test_a_damaged_state_file_is_set_aside_and_odd_entries_are_skipped(tuned):
    from myclaw.knowledge import tuning

    tuning.set_values({"decay_floor": 0.5})
    path = tuning._path()
    path.write_text(path.read_text()[:40])                       # truncated
    assert tuning.state()["history"] == []
    assert len(list(path.parent.glob("knowledge-tuning.json.bad-*"))) == 1     # kept, not overwritten
    path.write_text('{"history": "x", "proposals": [{"id": 3}, {"id": 4, "key": "decay_floor", "value": 0.6}], "seq": "7"}')
    st = tuning.state()
    assert st["history"] == [] and [p["id"] for p in st["proposals"]] == [4] and st["seq"] == 4
    assert tuning.set_values({"decay_floor": 0.4})["applied"] and tuning.describe()["history"][0]["id"] == 5


def test_an_agent_adds_decay_categories_but_never_removes_yours(tuned):
    from myclaw.config import CONFIG_FILE, load_config, load_encrypted_or_plain, save_config
    from myclaw.knowledge import tuning

    tuning.set_values({"decay_categories": {"person": 0}})
    raw = load_encrypted_or_plain(CONFIG_FILE)
    raw["knowledge"]["tuning_ai"] = "safe"
    save_config(raw)
    load_config(force_reload=True)
    tuning.set_values({"decay_categories": {"news": 30}}, actor="agent:x", reason="r")
    assert load_config().knowledge.decay_categories == {"person": 0.0, "news": 30.0}


def test_marks_have_their_own_file_and_survive_a_deleted_or_broken_usage_log(tuned):
    from myclaw.knowledge import relevance, usage

    relevance.vote("default", "restic backup", "a", 1)
    assert relevance.db_path().name == "knowledge-feedback.db" and relevance.db_path() != usage.db_path()
    usage.db_path().write_bytes(b"not a database")
    assert relevance.feedback("default")["labelled"] == 1
    relevance.db_path().write_bytes(b"not a database")
    relevance._CACHE.clear()
    fb = relevance.feedback("default")
    assert fb["labelled"] == 0 and "could not be read" in fb["error"]
    assert relevance.votes_for("default", "restic backup") == {}
    with pytest.raises(ValueError, match="could not be saved"):
        relevance.vote("default", "q", "a", 1)
