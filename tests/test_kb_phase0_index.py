"""KB phase 0 (2026-10-23): the SQLite index of the knowledge base.

Covers myclaw/knowledge/db.py and myclaw/knowledge/sync.py: backlinks kept on
re-index, links by title/slug/alias, forward links, aliases + metadata,
updates of existing entries, atomic re-index, hidden folders / .bmignore,
incremental sync, broken files, the circuit breaker across calls, moves,
boolean search, tag search and metadata-key validation.
"""

from __future__ import annotations

import os
import random
import sqlite3
import time
from pathlib import Path

import pytest

import myclaw.knowledge.storage as storage
from myclaw.knowledge import sync as sync_mod
from myclaw.knowledge.db import KnowledgeDB, sanitize_fts_query
from myclaw.knowledge.parser import Note, Observation, Relation, parse_note
from myclaw.knowledge.sync import (
    SyncEngine, detect_moves, reset_sync_failures, resolve_forward_references,
    scan_markdown_files, sync_knowledge,
)

USER = "test"


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """A knowledge folder + DB in tmp_path (storage.get_knowledge_dir patched)."""
    root = tmp_path / "knowledge"

    def fake_dir(user_id="default"):
        d = root / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(storage, "get_knowledge_dir", fake_dir)
    reset_sync_failures()
    sync_mod.clear_note_cache()

    class KB:
        dir = fake_dir(USER)
        db_path = tmp_path / "knowledge_test.db"

        def write(self, rel: str, text: str) -> Path:
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return p

        def db(self) -> KnowledgeDB:
            return KnowledgeDB(USER, db_path=self.db_path)

        def sync(self, **kw):
            return sync_knowledge(USER, db_path=self.db_path, **kw)

        def links(self):
            """{(source, relation, target)} of resolved links (by permalink)."""
            with self.db() as db:
                rows = db._get_connection().execute(
                    "SELECT a.permalink AS s, r.relation_type AS t, b.permalink AS d "
                    "FROM relations r JOIN entities a ON a.id = r.from_entity_id "
                    "JOIN entities b ON b.id = r.to_entity_id").fetchall()
            return {(r["s"], r["t"], r["d"]) for r in rows}

        def pending(self, permalink: str):
            with self.db() as db:
                e = db.get_entity_by_permalink(permalink)
                return db.get_unresolved_relations(e.id) if e else None

    yield KB()
    reset_sync_failures()


def note_md(title, links=(), tags=(), permalink=None, extra="", body="Some text."):
    fm = [f'title: "{title}"']
    if permalink:
        fm.append(f"permalink: {permalink}")
    if tags:
        fm.append("tags: [" + ", ".join(tags) + "]")
    if extra:
        fm.append(extra)
    rel = "".join(f"- {t} [[{x}]]\n" for t, x in links)
    return "---\n" + "\n".join(fm) + f"\n---\n\n# {title}\n\n{body}\n\n## Relations\n\n{rel}"


def bump(path: Path, text: str) -> None:
    """Rewrite a file and make sure its mtime moves forward."""
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))


# ── 1 (B1) re-indexing a note keeps the links of other notes to it ──────────

def test_reindex_keeps_backlinks(kb):
    kb.write("a.md", note_md("A", [("uses", "b")], permalink="a"))
    b = kb.write("b.md", note_md("B", permalink="b"))
    kb.sync(force=True)
    assert ("a", "uses", "b") in kb.links()

    bump(b, note_md("B", permalink="b", body="changed text"))
    stats = kb.sync()
    assert stats["updated"] == 1
    assert ("a", "uses", "b") in kb.links()

    with kb.db() as db:  # directly, too
        db.sync_entity_from_note(parse_note(b))
        assert [x.permalink for x in db.get_backlinks(db.get_entity_by_permalink("b").id)] == ["a"]
        # clear_relations keeps its old meaning (both directions)
        db.clear_outgoing_relations(db.get_entity_by_permalink("b").id)
    assert ("a", "uses", "b") in kb.links()


# ── 2 (B2) link targets by permalink, slug, name, title, alias ──────────────

