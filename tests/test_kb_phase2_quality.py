"""KB phase 2 (2026-10-24): the quality layer of the knowledge base.

Fact sources, category registry, conflict detection, hub penalty, duplicate
notes, and their agent tools / API routes. Real temp HOME (files + index).
"""
import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    from myclaw.knowledge import quality

    monkeypatch.setattr(quality, "_OPTIONS", dict(quality._OPTIONS, detect_conflicts=True,
                                                   hub_penalty=True, hub_degree=25))
    return tmp_path


def _kdir(home: Path) -> Path:
    return home / ".myclaw" / "knowledge" / "default"


def _trash(home: Path) -> list:
    t = _kdir(home) / ".trash"
    return sorted(t.glob("*.md")) if t.exists() else []


# ── Step 1: fact sources ────────────────────────────────────────────────────

def test_source_suffix_is_parsed_and_written_back(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.parser import Observation, parse_observations

    write_note("Box", content="A box.", observations=[
        Observation("spec", "32 GB RAM", ["hw"], provenance_path="tool read_file /etc/x"),
        Observation("fact", "Plain fact", [])])
    raw = (_kdir(home) / "box.md").read_text()
    assert "- [spec] 32 GB RAM #hw (source: tool read_file /etc/x)" in raw
    assert "- [fact] Plain fact\n" in raw
    n = read_note("box")
    assert [(o.content, o.tags, o.provenance_path) for o in n.observations] == [
        ("32 GB RAM", ["hw"], "tool read_file /etc/x"), ("Plain fact", [], None)]
    # source with parentheses and '#' (URL), tags after it, any case
    o = parse_observations("- [url] Docs page (Source: tool browse https://e.org/A_(b)#x) #docs")[0]
    assert (o.content, o.tags, o.provenance_path) == ("Docs page", ["docs"], "tool browse https://e.org/A_(b)#x")
    # stored in the index too
    from myclaw.knowledge.db import KnowledgeDB
    with KnowledgeDB("default") as db:
        e = db.get_entity_by_permalink("box")
        assert [x.provenance_path for x in db.get_observations(e.id)] == ["tool read_file /etc/x", None]


@pytest.mark.parametrize("line,content", [
    ("- [fact] Uses Python (3.12)", "Uses Python (3.12)"),
    ("- [fact] Fast (really) #perf", "Fast (really)"),
    ("- [fact] (source: only a source)", "(source: only a source)"),
    ("- [fact] The (source: x) is in the middle", "The (source: x) is in the middle"),
])
def test_other_parentheses_stay_part_of_the_fact(line, content):
    from myclaw.knowledge.parser import Observation, generate_markdown, parse_observations, Note

    o = parse_observations(line)[0]
    assert o.content == content and o.provenance_path is None
    md = generate_markdown(Note(name="n", permalink="n", title="N", content="", observations=[o]))
    assert parse_observations(md)[0].content == content


def test_write_to_knowledge_third_field_is_the_source(home):
    from myclaw.knowledge import read_note
    from myclaw.tools.kb import read_knowledge, write_to_knowledge

    write_to_knowledge("Srv", "A server.", tags="x",
                       observations="spec | 64 GB RAM | tool shell free -g\nfact | no source")
    n = read_note("srv")
    assert [(o.content, o.provenance_path) for o in n.observations] == [
        ("64 GB RAM", "tool shell free -g"), ("no source", None)]
    out = read_knowledge("srv")
    assert "- [spec] 64 GB RAM (source: tool shell free -g)" in out


def test_extractors_record_the_source(home):
    from myclaw.knowledge import read_note
    from myclaw.knowledge.extract import save_items
    from myclaw.knowledge.quality import chat_source, tool_source

    assert chat_source().startswith("chat 20")
    assert tool_source("browse", {"url": "https://x.org/a"}) == "tool browse https://x.org/a"
    assert tool_source("read_file", json.dumps({"path": "/etc/hosts"})) == "tool read_file /etc/hosts"
    assert tool_source("delegate", "not json") == "tool delegate"
    items = [{"title": "Ollama", "content": "Local models.", "tags": "ai",
              "facts": [{"category": "Settings", "text": "listens on port 11434"}]}]
    assert save_items(items, "default", ["auto-extracted"], 5, "[T]", "tool browse https://o.ai") == 1
    n = read_note("ollama")
    assert n.observations[0].provenance_path == "tool browse https://o.ai"
    assert "(source: tool browse https://o.ai)" in (_kdir(home) / "ollama.md").read_text()


def test_note_api_returns_fact_source(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Observation
    from myclaw.web import knowledge_api

    write_note("Box", content="x", observations=[Observation("spec", "1 TB disk", [], provenance_path="chat 2026-10-24")])
    app = FastAPI()
    app.include_router(knowledge_api.router)
    r = TestClient(app).get("/api/knowledge/note/box")
    assert r.status_code == 200
    assert r.json()["observations"][0]["source"] == "chat 2026-10-24"


# ── Step 2: categories ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,norm", [
    ("Fact", "fact"), (" facts ", "fact"), ("Settings", "setting"), ("config", "setting"),
    ("Configuration", "setting"), ("prefs", "preference"), ("Preference", "preference"),
    ("specs", "spec"), ("Specification", "spec"), ("URL", "url"), ("link", "url"),
    ("howto", "how-to"), ("How to", "how-to"), ("how_to", "how-to"), ("status", "status"),
    ("Addresses", "address"), ("libraries", "library"), ("os", "os"), ("news", "news"),
    ("user preferences", "user-preference"), ("people", "person"), ("", ""),
])
def test_normalize_category(raw, norm):
    from myclaw.knowledge.quality import normalize_category

    assert normalize_category(raw) == norm


