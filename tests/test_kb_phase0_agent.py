"""KB phase 0 (2026-10-23): agents see the text of notes, not only titles and facts.

Covers the automatic "## Relevant Knowledge" block, read_knowledge,
search_knowledge (AND/OR/NOT, tag:, snippets), build_context, the one
session-summary note per day, knowledge.enabled = false and the system prompt.
"""
import asyncio
import json
from datetime import datetime
from unittest.mock import patch

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    return tmp_path


def _kdir(home):
    return home / ".myclaw" / "knowledge" / "default"


@pytest.fixture
def agent(home):
    from myclaw.agent import Agent
    from myclaw.config import load_config

    a = Agent(load_config(), name="default")
    a._provider = object()
    a.set_gap_cache_enabled(False)
    return a


LONG = ("Zebrafish are small freshwater fish used in research because their embryos are "
        "transparent and develop quickly outside the mother. ") * 20


# ── 1. automatic context ──────────────────────────────────────────────────

def test_context_contains_note_text_and_respects_the_budget(agent):
    from myclaw.agent import KB_CONTEXT_BUDGET
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Observation

    for i in range(3):
        write_note(f"Zebrafish note {i}", content=LONG,
                   observations=[Observation("fact", f"zebrafish fact {i}-{j}", []) for j in range(5)])
    ctx = agent._search_knowledge_context_sync("tell me about zebrafish embryos", "default")
    assert ctx.startswith("## Relevant Knowledge")
    assert "transparent and develop quickly" in ctx      # the text, not only the title
    assert "…" in ctx                                    # shortened at a word boundary
    assert len(ctx) <= KB_CONTEXT_BUDGET + 10            # the whole block stays small
    first = ctx.split("**Zebrafish note")[1]
    assert first.count("- [fact]") <= 3


def test_context_strategy_two_uses_boolean_or(agent):
    calls = []

    def fake_search(query, user_id, limit=10, boolean=False):
        calls.append((query, boolean))
        return []

    with patch("myclaw.agent.search_notes", side_effect=fake_search):
        agent._search_knowledge_context_sync("quantum computing basics", "default")
    assert calls[0][1] is False                    # strategy 1: free text
    assert " OR " in calls[1][0] and calls[1][1] is True


def test_short_messages_are_no_knowledge_gap(agent):
    agent._kb_gaps.pop("default", None)
    agent._search_knowledge_context_sync("hi", "default")
    assert agent.get_kb_gaps("default") == []


def test_gap_hint_uses_newest_gap_and_writes_gap_file(agent, home, monkeypatch):
    import myclaw.agent as agent_mod

    gap_file = home / "gaps.jsonl"
    monkeypatch.setattr(agent_mod, "GAP_FILE", gap_file)
    agent._kb_gaps.pop("default", None)
    agent._search_knowledge_context_sync("old unknown subject matter", "default")
    agent._search_knowledge_context_sync("brand new unknown topic", "default")
    hint = agent._kb_gap_hint("default", had_kb_results=False)
    assert "brand new unknown topic" in hint
    lines = gap_file.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["query"] == "brand new unknown topic"
    agent._kb_gap_hint("default", had_kb_results=False)          # same topic again: logged once
    assert len(gap_file.read_text().splitlines()) == 1
    assert agent._kb_gap_hint("default", had_kb_results=True) == ""


# ── 2./3. tools ───────────────────────────────────────────────────────────