def test_resolve_target_rules(kb):
    with kb.db() as db:
        db.sync_entity_from_note(Note(name="home-server", permalink="home-server",
                                      title="Home server", content="", file_path=None))
        db.sync_entity_from_note(Note(name="Ziggy", permalink="z-1", title="Zeta Note",
                                      content="", aliases=["Zed", "Über Ding"]))
        hs = db.get_entity_by_permalink("home-server").id
        z = db.get_entity_by_permalink("z-1").id
        for text in ["home-server", "Home-Server", "Home server", "home SERVER",
                     "[[Home server]]", "Home server|the box", "home-server.md",
                     "Home server#Disks"]:
            assert db.resolve_target(text).id == hs, text
        for text in ["z-1", "ziggy", "ZETA NOTE", "zed", "über ding", "ÜBER DING"]:
            assert db.resolve_target(text).id == z, text
        for text in ["", "   ", "home", "server", "Zeta", "nothing here"]:
            assert db.resolve_target(text) is None, text  # no fuzzy matching
        assert db.get_entity_by_permalink("home-server").title == "Home server"


def test_links_by_title_are_stored(kb):
    kb.write("hs.md", note_md("Home server", permalink="home-server-box"))
    kb.write("n.md", note_md("Notes", [("about", "Home server"), ("see", "home server box")],
                             permalink="n"))
    kb.sync(force=True)
    assert ("n", "about", "home-server-box") in kb.links()
    assert ("n", "see", "home-server-box") in kb.links()  # slug of the target
    assert kb.pending("n") == []


# ── 3 forward links (key end-to-end scenario) ───────────────────────────────

def test_forward_links_end_to_end(kb):
    a = kb.write("a.md", note_md("Note A", [("runs_on", "Home server")], permalink="a"))
    kb.sync()
    assert kb.pending("a") == [("runs_on", "Home server")]
    with kb.db() as db:
        assert db.get_stats()["unresolved_relations"] == 1

    # the target is written later → resolved without touching A
    b = kb.write("home-server.md", note_md("Home server", [("hosts", "Note A")]))
    kb.sync()
    assert kb.pending("a") == []
    assert ("a", "runs_on", "home-server") in kb.links()
    assert ("home-server", "hosts", "a") in kb.links()
    with kb.db() as db:
        assert db.get_stats()["unresolved_relations"] == 0
    expected = kb.links()

    # re-sync both, incremental and forced: backlinks stay
    bump(a, note_md("Note A", [("runs_on", "Home server")], permalink="a", body="v2"))
    bump(b, note_md("Home server", [("hosts", "Note A")], body="v2"))
    assert kb.sync()["updated"] == 2
    assert kb.links() == expected
    since = time.time() + 1
    bump(b, note_md("Home server", [("hosts", "Note A")], body="v3"))
    os.utime(b, (since + 10, since + 10))
    kb.sync(since=since)
    assert kb.links() == expected
    for _ in range(2):
        kb.sync(force=True)  # rebuilt from the files: same relations
        assert kb.links() == expected

    # the target is deleted → A's link becomes unresolved again, and comes back
    b.unlink()
    assert kb.sync()["deleted"] == 1
    assert kb.pending("a") == [("runs_on", "Home server")]
    kb.write("home-server.md", note_md("Home server", [("hosts", "Note A")]))
    kb.sync()
    assert ("a", "runs_on", "home-server") in kb.links()


def test_resolve_forward_references_creates_no_placeholders(kb):
    with kb.db() as db:
        db.sync_entity_from_note(Note(name="A", permalink="a", title="A", content="",
                                      relations=[Relation("uses", "Missing Thing")]))
        # simulate a target that appears without going through sync_entity_from_note
        db.create_entity("Missing Thing", "missing-thing", "/x/missing-thing.md")
    stats = resolve_forward_references(USER, db_path=kb.db_path)
    assert stats == {"resolved": 1, "created": 0, "errors": 0}
    assert ("a", "uses", "missing-thing") in kb.links()
    stats = resolve_forward_references(USER, db_path=kb.db_path)
    assert stats["resolved"] == 0 and stats["created"] == 0
    with kb.db() as db:
        db.sync_entity_from_note(Note(name="B", permalink="b", title="B", content="",
                                      relations=[Relation("uses", "Nowhere")]))
    assert resolve_forward_references(USER, db_path=kb.db_path)["created"] == 0
    with kb.db() as db:
        assert db.get_entity_by_permalink("nowhere") is None