def _facts(*pairs):
    from myclaw.knowledge.parser import Observation

    return [Observation(c, t, []) for c, t in pairs]


def test_category_column_registry_and_suggestions(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.db import KnowledgeDB
    from myclaw.knowledge.quality import list_categories

    write_note("A", content="a", observations=_facts(("spec", "1"), ("spec", "2"), ("spec", "3"),
                                                    ("Specs", "4"), ("preference", "p1")))
    write_note("B", content="b", observations=_facts(("preference", "p2"), ("preference", "p3"),
                                                    ("preferance", "p4"), ("port", "x"), ("post", "y"),
                                                    ("port", "z"), ("port", "w")))
    # the file keeps the spelling as written; the index stores the normalised one
    assert "- [Specs] 4" in (_kdir(home) / "a.md").read_text()
    with KnowledgeDB("default") as db:
        rows = db._get_connection().execute(
            "SELECT category, category_norm FROM observations WHERE content = '4'").fetchall()
        assert [(r[0], r[1]) for r in rows] == [("Specs", "spec")]
    out = list_categories("default")
    cats = {c["name"]: c for c in out["categories"]}
    assert cats["spec"]["count"] == 4 and cats["spec"]["variants"] == {"spec": 3, "Specs": 1}
    assert cats["preference"]["count"] == 3 and cats["preferance"]["count"] == 1
    sugg = {(s["from"], s["to"]) for s in out["suggestions"]}
    assert ("Specs", "spec") in sugg and ("preferance", "preference") in sugg
    assert not any(s["from"] == "post" for s in out["suggestions"])  # short words: no guessing


def test_category_column_backfilled_on_old_database(home):
    """A database made before phase 2 (no category_norm, no new tables) is upgraded in place."""
    import sqlite3
    from myclaw.knowledge import write_note
    from myclaw.knowledge.db import KnowledgeDB

    write_note("X", content="x", observations=_facts(("Settings", "port 1")))
    db_path = home / ".myclaw" / "knowledge_default.db"
    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX idx_observations_category_norm")
    conn.execute("ALTER TABLE observations DROP COLUMN category_norm")
    conn.execute("DROP TABLE observation_schema")
    conn.execute("DROP TABLE conflicts")
    conn.commit()
    conn.close()
    with KnowledgeDB("default") as db:
        c = db._get_connection()
        assert c.execute("SELECT category, category_norm FROM observations").fetchone()[:] == ("Settings", "setting")
        assert c.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 0


def test_merge_category_preview_then_apply(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.quality import merge_category

    write_note("A", content="Text A.", observations=_facts(("Specs", "4 cores"), ("spec", "8 GB")))
    write_note("B", content="Text B.", observations=_facts(("specs", "2 cores")))
    before = {p: (_kdir(home) / f"{p}.md").read_text() for p in ("a", "b")}
    prev = merge_category("default", "Specs", "spec", dry_run=True)
    assert prev["dry_run"] and prev["facts"] == 2
    assert {n["permalink"] for n in prev["notes"]} == {"a", "b"}
    assert prev["notes"][0]["changes"][0] == {"before": "[Specs] 4 cores", "after": "[spec] 4 cores"}
    # nothing changed on disk by the preview
    assert {p: (_kdir(home) / f"{p}.md").read_text() for p in ("a", "b")} == before
    assert _trash(home) == []
    done = merge_category("default", "Specs", "spec", dry_run=False)
    assert not done["dry_run"] and done["facts"] == 2
    assert [o.category for o in read_note("a").observations] == ["spec", "spec"]
    assert read_note("a").content.count("Text A.") == 1
    assert [o.category for o in read_note("b").observations] == ["spec"]
    trashed = _trash(home)
    assert len(trashed) == 2 and any("[Specs] 4 cores" in t.read_text() for t in trashed)
    with pytest.raises(ValueError):
        merge_category("default", "", "spec")


# ── Step 3: conflicts ───────────────────────────────────────────────────────

CONFLICTS = [
    ("32 GB RAM", "64 GB RAM"),
    ("Ollama listens on port 11434", "Ollama listens on port 8080"),
    ("Ollama port: 11434", "Ollama port: 8080"),
    ("RAM = 32GB", "RAM = 64GB"),
    ("Default model is llama3", "Default model is qwen2.5"),
    ("Runs Ubuntu 22.04", "Runs Ubuntu 24.04"),
    ("Home server OS: Debian", "Home server OS: Ubuntu"),
    ("mindy01 has 32 GB RAM", "mindy01 has 64 GB RAM"),
]

NOT_CONFLICTS = [
    ("32 GB RAM", "32 GB RAM"),                                  # identical
    ("32 GB RAM", "32gb ram."),                                  # same, other spelling
    ("Has 32 GB RAM", "RAM is 32 GB"),                           # rephrased
    ("32 GB RAM", "32 GB RAM DDR4"),                             # subset / superset
    ("Port: 8080", "Port: 8080, 8443"),                          # value contains the other
    ("Server 1 has 32 GB", "Server 2 has 64 GB"),                # different subjects
    ("Lives in Berlin", "Works in Munich"),                      # unrelated
    ("Python 3.12 installed", "Node 20 installed"),              # versions, unrelated facts
    ("Backup ran on 2026-10-01", "Backup ran on 2026-10-02"),    # dates = history
    ("Released in 2019", "Released in 2021"),
    ("Meeting: 10:00 monday", "Meeting: 14:00 monday"),          # times
    ("v1.2 released 2020", "v1.3 released 2021"),
    ("Step 1: install docker", "Step 2: install docker"),        # ordinals
    ("Python is installed", "Python is fast"),                   # adjectives, not values
    ("Adrian is the owner", "Adrian is a developer"),
    ("Note: restart weekly", "Note: check logs"),                # generic key
    ("ollama port is not 8080", "ollama port is 11434"),         # negation
    ("Likes coffee", "Likes tea"),
    ("See https://a.org/x", "See https://b.org/y"),
]


@pytest.mark.parametrize("a,b", CONFLICTS)
def test_conflict_true_positives(a, b):
    from myclaw.knowledge.quality import conflict_kind

    assert conflict_kind(a, b) in ("number", "value")
    assert conflict_kind(b, a) in ("number", "value")


@pytest.mark.parametrize("a,b", NOT_CONFLICTS)
def test_conflict_must_not_flag(a, b):
    from myclaw.knowledge.quality import conflict_kind

    assert conflict_kind(a, b) is None
    assert conflict_kind(b, a) is None


def test_only_same_category_is_compared():
    from myclaw.knowledge.quality import find_conflicts

    obs = _facts(("spec", "32 GB RAM"), ("plan", "64 GB RAM"), ("Specs", "64 GB RAM"))
    assert find_conflicts(obs) == [(0, 2, "number")]  # spec ~ Specs; "plan" is another category


def test_conflict_flagged_on_index_and_closed_when_fixed(home):
    from myclaw.knowledge import update_note, write_note
    from myclaw.knowledge.quality import list_conflicts

    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM"),
                                                      ("fact", "Lives in the attic")))
    open_ = list_conflicts("default")
    assert len(open_) == 1
    c = open_[0]
    assert (c["permalink"], c["category"], c["a"], c["b"], c["kind"]) == (
        "box", "spec", "32 GB RAM", "64 GB RAM", "number")
    assert list_conflicts("default", permalink="other") == []
    # the user edits the note by hand: the pair is gone -> closed automatically
    update_note("box", observations=_facts(("spec", "64 GB RAM")))
    assert list_conflicts("default") == []
    assert list_conflicts("default", status="resolved")[0]["id"] == c["id"]