def _graph(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Relation

    write_note("Home server", content="The home server runs Ubuntu 24.04 in the basement.",
               tags=["homelab"])
    write_note("Pi", content="A Raspberry Pi 5 that collects sensor data.",
               relations=[Relation("connects_to", "home-server")], tags=["homelab", "sensors"])


def test_read_knowledge_shows_text_and_backlinks(home):
    from myclaw.tools.kb import read_knowledge

    _graph(home)
    out = read_knowledge("home-server")
    assert out.startswith("# Home server\nPermalink: home-server")
    assert "runs Ubuntu 24.04 in the basement" in out
    assert "Linked from:" in out and "pi" in out.split("Linked from:")[1].lower()
    assert "Tags: #homelab" in out


def test_read_knowledge_caps_long_text(home):
    from myclaw.knowledge import write_note
    from myclaw.tools.kb import READ_TEXT_CHARS, read_knowledge

    write_note("Big", content="word " * 5000)
    out = read_knowledge("big")
    assert "(text shortened" in out and len(out) < READ_TEXT_CHARS + 300


def test_search_knowledge_or_tag_filter_and_snippet(home):
    from myclaw.knowledge import write_note
    from myclaw.tools.kb import search_knowledge

    write_note("Alpha note", content="Apples grow in the orchard behind the house.")
    write_note("Beta note", content="Bananas ripen on the kitchen table.", tags=["fruit"])

    both = search_knowledge("apples OR bananas")
    assert "Alpha note" in both and "Beta note" in both
    assert "Bananas ripen on the kitchen table." in both          # one-line text snippet

    tagged = search_knowledge("tag:fruit")
    assert "Beta note" in tagged and "Alpha note" not in tagged
    assert "Beta note" in search_knowledge("bananas tag:fruit")
    assert "No results found" in search_knowledge("apples tag:fruit")


def _homelab(home):
    from myclaw.knowledge import write_note

    write_note("Home server backup", tags=["homelab"],
               content="We use restic to back up the home server every night to the NAS.")
    write_note("Router", tags=["homelab"], content="The router is an OpenWrt box in the hallway.")
    write_note("NAS box", tags=["homelab"], content="The nas holds 8 TB of photos and backups.")
    write_note("Shopping list", content="Milk, eggs and bread.")


def test_natural_sentence_search_finds_notes(home):
    """Review 2026-10-23: boolean=True must not make plain sentences find nothing
    (db.search_fts treats text without AND/OR/NOT/NEAR or quotes as free text)."""
    from myclaw.tools.kb import search_knowledge

    _homelab(home)
    out = search_knowledge("what do we use for the home server backup")
    assert "Home server backup" in out and "No results found" not in out


def test_tag_with_not_excludes_instead_of_searching(home):
    """Review 2026-10-23: "tag:homelab NOT nas" returned the NAS note."""
    from myclaw.tools.kb import search_knowledge

    _homelab(home)
    out = search_knowledge("tag:homelab NOT nas", limit=10)
    assert "Router" in out
    assert "NAS box" not in out and "Home server backup" not in out   # both mention nas
    assert "Shopping list" not in out                                 # no homelab tag

    with_nas = search_knowledge("tag:homelab nas", limit=10)
    assert "NAS box" in with_nas and "Router" not in with_nas and "Shopping list" not in with_nas


def test_not_alone_lists_every_note_without_the_term(home):
    """A leading NOT with no positive term means "every note except"."""
    from myclaw.tools.kb import search_knowledge

    _homelab(home)
    out = search_knowledge("NOT nas", limit=10)
    assert "Router" in out and "Shopping list" in out
    assert "NAS box" not in out and "Home server backup" not in out


def test_split_negations():
    from myclaw.tools.kb import _split_negations, _split_tag_terms

    assert _split_tag_terms("tag:homelab NOT nas") == ("NOT nas", ["homelab"])
    assert _split_tag_terms("tag:homelab AND NOT nas") == ("NOT nas", ["homelab"])
    assert _split_negations("NOT nas") == ("", ["nas"])
    assert _split_negations('NOT "old box" backup') == ("backup", ["old box"])
    assert _split_negations("backup NOT nas") == ("backup NOT nas", [])     # FTS5 handles it
    assert _split_tag_terms("why is it not working") == ("why is it not working", [])


def test_split_tag_terms():
    from myclaw.tools.kb import _split_tag_terms

    assert _split_tag_terms("docker AND tag:homelab") == ("docker", ["homelab"])
    assert _split_tag_terms("tag:a tag:B") == ("", ["a", "b"])
    assert _split_tag_terms('"exact phrase" OR x') == ('"exact phrase" OR x', [])


# ── 4. build_context ──────────────────────────────────────────────────────

def test_build_context_has_text_snippets_and_backlinks(home):
    from myclaw.knowledge.graph import build_context

    _graph(home)
    ctx = build_context("pi", depth=1)
    assert ctx.startswith("# Pi")
    assert "collects sensor data" in ctx
    assert "### Depth 1" in ctx and "runs Ubuntu 24.04" in ctx    # neighbour snippet
    back = build_context("home-server", depth=1)
    assert "## Linked from" in back and "(pi)" in back.split("## Linked from")[1]


# ── 5. session summaries ──────────────────────────────────────────────────

def test_two_summaries_on_one_day_make_one_note(agent, home):
    from myclaw.agent import Agent
    from myclaw.knowledge import read_note

    Agent._store_session_summary("User planned a trip.", "default", now=datetime(2026, 10, 23, 9, 0))
    Agent._store_session_summary("Trip booked.", "default", now=datetime(2026, 10, 23, 10, 30))
    files = sorted(p.name for p in _kdir(home).glob("session-summary-*.md"))
    assert files == ["session-summary-20261023.md"]
    note = read_note("session-summary-20261023")
    assert note.title == "Session summaries 2026-10-23"
    assert "### 09:00 UTC" in note.content and "### 10:30 UTC" in note.content
    assert note.content.index("User planned") < note.content.index("Trip booked")
    assert "session_summary" in note.tags
    Agent._store_session_summary("Next day.", "default", now=datetime(2026, 10, 24, 8, 0))
    assert len(list(_kdir(home).glob("session-summary-*.md"))) == 2


def test_day_note_keeps_only_the_newest_text(agent, home):
    from myclaw.agent import SESSION_SUMMARY_MAX_CHARS, Agent
    from myclaw.knowledge import read_note
    from myclaw.knowledge.parser import extract_body

    for i in range(30):
        Agent._store_session_summary(f"summary {i} " + "x " * 600, "default",
                                     now=datetime(2026, 10, 23, 12, i))
    body = extract_body(read_note("session-summary-20261023").content)
    assert len(body) <= SESSION_SUMMARY_MAX_CHARS
    assert body.startswith("### ") and "summary 29" in body and "summary 0 " not in body


def test_background_summary_writes_the_day_note(agent, home, monkeypatch):
    import myclaw.provider as prov_mod

    async def summary(provider, msgs, model):
        return "User asked about zebrafish."

    monkeypatch.setattr(prov_mod, "complete_text", summary)
    history = [{"role": "user", "content": f"message {i}"} for i in range(12)]
    asyncio.run(agent._background_summarize_context(history, "default", None))
    asyncio.run(agent._background_summarize_context(history, "default", None))
    files = list(_kdir(home).glob("session-summary-*.md"))
    assert len(files) == 1
    assert files[0].read_text().count("User asked about zebrafish.") == 2


# ── 6. knowledge.enabled = false ──────────────────────────────────────────

def test_knowledge_disabled_skips_automatic_parts(agent, home, monkeypatch):
    import myclaw.provider as prov_mod
    from myclaw.knowledge import write_note
    from myclaw.tools.kb import search_knowledge

    write_note("Zebrafish", content=LONG)
    agent.config.knowledge.enabled = False
    agent.config.knowledge.auto_extract = True
    assert agent._kb_enabled is False and agent._kb_auto_extract is False

    with patch("myclaw.agent.search_notes") as searched:
        assert agent._search_knowledge_context_sync("zebrafish embryos", "default") == ""
        searched.assert_not_called()

    async def summary(provider, msgs, model):
        raise AssertionError("no summary call when the knowledge base is off")

    monkeypatch.setattr(prov_mod, "complete_text", summary)
    history = [{"role": "user", "content": f"message {i}"} for i in range(12)]
    asyncio.run(agent._background_summarize_context(history, "default", None))
    assert not list(_kdir(home).glob("session-summary-*.md"))

    agent._record_kb_gap("default", "some gap topic")
    assert agent._kb_gap_hint("default", had_kb_results=False) == ""
    assert "Zebrafish" in search_knowledge("zebrafish")          # explicit tools still work


def test_kb_enabled_reads_config_defensively():
    from types import SimpleNamespace

    from myclaw.agent import Agent

    assert Agent._kb_enabled.fget(SimpleNamespace()) is True                       # no config
    assert Agent._kb_enabled.fget(SimpleNamespace(config=SimpleNamespace())) is True
    off = SimpleNamespace(config=SimpleNamespace(knowledge=SimpleNamespace(enabled=False)))
    assert Agent._kb_enabled.fget(off) is False


# ── 7. system prompt ──────────────────────────────────────────────────────

def test_system_prompt_never_asks_to_save_secrets():
    from myclaw.agent import SYSTEM_PROMPT

    mandate = SYSTEM_PROMPT.split("KNOWLEDGE GROWTH MANDATE")[1]
    assert "credentials" not in mandate.lower()
    assert "Never store passwords, API keys, tokens or other secrets" in SYSTEM_PROMPT
    assert "[[Other note]]" in SYSTEM_PROMPT