# ── 4 aliases + metadata ────────────────────────────────────────────────────

def test_aliases_and_metadata_stored_and_replaced(kb):
    p = kb.write("isaac.md", note_md("Isaac Newton", tags=["physics", "People"],
                                     permalink="isaac-newton",
                                     extra="aliases: [Newton, Sir Isaac]\ntype: person"))
    kb.write("apple.md", note_md("Apple", [("about", "newton")], permalink="apple"))
    kb.sync(force=True)
    assert ("apple", "about", "isaac-newton") in kb.links()
    with kb.db() as db:
        e = db.get_entity_by_alias("SIR ISAAC")
        assert e is not None and e.permalink == "isaac-newton"
        assert sorted(db.get_aliases_for_entity(e.id)) == ["Newton", "Sir Isaac"]
        assert e.entity_metadata["type"] == "person"
        assert e.entity_metadata["tags"] == ["physics", "People"]
        assert [x.permalink for x in db.search_by_metadata({"type": "person"})] == ["isaac-newton"]

    bump(p, note_md("Isaac Newton", permalink="isaac-newton", extra="aliases: [Ike]"))
    kb.sync()
    with kb.db() as db:
        e = db.get_entity_by_permalink("isaac-newton")
        assert db.get_aliases_for_entity(e.id) == ["Ike"]
        assert "type" not in e.entity_metadata and e.entity_metadata["tags"] == []


# ── 5 (B6) an existing entry gets every field updated ───────────────────────

def test_existing_entity_fields_updated(kb):
    with kb.db() as db:
        eid = db.sync_entity_from_note(
            Note(name="Old", permalink="p", title="Old", content="", file_path=Path("/x/old.md")),
            checksum="c1", mtime=1.0, size=10)
        db.sync_entity_from_note(Note(name="Taken", permalink="other", title="T", content=""))
        again = db.sync_entity_from_note(
            Note(name="New", permalink="p", title="New title", content="",
                 file_path=Path("/x/new.md")), checksum="c2", mtime=2.0, size=20)
        assert again == eid
        e = db.get_entity_by_id(eid)
        assert (e.name, e.title, e.file_path, e.checksum, e.mtime, e.size) == \
            ("New", "New title", "/x/new.md", "c2", 2.0, 20)
        # name is UNIQUE: a name used by another note keeps the old one, no crash
        db.sync_entity_from_note(Note(name="Taken", permalink="p", title="X", content=""))
        e = db.get_entity_by_id(eid)
        assert e.name == "New" and e.title == "X" and e.checksum == "c2"
        # a NEW note whose name is taken is still indexed
        nid = db.sync_entity_from_note(Note(name="Taken", permalink="third", title="3",
                                            content=""))
        assert db.get_entity_by_id(nid).permalink == "third"


# ── 6 the whole re-index is one transaction ─────────────────────────────────