def test_dismissed_stays_dismissed_also_after_full_rebuild(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.quality import list_conflicts, resolve_conflict
    from myclaw.knowledge.sync import sync_knowledge

    write_note("Box", content="x", observations=_facts(("setting", "web port: 80"), ("setting", "web port: 443")))
    cid = list_conflicts("default")[0]["id"]
    before = (_kdir(home) / "box.md").read_text()
    r = resolve_conflict("default", cid, "both")
    assert r["ok"] and not r["changed"]
    assert (_kdir(home) / "box.md").read_text() == before
    assert list_conflicts("default") == []
    sync_knowledge("default", force=True)
    write_note("Box", content="x again", observations=_facts(("setting", "web port: 80"), ("setting", "web port: 443")))
    assert list_conflicts("default") == []
    assert [c["id"] for c in list_conflicts("default", status="dismissed")] == [cid]


def test_keep_a_removes_b_from_the_file_with_backup(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.quality import list_conflicts, resolve_conflict

    write_note("Box", content="Text stays.", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM"),
                                                                ("fact", "Blue case")))
    cid = list_conflicts("default")[0]["id"]
    r = resolve_conflict("default", cid, "b")
    assert r["changed"] and "64 GB RAM" in r["message"]
    n = read_note("box")
    assert [o.content for o in n.observations] == ["64 GB RAM", "Blue case"]
    assert "Text stays." in n.content
    trashed = _trash(home)
    assert len(trashed) == 1 and "32 GB RAM" in trashed[0].read_text()
    assert list_conflicts("default") == []
    again = resolve_conflict("default", cid, "a")
    assert not again["changed"] and "already" in again["message"]
    with pytest.raises(ValueError):
        resolve_conflict("default", cid, "x")
    with pytest.raises(LookupError):
        resolve_conflict("default", 9999, "a")


def test_detect_conflicts_switch_off(home, monkeypatch):
    from myclaw.knowledge import quality, write_note

    monkeypatch.setitem(quality._OPTIONS, "detect_conflicts", False)
    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM")))
    assert quality.list_conflicts("default") == []


def test_deleting_the_note_closes_its_conflicts(home):
    from myclaw.knowledge import delete_note, write_note
    from myclaw.knowledge.quality import list_conflicts

    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM")))
    assert len(list_conflicts("default")) == 1
    delete_note("box")
    assert list_conflicts("default") == []


# ── Step 4: hub penalty ─────────────────────────────────────────────────────

def _hub_base(n_leaves: int = 6):
    """A "Homelab" hub linked from n leaves, a specific "Zebra server" note."""
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Relation

    write_note("Homelab", title="Zebra homelab",
               content="zebra zebra zebra zebra stripes everywhere in the zebra homelab")
    for i in range(n_leaves):
        write_note(f"Leaf {i}", content=f"leaf {i}", relations=[Relation("part_of", "Homelab")])
    write_note("Zebra server", content="the zebra server", relations=[Relation("part_of", "Homelab")])


def test_hub_ranks_lower_but_is_kept(home, monkeypatch):
    from myclaw.knowledge import quality, read_note

    _hub_base()
    found = [read_note("homelab"), read_note("zebra-server"), read_note("leaf-1")]  # search order
    monkeypatch.setitem(quality._OPTIONS, "hub_degree", 2)
    ranked = [n.permalink for n in quality.rank_with_hub_penalty(found, "default")]
    assert ranked == ["zebra-server", "homelab", "leaf-1"]          # lower, not removed
    pinned = quality.rank_with_hub_penalty(found, "default", pinned={"homelab"})
    assert [n.permalink for n in pinned][0] == "homelab"
    monkeypatch.setitem(quality._OPTIONS, "hub_degree", 25)       # not a hub at 25
    assert [n.permalink for n in quality.rank_with_hub_penalty(found, "default")] == [
        "homelab", "zebra-server", "leaf-1"]
    monkeypatch.setitem(quality._OPTIONS, "hub_degree", 2)
    monkeypatch.setitem(quality._OPTIONS, "hub_penalty", False)
    assert quality.rank_with_hub_penalty(found, "default") == found
    assert quality.hub_factor(10, 25) == 1.0 and quality.hub_factor(100, 25) < 0.5


def test_agent_context_puts_the_specific_note_first(home, monkeypatch):
    from unittest.mock import MagicMock

    from myclaw.agent import Agent
    from myclaw.knowledge import quality

    _hub_base()
    monkeypatch.setitem(quality._OPTIONS, "hub_degree", 2)
    agent = Agent.__new__(Agent)
    agent.config = MagicMock()
    agent.config.knowledge.enabled = True
    ctx = Agent._search_knowledge_context_sync(agent, "zebra stripes server", "default", max_results=2)
    assert ctx.index("**Zebra server**") < ctx.index("**Zebra homelab**")


def test_build_context_does_not_walk_through_a_hub(home, monkeypatch):
    from myclaw.knowledge import quality, write_note
    from myclaw.knowledge.graph import build_context, get_related_entities
    from myclaw.knowledge.parser import Relation

    _hub_base()
    write_note("Rack", content="the rack", relations=[Relation("contains", "Homelab")])
    # depth 2 without penalty: Rack -> Homelab -> (nothing outgoing); use incoming-free graph:
    # Homelab links out to many notes instead
    write_note("Homelab", content="hub", relations=[Relation("has", f"Leaf {i}") for i in range(6)])
    monkeypatch.setitem(quality._OPTIONS, "hub_degree", 3)
    rel = get_related_entities("rack", depth=2)
    assert [(r["permalink"], r.get("hub")) for r in rel] == [("homelab", True)]
    ctx = build_context("rack", depth=2)
    assert "## Related Knowledge" in ctx and "### Depth 1" in ctx
    assert "(homelab) — hub note" in ctx and "leaf-0" not in ctx
    assert len(get_related_entities("rack", depth=1)) == 1          # depth 1 unchanged
    monkeypatch.setitem(quality._OPTIONS, "hub_penalty", False)
    assert {r["permalink"] for r in get_related_entities("rack", depth=2)} >= {"homelab", "leaf-0"}


# ── Step 5: duplicate notes ─────────────────────────────────────────────────

def test_find_duplicates_reasons_and_keep_choice(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.parser import Relation
    from myclaw.knowledge.quality import find_duplicates

    write_note("Home server", content="The box under the desk.", observations=_facts(("spec", "8 cores")))
    write_note("home-server-copy", title="Home Server!", content="Another text about it.")
    write_note("Mindy", content="Runs everything.", aliases=["NAS box"])
    write_note("NAS box", content="Some words here.")
    write_note("Twin A", content="Exactly the same body text in both notes.")
    write_note("Twin B", content="Exactly the same body text in both notes.")
    write_note("Long A", content="Ollama runs on the home server and serves llama3 and qwen models to agents.")
    write_note("Long B", content="Ollama runs on the home server and serves llama3 and qwen models to the agents.")
    write_note("Other", content="Totally unrelated content about gardening.", relations=[Relation("about", "Twin B")])
    pairs = {(d["keep"]["permalink"], d["drop"]["permalink"]): d["reasons"] for d in find_duplicates("default")}
    assert pairs[("home-server", "home-server-copy")] == ["same title"]     # more facts kept
    assert pairs[("mindy", "nas-box")] == ["title is an alias of the other note"]
    assert pairs[("twin-b", "twin-a")] == ["identical text"]                # more links kept
    assert any(r.startswith("very similar text") for r in pairs[("long-a", "long-b")])
    assert not any("other" in p for p in pairs)
    # nothing was changed
    assert _trash(home) == []


def test_merge_notes_preview_then_apply_keeps_links_working(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.db import KnowledgeDB
    from myclaw.knowledge.graph import get_backlinks
    from myclaw.knowledge.parser import Relation
    from myclaw.knowledge.quality import merge_notes

    write_note("Home server", content="Main box.", tags=["homelab"],
               observations=_facts(("spec", "8 cores")), relations=[Relation("runs", "Ollama")])
    write_note("Mindy01", content="Old name of the box. Has a GPU.", tags=["gpu"], aliases=["the PC"],
               observations=_facts(("spec", "8 cores"), ("spec", "RTX 3060")),
               relations=[Relation("part_of", "Homelab"), Relation("same_as", "Home server")])
    write_note("Ollama", content="Models.")
    write_note("Notes A", content="a", relations=[Relation("uses", "Mindy01")])
    write_note("Notes B", content="b", relations=[Relation("uses", "the PC")])
    write_note("Notes C", content="c", relations=[Relation("uses", "mindy01")])
    before = {p.name: p.read_text() for p in _kdir(home).glob("*.md")}

    prev = merge_notes("default", "home-server", "mindy01")
    assert prev["dry_run"] is True
    assert prev["tags"] == ["homelab", "gpu"]
    assert prev["aliases"] == ["Mindy01", "the PC"]                       # title, alias, permalink (once)
    assert prev["facts"] == ["[spec] 8 cores", "[spec] RTX 3060"]
    assert prev["links"] == ["runs [[Ollama]]", "part_of [[Homelab]]"]       # no self-link
    assert "## Merged from Mindy01" in prev["body"] and "Has a GPU." in prev["body"]
    assert {p.name: p.read_text() for p in _kdir(home).glob("*.md")} == before  # preview only

    merge_notes("default", "home-server", "mindy01", dry_run=False)
    assert not (_kdir(home) / "mindy01.md").exists()
    assert any("Old name of the box" in t.read_text() for t in _trash(home))
    n = read_note("home-server")
    assert "Main box." in n.content and "## Merged from Mindy01" in n.content
    assert {"Mindy01", "the PC"} <= set(n.aliases)
    assert [o.content for o in n.observations] == ["8 cores", "RTX 3060"]
    # links to the dropped note (by title, alias, permalink) now reach the kept one
    sources = {b["permalink"] for b in get_backlinks("home-server")}
    assert {"notes-a", "notes-b", "notes-c"} <= sources
    with KnowledgeDB("default") as db:
        e = db.get_entity_by_permalink("notes-a")
        assert db.get_unresolved_relations(e.id) == []
    # a full rebuild from the files gives the same links
    from myclaw.knowledge.sync import sync_knowledge
    sync_knowledge("default", force=True)
    assert {"notes-a", "notes-b", "notes-c"} <= {b["permalink"] for b in get_backlinks("home-server")}


def test_merge_notes_bad_input(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.quality import merge_notes

    write_note("A", content="a")
    with pytest.raises(ValueError):
        merge_notes("default", "a", "a")
    with pytest.raises(LookupError):
        merge_notes("default", "a", "nope")


# ── Step 6: agent tools and API ─────────────────────────────────────────────

def test_tools_return_actionable_text(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.tools import TOOLS
    from myclaw.tools.kb import (find_duplicate_notes, knowledge_categories, knowledge_conflicts,
                                 resolve_knowledge_conflict)

    for name in ("knowledge_conflicts", "resolve_knowledge_conflict", "knowledge_categories",
                 "find_duplicate_notes"):
        assert name in TOOLS
    assert "merge_notes" not in TOOLS and "merge_category" not in TOOLS   # user actions only

    assert "No open conflicts" in knowledge_conflicts()
    assert "No facts yet" in knowledge_categories()
    assert "No duplicate notes" in find_duplicate_notes()
    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("Spec", "64 GB RAM"),
                                                      ("spec", "1 TB"), ("spec", "2 fans")))
    write_note("Box copy", title="box", content="y")
    out = knowledge_conflicts()
    assert "32 GB RAM" in out and "64 GB RAM" in out and "resolve_knowledge_conflict" in out
    cid = int(out.split("#", 1)[1].split()[0])
    assert "keep" in resolve_knowledge_conflict(cid, "maybe")
    assert "knowledge_conflicts()" in resolve_knowledge_conflict(4242, "a")
    assert "Cannot resolve" in resolve_knowledge_conflict("abc", "a")
    done = resolve_knowledge_conflict(cid, "b")
    assert done.startswith("✅") and ".trash" in done
    assert [o.content for o in read_note("box").observations] == ["64 GB RAM", "1 TB", "2 fans"]
    cats = knowledge_categories()
    assert "- spec: 3 (written as: Spec)" in cats and '"Spec" (1) → "spec" (2)' not in cats
    dup = find_duplicate_notes()
    assert "box-copy" in dup and "same title" in dup and "Knowledge page" in dup


@pytest.fixture
def api(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from myclaw.web import knowledge_api

    app = FastAPI()
    app.include_router(knowledge_api.router)
    return TestClient(app)


def test_api_conflicts_routes(api, home):
    from myclaw.knowledge import write_note

    write_note("Box", content="x", observations=_facts(("setting", "web port: 80"), ("setting", "web port: 8080")))
    r = api.get("/api/knowledge/conflicts", params={"permalink": "box"})
    assert r.status_code == 200 and len(r.json()["conflicts"]) == 1
    c = r.json()["conflicts"][0]
    note = api.get("/api/knowledge/note/box").json()
    assert [x["id"] for x in note["conflicts"]] == [c["id"]]
    graph = api.get("/api/knowledge/graph").json()
    assert {n["id"]: n["conflicts"] for n in graph["nodes"]}["box"] == 1
    assert {n["id"]: n["categories"] for n in graph["nodes"]}["box"] == ["setting"]
    assert api.post(f"/api/knowledge/conflicts/{c['id']}", json={"keep": "x"}).status_code == 400
    assert api.post("/api/knowledge/conflicts/999", json={"keep": "a"}).status_code == 404
    assert api.get("/api/knowledge/conflicts", params={"status": "bad"}).status_code == 400
    r = api.post(f"/api/knowledge/conflicts/{c['id']}", json={"keep": "both"})
    assert r.status_code == 200 and r.json()["conflict"]["status"] == "dismissed"
    assert api.get("/api/knowledge/conflicts").json()["conflicts"] == []


def test_api_categories_routes(api, home):
    from myclaw.knowledge import read_note, write_note

    write_note("A", content="a", observations=_facts(("spec", "1"), ("spec", "2"), ("spec", "3"), ("Specs", "4")))
    data = api.get("/api/knowledge/categories").json()
    assert data["categories"][0]["name"] == "spec"
    assert data["suggestions"][0]["from"] == "Specs" and data["suggestions"][0]["to"] == "spec"
    prev = api.post("/api/knowledge/categories/merge", json={"from": "Specs", "to": "spec"}).json()
    assert prev["dry_run"] and prev["facts"] == 1
    assert [o.category for o in read_note("a").observations][-1] == "Specs"
    done = api.post("/api/knowledge/categories/merge", json={"from": "Specs", "to": "spec", "dry_run": False}).json()
    assert not done["dry_run"]
    assert {o.category for o in read_note("a").observations} == {"spec"}
    assert api.post("/api/knowledge/categories/merge", json={"from": "", "to": "x"}).status_code == 400


def test_api_duplicates_and_merge_routes(api, home):
    from myclaw.knowledge import write_note

    write_note("Home server", content="One.")
    write_note("home-server-2", title="Home server", content="Two.")
    pairs = api.get("/api/knowledge/duplicates").json()["pairs"]
    assert len(pairs) == 1
    keep, drop = pairs[0]["keep"]["permalink"], pairs[0]["drop"]["permalink"]
    prev = api.post("/api/knowledge/merge", json={"keep": keep, "drop": drop}).json()
    assert prev["dry_run"] and (_kdir(home) / f"{drop}.md").exists()
    done = api.post("/api/knowledge/merge", json={"keep": keep, "drop": drop, "dry_run": False})
    assert done.status_code == 200 and not (_kdir(home) / f"{drop}.md").exists()
    assert api.post("/api/knowledge/merge", json={"keep": keep, "drop": drop}).status_code == 404
    assert api.post("/api/knowledge/merge", json={"keep": keep, "drop": keep}).status_code == 400


def test_year_like_values_after_a_key_are_values():
    from myclaw.knowledge.quality import conflict_kind

    assert conflict_kind("web port: 1999", "web port: 2000") == "value"
    assert conflict_kind("Released in 2019", "Released in 2021") is None


def test_many_facts_are_checked_quickly():
    import time

    from myclaw.knowledge.quality import find_conflicts

    obs = _facts(*[("spec", f"disk {i} holds {i * 7} GB of data from project {i}") for i in range(150)])
    t = time.time()
    find_conflicts(obs)
    assert time.time() - t < 3


# ── Review fixes (2026-10-24) ───────────────────────────────────────────────

def test_r1_pipes_in_a_fact_stay_text(home):
    from myclaw.knowledge import read_note
    from myclaw.tools.kb import write_to_knowledge

    write_to_knowledge("Shell tips", "Tips.", "linux",
                       "tip | ps aux | grep python\nsetting | ports: 80 | 443\n"
                       "fact | from docs | source: the manual\nurl | docs | https://x.org/a")
    n = read_note("shell-tips")
    assert [(o.content, o.provenance_path) for o in n.observations] == [
        ("ps aux | grep python", None), ("ports: 80 | 443", None),
        ("from docs", "the manual"), ("docs", "https://x.org/a")]


def test_r2_same_fact_from_two_sources_is_kept(home):
    from myclaw.knowledge import read_note, update_note

    f = _kdir(home) / "box.md"
    f.write_text("---\ntitle: Box\n---\n\n# Box\n\n- [fact] A (source: chat 1)\n- [fact] A (source: chat 2)\n")
    from myclaw.knowledge.sync import sync_knowledge
    sync_knowledge("default")
    assert [o.provenance_path for o in read_note("box").observations] == ["chat 1", "chat 2"]
    update_note("box", tags=["new"])
    text = f.read_text()
    assert "(source: chat 1)" in text and "(source: chat 2)" in text
    # dropping one of them is a loss: the old version goes to .trash
    from myclaw.knowledge.parser import Observation
    update_note("box", observations=[Observation("fact", "A", [], provenance_path="chat 1")])
    assert len(_trash(home)) == 1


@pytest.mark.parametrize("line,content,source", [
    ("- [fact] cost (source: invoice) (approx)", "cost (source: invoice) (approx)", None),
    ("- [fact] see the docs (source: x) for details", "see the docs (source: x) for details", None),
    ("- [fact] a (source: b) c (source: d)", "a (source: b) c", "d"),
    ("- [fact] nested (source: a (b (c)))", "nested", "a (b (c))"),
    ("- [fact] note (source:)", "note (source:)", None),
])
def test_r7_last_source_running_to_the_end(line, content, source):
    from myclaw.knowledge.parser import Note, generate_markdown, parse_observations

    o = parse_observations(line)[0]
    assert (o.content, o.provenance_path) == (content, source)
    md = generate_markdown(Note(name="n", permalink="n", title="N", content="", observations=[o]))
    back = parse_observations(md)[0]
    assert (back.content, back.provenance_path) == (content, source)


def test_r7_unbalanced_source_is_written_safely():
    from myclaw.knowledge.parser import Note, generate_markdown, parse_observations
    from myclaw.knowledge.parser import Observation

    o = Observation("fact", "x", [], provenance_path="odd ) source (source: y")
    md = generate_markdown(Note(name="n", permalink="n", title="N", content="", observations=[o]))
    back = parse_observations(md)[0]
    assert back.content == "x" and back.provenance_path and "(source:" not in back.provenance_path


REVIEW_CORPUS = {
"weather": [("weather","Temperature in Bucharest: 12°C"),("weather","Temperature in Bucharest: 18°C"),
            ("weather","Wind: 10 km/h NW"),("weather","Wind: 25 km/h S"),("weather","Humidity 60%"),("weather","Humidity 85%"),
            ("forecast","Monday: sunny, 20°C"),("forecast","Tuesday: rain, 14°C"),("weather","Morning: 8°C"),("weather","Afternoon: 16°C")],
"servers": [("spec","Server mindy01 has 32 GB RAM"),("spec","Server mindy02 has 64 GB RAM"),
            ("spec","CPU: Ryzen 7 5800X"),("spec","GPU: RTX 3060 12 GB"),("spec","GPU: RTX 4090 24 GB"),
            ("spec","Disk /dev/sda: 1 TB"),("spec","Disk /dev/sdb: 4 TB"),("spec","RAM slot A1: 16 GB"),("spec","RAM slot A2: 16 GB"),
            ("spec","nvme0n1 512 GB"),("spec","nvme1n1 2 TB"),("spec","eth0 1 Gbps"),("spec","eth1 10 Gbps")],
"ports": [("setting","Ollama listens on port 11434"),("setting","Open WebUI listens on port 8080"),
          ("setting","ZenSynora web UI port: 8000"),("setting","Grafana port: 3000"),
          ("port","ssh: 22"),("port","http: 80"),("port","https: 443"),
          ("setting","port: 8080"),("setting","port: 9090")],
"llm providers": [("provider","OpenAI: gpt-4o"),("provider","Anthropic: claude-3.5-sonnet"),
                  ("model","default model is llama3"),("model","fallback model is qwen2.5"),
                  ("provider","Groq rate limit: 30 requests/min"),("provider","Cerebras rate limit: 60 requests/min"),
                  ("model","Context window: 8192 tokens"),("model","Context window: 128000 tokens"),
                  ("model","llama3 is 8B"),("model","qwen2.5 is 14B"),("model","Model: llama3"),("model","Model: qwen2.5")],
"versions": [("version","Python 3.11 on server"),("version","Python 3.13 on laptop"),("version","Node.js 20.19"),("version","Node.js 22.1"),
             ("version","ZenSynora version: 0.7.5"),("version","ZenSynora version: 0.7.6"),
             ("version","Docker 24.0.7"),("version","Docker Compose 2.23")],
"errors": [("error","Error 500 on /api/chat"),("error","Error 502 on /api/chat"),("error","Timeout after 30 s"),("error","Timeout after 120 s"),
           ("error","Exit code: 1"),("error","Exit code: 137"),("error","Retry 3 failed"),("error","Retry 5 failed"),
           ("error","HTTP 429: Too Many Requests"),("error","HTTP 401: Unauthorized")],
"session": [("summary","Session 2026-10-01: fixed sync"),("summary","Session 2026-10-02: added tests"),
            ("decision","Use SQLite for the index"),("decision","Use Postgres for metrics"),
            ("fact","Tests: 844 passing"),("fact","Tests: 921 passing"),("fact","Commit count 120"),("fact","Commit count 135"),
            ("fact","Status: done"),("fact","Status: in progress"),("todo","Owner: Adrian"),("todo","Owner: Claude")],
"dates": [("fact","Backup ran at 03:00"),("fact","Backup ran at 04:00"),("fact","Last backup: 2026-10-01"),("fact","Last backup: 2026-10-02"),
          ("fact","Uptime 12 days"),("fact","Uptime 30 days"),("fact","Disk usage 45%"),("fact","Disk usage 80%")],
"family": [("person","Brother: Mihai"),("person","Sister: Ana"),("person","Sister: Maria"),("person","Kid age 5"),("person","Kid age 8")],
"recipes": [("ingredient","Flour: 500 g"),("ingredient","Sugar: 200 g"),("step","Bake 20 min at 180°C"),("step","Bake 10 min at 200°C"),
            ("ingredient","Eggs: 2"),("ingredient","Milk: 250 ml")],
}


def test_r3_review_corpus_has_no_false_conflicts():
    from myclaw.knowledge.parser import Observation
    from myclaw.knowledge.quality import find_conflicts

    for name, facts in REVIEW_CORPUS.items():
        obs = [Observation(c, t, []) for c, t in facts]
        assert find_conflicts(obs) == [], name


def test_r3_real_conflicts_still_flagged_and_only_in_value_categories():
    from myclaw.knowledge.quality import find_conflicts

    obs = _facts(("setting", "Ollama port: 11434"), ("setting", "Ollama port: 8080"),
                 ("spec", "mindy01 has 32 GB RAM"), ("spec", "mindy01 has 64 GB RAM"),
                 ("fact", "mindy01 has 16 GB RAM"), ("fact", "mindy01 has 8 GB RAM"),
                 ("config", "Grafana port = 3000"), ("Settings", "Grafana port = 3001"))
    assert sorted((i, j) for i, j, _ in find_conflicts(obs)) == [(0, 1), (2, 3), (6, 7)]


def test_r4_templated_notes_are_not_duplicates(home):
    from myclaw.knowledge import write_note
    from myclaw.knowledge.quality import find_duplicates

    for i in range(30):
        write_note(f"Weather Bucharest 2026-10-{i + 1:02d}",
                   content=f"Weather report for Bucharest on 2026-10-{i + 1:02d}. Temperature {i % 20}°C, "
                           f"wind {i % 7 + 3} km/h, humidity {50 + i}%. Sky: cloudy.")
    write_note("Router setup", content="Configure the router: open the admin page, set the WiFi name and the password, then reboot it.")
    write_note("Router setup notes", content="Configure the router: open the admin page, set the WiFi name and the password, and then reboot it.")
    r = find_duplicates("default", report=True)
    assert [(p["keep"]["permalink"], p["drop"]["permalink"]) for p in r["pairs"]] in (
        [("router-setup", "router-setup-notes")], [("router-setup-notes", "router-setup")])
    assert r["partial"] is False


def test_r5_duplicates_report_partial_when_capped(home, monkeypatch):
    from myclaw.knowledge import quality, write_note

    text = "Configure the router: open the admin page, set the WiFi name and the password, then reboot it."
    for i in range(4):
        write_note(f"Router setup {'abcd'[i]}", content=text + " " + "xyzw"[i])
    monkeypatch.setattr(quality, "_MAX_CANDIDATES", 1)
    r = quality.find_duplicates("default", report=True)
    assert r["partial"] is True and "not complete" in r["message"]
    assert isinstance(quality.find_duplicates("default"), list)  # default shape unchanged


def test_r5_duplicates_are_fast_on_a_big_base(home):
    import time

    from myclaw.knowledge.quality import find_duplicates

    kd = _kdir(home)
    for i in range(1500):
        kd.joinpath(f"n{i}.md").write_text(
            f"---\ntitle: Session summary {i}\n---\n\n# Session summary {i}\n\nWorked on the knowledge base "
            f"and fixed sync number {i}. Added tests. Reviewed merge.\n")
    t = time.time()
    r = find_duplicates("default", report=True)
    assert time.time() - t < 5 and r["pairs"] == []


def test_r6_concurrent_resolve_removes_only_one_fact(home):
    import threading

    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.quality import list_conflicts, resolve_conflict

    write_note("Box", content="x", observations=_facts(("setting", "web port: 80"), ("setting", "web port: 8080")))
    cid = list_conflicts("default")[0]["id"]
    out = []
    ts = [threading.Thread(target=lambda k=k: out.append(resolve_conflict("default", cid, k))) for k in "ab"]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in out if r["changed"]) == 1
    assert len(read_note("box").observations) == 1


def test_r6_failed_resolve_gives_the_conflict_back(home, monkeypatch):
    from myclaw.knowledge import storage, write_note
    from myclaw.knowledge.quality import list_conflicts, resolve_conflict

    write_note("Box", content="x", observations=_facts(("setting", "web port: 80"), ("setting", "web port: 8080")))
    cid = list_conflicts("default")[0]["id"]

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "update_note", boom)
    with pytest.raises(OSError):
        resolve_conflict("default", cid, "a")
    assert [c["id"] for c in list_conflicts("default")] == [cid]


def test_r8_old_index_is_reread_once_by_incremental_sync(home):
    import sqlite3

    from myclaw.knowledge.quality import list_conflicts
    from myclaw.knowledge.sync import sync_knowledge

    f = _kdir(home) / "box.md"
    f.write_text("# Box\n\n- [spec] mindy01 has 32 GB RAM\n- [spec] mindy01 has 64 GB RAM (source: chat 1)\n")
    sync_knowledge("default")
    db = home / ".myclaw" / "knowledge_default.db"
    conn = sqlite3.connect(db)  # make it look like an index from before phase 2
    conn.execute("DROP INDEX idx_observations_category_norm")
    conn.execute("ALTER TABLE observations DROP COLUMN category_norm")
    conn.execute("UPDATE observations SET provenance_path = NULL")
    conn.execute("DELETE FROM conflicts")
    conn.commit()
    conn.close()
    stats = sync_knowledge("default")             # incremental, file unchanged
    assert stats["updated"] == 1
    assert len(list_conflicts("default")) == 1
    from myclaw.knowledge.db import KnowledgeDB
    with KnowledgeDB("default") as kdb:
        assert kdb.get_sync_meta("reparse_all_notes") == "0"
        assert kdb._get_connection().execute(
            "SELECT provenance_path FROM observations WHERE provenance_path IS NOT NULL").fetchone()[0] == "chat 1"
    assert sync_knowledge("default")["updated"] == 0  # only once


def test_r9_category_merge_matches_unicode_and_spaces(home):
    from myclaw.knowledge.quality import merge_category
    from myclaw.knowledge.sync import sync_knowledge

    kd = _kdir(home)
    (kd / "a.md").write_text("# A\n\n- [Настройки] x 1\n")
    (kd / "b.md").write_text("# B\n\n- [настройки] y 2\n")
    (kd / "c.md").write_text("# C\n\n- [my  cat] z\n")
    sync_knowledge("default")
    assert [n["permalink"] for n in merge_category("default", "настройки", "setting")["notes"]] == ["a", "b"]
    assert [n["permalink"] for n in merge_category("default", "my cat", "cat")["notes"]] == ["c"]


def test_r10_huge_ids_are_404_not_500(api):
    for cid in ("99999999999999999999", "0", "-1"):
        r = api.post(f"/api/knowledge/conflicts/{cid}", json={"keep": "a"})
        assert r.status_code == 422, (cid, r.status_code)
    assert api.get("/api/knowledge/duplicates", params={"limit": -5}).status_code == 422
    from myclaw.tools.kb import resolve_knowledge_conflict
    assert "knowledge_conflicts()" in resolve_knowledge_conflict(10 ** 20, "a")


def test_r11_sources_hold_no_secrets():
    from myclaw.knowledge.quality import tool_source

    assert tool_source("shell", {"cmd": "mysql -p hunter2"}) == "tool shell"
    assert tool_source("ssh_command", {"host": "h", "cmd": "x"}) == "tool ssh_command"
    s = tool_source("browse", {"url": "https://user:pw@api.x.org/v1/a?token=abc123#frag"})
    assert s == "tool browse https://api.x.org/v1/a"
    s = tool_source("read_file", {"path": "/srv/keys/sk-abcdefghijklmnopqrstuvwxyz0123"})
    assert "abcdefghijklmnop" not in s and "redacted" in s


def test_r12_conflicts_hidden_while_detection_is_off(api, home, monkeypatch):
    from myclaw.knowledge import quality, write_note

    write_note("Box", content="x", observations=_facts(("spec", "32 GB RAM"), ("spec", "64 GB RAM")))
    assert len(api.get("/api/knowledge/conflicts").json()["conflicts"]) == 1
    monkeypatch.setitem(quality._OPTIONS, "detect_conflicts", False)
    assert api.get("/api/knowledge/conflicts").json()["conflicts"] == []
    assert api.get("/api/knowledge/note/box").json()["conflicts"] == []
    assert {n["id"]: n["conflicts"] for n in api.get("/api/knowledge/graph").json()["nodes"]}["box"] == 0
    monkeypatch.setitem(quality._OPTIONS, "detect_conflicts", True)
    assert len(api.get("/api/knowledge/conflicts").json()["conflicts"]) == 1  # kept, not deleted