def test_sync_entity_is_atomic(kb, monkeypatch):
    with kb.db() as db:
        db.sync_entity_from_note(Note(name="T", permalink="t", title="T", content="",
                                      observations=[Observation("fact", "one")]))
        db.sync_entity_from_note(Note(name="U", permalink="u", title="U", content="",
                                      relations=[Relation("uses", "t")]))
        tid = db.get_entity_by_permalink("t").id

        def boom(*a, **k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(KnowledgeDB, "_index_note_text_conn", boom)
        with pytest.raises(RuntimeError):
            db.sync_entity_from_note(Note(name="T2", permalink="t", title="T2", content="",
                                          observations=[Observation("fact", "two")],
                                          aliases=["tee"]))
        assert [o.content for o in db.get_observations(tid)] == ["one"]
        assert db.get_entity_by_id(tid).name == "T"
        assert db.get_aliases_for_entity(tid) == []
    assert ("u", "uses", "t") in kb.links()


# ── 7 (B3) hidden folders and .bmignore globs ───────────────────────────────

def test_hidden_folders_and_bmignore(kb):
    kb.write("keep.md", note_md("Keep"))
    kb.write("sub/deep/keep2.md", note_md("Keep2"))
    kb.write(".trash/deleted.md", note_md("Deleted"))
    kb.write(".git/x.md", note_md("Git"))
    kb.write(".obsidian/y.md", note_md("Obs"))
    kb.write("sub/.hidden/z.md", note_md("Hidden"))
    kb.write(".dotfile.md", note_md("Dot"))
    kb.write("a.tmp.md", note_md("tmp"))
    kb.write("drafts/d.md", note_md("Draft"))
    kb.write("archive/2020/old.md", note_md("Old"))
    kb.write("notes/scratch-1/s.md", note_md("Scratch"))
    kb.write("plain/prefix.md", note_md("Prefix"))
    (kb.dir / ".bmignore").write_text(
        "# comment\n*.tmp.md\ndrafts/\narchive/**\nscratch-*/\nplain/prefix.md\n",
        encoding="utf-8")
    names = {p.relative_to(kb.dir).as_posix() for p in scan_markdown_files(USER)}
    assert names == {"keep.md", "sub/deep/keep2.md"}
    engine = SyncEngine(USER, db_path=kb.db_path)
    assert {p.name for p in engine._scan_markdown_files()} == {"keep.md", "keep2.md"}
    kb.sync(force=True)
    with kb.db() as db:
        assert sorted(e.name for e in db.list_all_entities()) == ["keep", "keep2"]


def test_deleted_note_in_trash_is_dropped_from_index(kb):
    p = kb.write("gone.md", note_md("Gone", permalink="gone"))
    kb.sync()
    (kb.dir / ".trash").mkdir()
    p.rename(kb.dir / ".trash" / "gone.md")
    assert kb.sync()["deleted"] == 1
    with kb.db() as db:
        assert db.get_entity_by_permalink("gone") is None


# ── 8 (B7) incremental sync deletes only notes whose file is gone ───────────

def test_incremental_sync_does_not_delete_unchanged_notes(kb):
    for i in range(5):
        kb.write(f"n{i}.md", note_md(f"N{i}", permalink=f"n{i}"))
    kb.sync(force=True)
    since = time.time() + 100
    p = kb.write("fresh.md", note_md("Fresh", permalink="fresh"))
    os.utime(p, (since + 10, since + 10))
    (kb.dir / "n4.md").unlink()
    stats = kb.sync(since=since)
    assert stats["added"] == 1 and stats["deleted"] == 1
    with kb.db() as db:
        assert sorted(e.permalink for e in db.list_all_entities()) == \
            ["fresh", "n0", "n1", "n2", "n3"]
    # the old internals agree
    with kb.db() as db:
        changes = SyncEngine(USER, db_path=kb.db_path)._detect_changes(db, since=since + 50)
    assert changes.to_delete == [] and changes.to_add == []


def test_incremental_sync_sees_moved_file_with_old_mtime(kb):
    p = kb.write("x.md", note_md("X", permalink="x"))
    kb.sync()
    old = p.stat().st_mtime
    new = kb.dir / "sub" / "x.md"
    new.parent.mkdir()
    p.rename(new)
    os.utime(new, (old, old))  # mv keeps the mtime
    stats = kb.sync(since=old + 1)
    assert stats["deleted"] == 0
    with kb.db() as db:
        assert db.get_entity_by_permalink("x").file_path == str(new)


# ── 9 broken files are skipped and keep their entry ─────────────────────────

def test_broken_file_is_skipped_and_keeps_entry(kb):
    good = kb.write("good.md", note_md("Good", permalink="good"))
    bad = kb.write("bad.md", note_md("Bad", permalink="bad"))
    kb.sync()
    bad.write_bytes(b"---\ntitle: \xff\xfe broken\n---\n")  # not UTF-8
    bump(good, note_md("Good", permalink="good", body="v2"))
    stats = kb.sync()
    assert stats["errors"] == 1 and stats["updated"] == 1 and stats["deleted"] == 0
    with kb.db() as db:
        assert db.get_entity_by_permalink("bad") is not None
        changes = SyncEngine(USER, db_path=kb.db_path)._detect_changes(db)
    assert "bad" not in changes.to_delete  # no exception either
    # a new broken file does not stop the others being indexed
    kb.write("bad2.md", "").write_bytes(b"\xff\xff")
    kb.write("new.md", note_md("New", permalink="new"))
    stats = kb.sync()
    assert stats["added"] == 1 and stats["errors"] >= 1


# ── 10 (B8) circuit breaker across sync_knowledge() calls ───────────────────

def test_circuit_breaker_survives_new_engines(kb):
    bad = kb.dir / "bad.md"
    bad.write_bytes(b"\xff\xfe")
    errors = [kb.sync()["errors"] for _ in range(sync_mod.MAX_CONSECUTIVE_FAILURES + 2)]
    assert errors == [1] * sync_mod.MAX_CONSECUTIVE_FAILURES + [0, 0]
    bad.write_bytes(b"\xff\xfe\xfd")  # changed → tried again
    assert kb.sync()["errors"] == 1
    bad.write_text(note_md("Fixed", permalink="fixed"), encoding="utf-8")
    assert kb.sync()["added"] == 1
    for i in range(sync_mod._MAX_TRACKED_FAILURES + 20):  # bounded
        SyncEngine(USER)._record_failure(f"/x/{i}.md", "e")
    assert len(sync_mod._engine_failures) <= sync_mod._MAX_TRACKED_FAILURES


# ── 11 (B9) moved / renamed files keep their entry and backlinks ────────────

def test_rename_without_permalink_keeps_entity_and_backlinks(kb):
    body = "---\ntitle: \"Server\"\n---\n\n# Server\n\nThe box.\n"
    old = kb.write("old-name.md", body)
    kb.write("a.md", note_md("A", [("uses", "Server")], permalink="a"))
    kb.sync()
    with kb.db() as db:
        eid = db.get_entity_by_permalink("old-name").id
    assert ("a", "uses", "old-name") in kb.links()

    new = kb.dir / "folder" / "new-name.md"
    new.parent.mkdir()
    old.rename(new)
    stats = kb.sync()
    assert stats["deleted"] == 0 and stats["updated"] == 1 and stats["added"] == 0
    with kb.db() as db:
        assert db.get_entity_by_permalink("old-name") is None
        e = db.get_entity_by_permalink("new-name")
        assert e is not None and e.id == eid and e.file_path == str(new)
    assert ("a", "uses", "new-name") in kb.links()
    assert kb.sync() == {"added": 0, "updated": 0, "deleted": 0, "errors": 0}


def test_permalink_changed_inside_file_keeps_entity(kb):
    p = kb.write("p.md", note_md("P", permalink="p-old"))
    kb.write("a.md", note_md("A", [("uses", "P")], permalink="a"))
    kb.sync()
    with kb.db() as db:
        eid = db.get_entity_by_permalink("p-old").id
    bump(p, note_md("P", permalink="p-new"))
    stats = kb.sync()
    assert stats["deleted"] == 0
    with kb.db() as db:
        assert db.get_entity_by_permalink("p-new").id == eid
        assert len(db.list_all_entities()) == 2
    assert ("a", "uses", "p-new") in kb.links()


def test_duplicate_permalink_is_an_error_not_a_flip_flop(kb):
    kb.write("one.md", note_md("One", permalink="same"))
    kb.sync()
    kb.write("two.md", note_md("Two", permalink="same"))
    for _ in range(2):
        stats = kb.sync()
        assert stats["updated"] == 0 and stats["errors"] == 1
    with kb.db() as db:
        assert db.get_entity_by_permalink("same").file_path.endswith("one.md")


def test_detect_moves_module_function(kb):
    """The old test_detect_moves accepted any result; this one checks it."""
    old = kb.write("original.md", note_md("Original", permalink="original"))
    kb.sync()
    new = kb.dir / "renamed.md"
    old.rename(new)
    checksums = {str(new): storage.get_file_metadata(new)["checksum"]}
    moves = detect_moves(USER, [new], checksums, db_path=kb.db_path)
    assert moves == {str(old): str(new)}
    kb.sync()
    with kb.db() as db:
        assert db.get_entity_by_permalink("original").file_path == str(new)


# ── 12 boolean full-text search ─────────────────────────────────────────────

def test_sanitize_boolean_rules():
    q = sanitize_fts_query
    assert q("docker AND NOT kubernetes", boolean=True) == '"docker" NOT "kubernetes"'
    assert q('"home server" OR nas*', boolean=True) == '"home server" OR "nas"*'
    assert q("a and b", boolean=True) == '"a" "b"'  # lowercase: plain text
    assert q("AND a OR", boolean=True) == '"a"'
    assert q("a AND OR b", boolean=True) == '"a" AND "b"'
    assert q("NOT a b", boolean=True) == '"b"'  # no unary NOT in FTS5
    assert q('"unbalanced phrase', boolean=True) == '"unbalanced phrase"'
    assert q('""  AND', boolean=True) == "*"
    assert q('"pre fix"*', boolean=True) == '"pre fix"*'
    # free text unchanged
    assert q("docker AND NOT kubernetes") == '"docker" "kubernetes"'
    assert q('"home server" OR nas*') == '"home" "server" "nas"*'


def test_sanitize_boolean_never_a_syntax_error():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
    conn.execute("INSERT INTO t VALUES ('docker home server nas')")
    odd = ['"', '""', '"""', "AND", "OR OR", "NOT NOT x", "a NOT", "(a OR b)", "a:b",
           "NEAR(a b)", "-a +b", "^a", "a*b*", "*", "**", '"a" "b', "a OR NOT b",
           "日本語 AND テスト", "c++ OR c#", "a\x00b", "{a} [b]", "col: x", "a AND",
           "x" * 5000, " ".join(["w"] * 100), "AND " * 50, '"' * 33, "a'b", "a;--"]
    rnd = random.Random(1234)
    alphabet = ['"', "*", " ", "AND", "OR", "NOT", "NEAR", "a", "b", "(", ")", ":", "-",
                "^", "+", "é", "#", "'", "\\"]
    for _ in range(500):
        odd.append(" ".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 12))))
        odd.append("".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 12))))
    for text in odd:
        for boolean in (True, False):
            for any_term in (True, False):
                expr = sanitize_fts_query(text, any_term=any_term, boolean=boolean)
                if expr != "*":
                    conn.execute("SELECT * FROM t WHERE t MATCH ?", (expr,)).fetchall()


def test_search_fts_boolean(kb):
    kb.write("d.md", note_md("Docker", body="docker on the home server"))
    kb.write("k.md", note_md("Kube", body="docker and kubernetes cluster"))
    kb.write("n.md", note_md("Nas", body="network storage"))
    kb.sync(force=True)
    with kb.db() as db:
        def hits(text, **kw):
            return sorted(e.name for e in db.search_fts(text, boolean=True, **kw))
        assert hits("docker AND NOT kubernetes") == ["d"]
        assert hits("docker NOT kubernetes") == ["d"]
        assert hits("docker OR storage") == ["d", "k", "n"]
        assert hits('"home server"') == ["d"]
        assert hits('"server home"') == []
        assert hits("kube*") == ["k"]
        assert hits("AND OR NOT") == []
        # free text: the words AND/NOT are dropped, all words must match
        assert sorted(e.name for e in db.search_fts("docker NOT kubernetes")) == ["k"]
        # no any-word fallback for boolean queries
        assert hits("docker AND cluster AND storage AND network") == []
        assert db.search_fts("docker cluster storage network") != []


# ── 13 (B13) tag search: frontmatter + observation tags, whole tag ──────────

def test_search_by_tag(kb):
    kb.write("a.md", note_md("A", tags=["Python", "web"], permalink="a"))
    kb.write("b.md", note_md("B", permalink="b") + "\n## Observations\n- [fact] uses py #py\n")
    kb.write("c.md", note_md("C", tags=["pythonic"], permalink="c"))
    kb.sync(force=True)
    with kb.db() as db:
        assert [e.permalink for e in db.search_by_tag("python")] == ["a"]
        assert [e.permalink for e in db.search_by_tag("#PYTHON")] == ["a"]
        assert [e.permalink for e in db.search_by_tag("py")] == ["b"]
        assert db.search_by_tag("pyth") == [] and db.search_by_tag("") == []
        # notes indexed before tags were stored in metadata: text-index tags
        conn = db._get_connection()
        conn.execute("UPDATE entities SET entity_metadata = NULL WHERE permalink = 'a'")
        conn.commit()
        assert [e.permalink for e in db.search_by_tag("web")] == ["a"]
    assert [n.permalink for n in storage.get_note_by_tag("web", USER, db_path=kb.db_path)] == ["a"]


# ── 14 search_by_metadata keys are validated ────────────────────────────────

def test_search_by_metadata_keys(kb):
    with kb.db() as db:
        db.sync_entity_from_note(Note(name="P", permalink="p", title="P", content="",
                                      metadata={"status": "active", "my-key": "v",
                                                "project": {"owner": "ann"}}))
        assert [e.permalink for e in db.search_by_metadata({"status": "active"})] == ["p"]
        assert [e.permalink for e in db.search_by_metadata({"my-key": "v"})] == ["p"]
        assert [e.permalink for e in db.search_by_metadata({"project.owner": "ann"})] == ["p"]
        assert db.search_by_metadata({"status": "done"}) == []
        for bad in ["a') OR 1=1 --", "a b", "", "$.x", "a..b", ".a", "a[0]", "x'"]:
            with pytest.raises(ValueError):
                db.search_by_metadata({bad: "x"})
        # broken JSON in one row does not break the search
        conn = db._get_connection()
        conn.execute("UPDATE entities SET entity_metadata = '{oops' WHERE permalink = 'p'")
        conn.commit()
        assert db.search_by_metadata({"status": "active"}) == []
        assert db.list_all_entities()[0].entity_metadata is None


# ── misc: busy timeout, stats, migration of old databases ───────────────────

def test_busy_timeout_and_stats_keys(kb):
    with kb.db() as db:
        conn = db._get_connection()
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert set(db.get_stats()) == {"entities", "observations", "relations",
                                       "unresolved_relations"}


def test_old_index_rows_are_reindexed_once(kb):
    """Entries written by an older version (no title) are re-read on the next sync."""
    kb.write("hs.md", note_md("Home server", permalink="hs"))
    kb.write("a.md", note_md("A", [("uses", "Home server")], permalink="a"))
    kb.sync(force=True)
    with kb.db() as db:
        conn = db._get_connection()
        conn.execute("UPDATE entities SET title = NULL")
        conn.execute("DELETE FROM relations")
        conn.commit()
    stats = kb.sync()
    assert stats["updated"] == 2
    assert ("a", "uses", "hs") in kb.links()
    assert kb.sync()["updated"] == 0


def test_write_note_round_trip_links(kb):
    """storage.write_note → index: title links resolve both ways."""
    storage.write_note("Note A", relations=[Relation("runs_on", "Home server")],
                       user_id=USER, db_path=kb.db_path)
    assert kb.pending("note-a") == [("runs_on", "Home server")]
    storage.write_note("Home server", content="A small box.", user_id=USER, db_path=kb.db_path)
    assert ("note-a", "runs_on", "home-server") in kb.links()
    storage.write_note("Note A", relations=[Relation("runs_on", "Home server")],
                       content="again", user_id=USER, db_path=kb.db_path)
    storage.write_note("Home server", content="A bigger box.", user_id=USER, db_path=kb.db_path)
    assert ("note-a", "runs_on", "home-server") in kb.links()
