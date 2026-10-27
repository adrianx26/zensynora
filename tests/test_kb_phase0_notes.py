"""Knowledge base phase 0 (2026-10-23): notes, frontmatter and storage (agent B).

Markdown notes are the source of truth: tags/aliases are always lists, other
frontmatter keys survive a round trip, task boxes are text, writes are atomic,
update_note stores the file checksum, delete_note moves the file to .trash,
read_note finds notes in sub-folders.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    from myclaw.knowledge import storage

    storage.set_knowledge_root(None)
    return tmp_path


def _kdir(home):
    return home / ".myclaw" / "knowledge" / "default"


def _note(home, name, text):
    f = _kdir(home) / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


# ── 1. frontmatter ───────────────────────────────────────────────────────────

def test_scalar_tag_is_a_list_not_characters():
    from myclaw.knowledge.parser import parse_frontmatter

    assert parse_frontmatter("---\ntags: foo\n---\n")["tags"] == ["foo"]
    assert parse_frontmatter("---\ntags: '#foo'\n---\n")["tags"] == ["foo"]


def test_comma_and_inline_tags():
    from myclaw.knowledge.parser import parse_frontmatter

    assert parse_frontmatter("---\ntags: a, #b, \"c\"\n---\n")["tags"] == ["a", "b", "c"]
    assert parse_frontmatter("---\ntags: [\"a\", 'b', c]\n---\n")["tags"] == ["a", "b", "c"]
    assert parse_frontmatter("---\ntags: []\n---\n")["tags"] == []
    assert parse_frontmatter("---\ntags:\n---\n")["tags"] == []


def test_block_list_tags_and_aliases():
    from myclaw.knowledge.parser import parse_frontmatter

    fm = parse_frontmatter(
        "---\ntitle: Home server\ntags:\n  - homelab\n  - \"#linux\"\n"
        "aliases:\n- HS\n- 'The box'\ntype: machine\n---\n# Home server\n")
    assert fm["tags"] == ["homelab", "linux"]
    assert fm["aliases"] == ["HS", "The box"]
    assert fm["title"] == "Home server" and fm["type"] == "machine"


def test_aliases_comma_list_keeps_spaces():
    from myclaw.knowledge.parser import parse_frontmatter

    assert parse_frontmatter("---\naliases: Home server, HS\n---\n")["aliases"] == ["Home server", "HS"]


@pytest.mark.parametrize("text", [
    "---\n: : :\n\t- \n[[[\n---\n", "---\nonly the start", "", "---\n---\n",
    "---\ntags: [unclosed\nk: {bad json}\n---\n", "﻿---\r\ntags: a\r\n---\r\nbody",
])
def test_broken_frontmatter_never_raises(text):
    from myclaw.knowledge.parser import parse_frontmatter

    assert isinstance(parse_frontmatter(text), dict)
    assert parse_frontmatter(None) == {}


def test_crlf_and_bom_frontmatter():
    from myclaw.knowledge.parser import parse_frontmatter

    assert parse_frontmatter("﻿---\r\ntags: a\r\n---\r\nbody")["tags"] == ["a"]


# ── 2./3. aliases + metadata in Note, written back ──────────────────────────

def test_parse_note_fills_aliases_and_metadata(home):
    from myclaw.knowledge.parser import parse_note

    f = _note(home, "pi.md", "---\ntitle: Pi\npermalink: pi\ntags: hw\naliases: [Raspberry]\n"
              "type: device\nstatus: \"in use: yes\"\nsource: https://x.org/a#b\n"
              "created: 2026-01-02T03:04:05\n---\n\n# Pi\n\ntext\n")
    n = parse_note(f)
    assert n.tags == ["hw"] and n.aliases == ["Raspberry"]
    assert n.metadata == {"type": "device", "status": "in use: yes", "source": "https://x.org/a#b"}
    assert n.created_at.year == 2026


def test_metadata_round_trip_is_stable(home):
    from myclaw.knowledge.parser import generate_markdown, parse_note

    f = _note(home, "m.md", "---\ntitle: M\npermalink: m\ntags:\n  - a\naliases: Em, The M\n"
              "type: note\nstatus: draft\nscore: 5\ndone: true\nurl: \"http://x/y?q=1\"\n"
              "people: [Ann, \"Bob, Jr.\"]\nsrc:\n  url: http://z\ndesc: |\n  line 1\n  line 2\n"
              "---\n\n# M\n\nBody text.\n- [ ] a task\n\n## Observations\n\n- [fact] one #t\n")
    first = generate_markdown(parse_note(f))
    f.write_text(first, encoding="utf-8")
    second = generate_markdown(parse_note(f))
    assert first == second
    n = parse_note(f)
    assert n.aliases == ["Em", "The M"] and n.tags == ["a"]
    assert n.metadata["type"] == "note" and n.metadata["status"] == "draft"
    assert n.metadata["people"] == ["Ann", "Bob, Jr."]
    assert n.metadata["url"] == "http://x/y?q=1"
    assert n.metadata["src"] == {"url": "http://z"}
    assert n.metadata["desc"] == "line 1\nline 2"
    assert "Body text." in first and "- [ ] a task" in first
    assert [(o.category, o.content) for o in n.observations] == [("fact", "one")]


def test_generate_markdown_skips_reserved_and_quotes_odd_metadata_keys():
    from myclaw.knowledge.parser import Note, generate_markdown, parse_frontmatter

    md = generate_markdown(Note(name="x", permalink="x", title="X", content="",
                                metadata={"title": "no", "odd key!": 1, "ok": "yes"}))
    assert "ok: yes" in md and '"odd key!": 1' in md and md.count("title:") == 1
    assert parse_frontmatter(md)["odd key!"] == "1"


# ── 4. task boxes ────────────────────────────────────────────────────────────

def test_checkboxes_are_not_observations_and_stay_in_the_body():
    from myclaw.knowledge.parser import extract_body, parse_observations

    text = "Plan\n- [ ] todo\n- [x] done\n- [X] also done\n- [idea] real fact #t"
    obs = parse_observations(text)
    assert [(o.category, o.content, o.tags) for o in obs] == [("idea", "real fact", ["t"])]
    body = extract_body(text)
    assert "- [ ] todo" in body and "- [x] done" in body and "- [X] also done" in body
    assert "real fact" not in body


def test_write_note_keeps_checkbox_lines(home):
    from myclaw.knowledge import read_note, write_note

    write_note("Tasks", content="My list\n- [ ] buy milk\n- [x] call Bob")
    raw = (_kdir(home) / "tasks.md").read_text()
    assert "- [ ] buy milk" in raw and "- [x] call Bob" in raw
    assert read_note("tasks").observations == []


# ── 5. write_note keeps aliases / metadata, atomic ──────────────────────────

def test_write_note_stores_and_keeps_aliases_and_metadata(home):
    from myclaw.knowledge import read_note, write_note

    write_note("Server", content="Text.", tags=["a"], aliases=["Srv"], metadata={"type": "machine"})
    n = read_note("server")
    assert n.aliases == ["Srv"] and n.metadata == {"type": "machine"} and n.tags == ["a"]
    created = n.created_at
    write_note("Server", content="New text.", tags=["b"])  # no aliases/metadata given
    n = read_note("server")
    assert n.aliases == ["Srv"] and n.metadata == {"type": "machine"} and n.created_at == created
    assert "New text." in (_kdir(home) / "server.md").read_text()
    write_note("Server", content="New text.", aliases=[], metadata={"status": "old"})
    n = read_note("server")
    assert n.aliases == [] and n.metadata == {"status": "old"}


def test_write_note_whole_file_plus_metadata(home):
    from myclaw.knowledge import read_note, write_note

    doc = '---\ntitle: "X"\npermalink: x\ntags: ["t"]\n---\n\n# X\n\nbody\n- [k] fact\n'
    write_note("x", content=doc, metadata={"type": "memo"})
    n = read_note("x")
    assert n.metadata == {"type": "memo"} and n.tags == ["t"]
    assert "body" in n.content and [o.content for o in n.observations] == ["fact"]


def test_write_note_is_atomic_and_leaves_no_temp_file(home, monkeypatch):
    import os

    from myclaw.knowledge import storage, write_note

    write_note("Atom", content="first")
    calls = []
    real = os.replace
    monkeypatch.setattr(storage.os, "replace", lambda a, b: (calls.append((a, b)), real(a, b)))
    write_note("Atom", content="second")
    assert calls and Path(calls[0][1]) == _kdir(home) / "atom.md"
    assert "second" in (_kdir(home) / "atom.md").read_text()
    assert not [p for p in _kdir(home).iterdir() if p.name.endswith(".tmp")]


def test_a_write_note_positional_still_works(home):
    import asyncio

    from myclaw.knowledge import a_write_note, read_note

    asyncio.run(a_write_note("Async", "Async title", None, None, ["x"], "default", "Some text"))
    n = read_note("async")
    assert n.title == "Async title" and n.tags == ["x"] and "Some text" in n.content


# ── 6. update_note ───────────────────────────────────────────────────────────

def _entity(permalink):
    from myclaw.knowledge.db import KnowledgeDB

    with KnowledgeDB("default") as db:
        return db.get_entity_by_permalink(permalink)


def test_update_note_stores_checksum_and_keeps_text(home):
    from myclaw.knowledge import read_note, update_note, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Box", content="Important text.", tags=["a"], aliases=["B"], metadata={"type": "x"},
               observations=[Observation("spec", "4 cores", [])])
    assert update_note("box", tags=["a", "b"]) is True
    f = _kdir(home) / "box.md"
    e = _entity("box")
    assert e.checksum == hashlib.md5(f.read_bytes()).hexdigest()
    assert e.size == f.stat().st_size
    n = read_note("box")
    assert "Important text." in f.read_text() and n.tags == ["a", "b"]
    assert n.aliases == ["B"] and n.metadata == {"type": "x"}
    assert [o.content for o in n.observations] == ["4 cores"]


def test_update_note_new_content_keeps_facts_and_links(home):
    from myclaw.knowledge import read_note, update_note, write_note
    from myclaw.knowledge.parser import Observation

    write_note("Box", content="Old text.", observations=[Observation("spec", "4 cores", [])])
    assert update_note("box", content="New text.\n- [os] Debian\n- runs [[Pihole]]",
                       aliases=["Bx"], metadata={"status": "live"})
    n = read_note("box")
    raw = (_kdir(home) / "box.md").read_text()
    assert "New text." in raw and "Old text." not in raw
    assert [(o.category, o.content) for o in n.observations] == [("spec", "4 cores"), ("os", "Debian")]
    assert [r.target for r in n.relations] == ["Pihole"]
    assert n.aliases == ["Bx"] and n.metadata == {"status": "live"}
    assert _entity("box").checksum == hashlib.md5((_kdir(home) / "box.md").read_bytes()).hexdigest()


def test_update_note_missing_returns_false(home):
    from myclaw.knowledge import update_note

    assert update_note("nope", tags=["x"]) is False


# ── 7. read_note fallback ────────────────────────────────────────────────────

def test_read_note_falls_back_to_the_indexed_subfolder_file(home):
    from myclaw.knowledge import delete_note, read_note, sync_knowledge
    from myclaw.knowledge.storage import _batch_read_notes

    _note(home, "projects/Big Plan.md",
          "---\ntitle: Big Plan\npermalink: big-plan\n---\n\n# Big Plan\n\nSteps.\n")
    sync_knowledge("default")
    n = read_note("big-plan")
    assert n is not None and n.title == "Big Plan" and "Steps." in n.content
    assert [x.permalink for x in _batch_read_notes(["big-plan", "missing"], "default")] == ["big-plan"]
    assert read_note("missing") is None
    assert delete_note("big-plan") is True
    assert not (_kdir(home) / "projects" / "Big Plan.md").exists()
    assert read_note("big-plan") is None


def test_read_note_ignores_index_paths_outside_the_knowledge_dir(home):
    from myclaw.knowledge import read_note
    from myclaw.knowledge.db import KnowledgeDB

    outside = home / "secret.md"
    outside.write_text("---\ntitle: S\n---\nsecret\n")
    with KnowledgeDB("default") as db:
        db.create_entity(name="S", permalink="s", file_path=str(outside))
    assert read_note("s") is None
    assert read_note("../secret") is None


# ── 8. delete_note → .trash ──────────────────────────────────────────────────

def test_delete_note_moves_the_file_to_trash(home):
    from myclaw.knowledge import delete_note, read_note, write_note

    write_note("Gone", content="Keep me safe.", tags=["t"])
    original = (_kdir(home) / "gone.md").read_bytes()
    assert delete_note("gone") is True
    assert not (_kdir(home) / "gone.md").exists()
    trashed = list((_kdir(home) / ".trash").glob("gone-*.md"))
    assert len(trashed) == 1 and trashed[0].read_bytes() == original
    assert read_note("gone") is None and _entity("gone") is None
    assert delete_note("gone") is False

    write_note("Gone", content="Second copy.")  # same second: no overwrite
    assert delete_note("gone") is True
    assert len(list((_kdir(home) / ".trash").glob("gone-*.md"))) == 2


# ── 9. list_notes ────────────────────────────────────────────────────────────

def test_list_notes_tag_filter_is_case_insensitive_and_skips_bad_files(home):
    from myclaw.knowledge import list_notes, write_note

    write_note("A", content="a", tags=["HomeLab"])
    write_note("B", content="b", tags=["other"])
    (_kdir(home) / "bad.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    _note(home, ".trash/old.md", "---\ntags: homelab\n---\n")
    _note(home, "sub/c.md", "---\ntitle: C\ntags: homelab\n---\n")
    assert sorted(n.permalink for n in list_notes(tags=["homelab"])) == ["a", "c"]
    assert sorted(n.permalink for n in list_notes(tags=["#HOMELAB"])) == ["a", "c"]
    assert sorted(n.permalink for n in list_notes()) == ["a", "b", "c"]


# ── 10. knowledge root ───────────────────────────────────────────────────────

def test_knowledge_root_and_scope_migration_use_the_configured_folder(home, tmp_path):
    from myclaw.knowledge import scope, storage

    assert storage.knowledge_root() == home / ".myclaw" / "knowledge"
    other = tmp_path / "elsewhere"
    (other / "webui").mkdir(parents=True)
    (other / "webui" / "n.md").write_text("---\ntitle: N\n---\n\nsome text here\n")
    try:
        storage.set_knowledge_root(other)
        assert storage.knowledge_root() == other
        assert storage.get_knowledge_dir("default") == other / "default"
        scope.migrate_internal_scopes()
        assert (other / "default" / "n.md").exists()
    finally:
        storage.set_knowledge_root(None)
    assert storage.knowledge_root() == home / ".myclaw" / "knowledge"


# ── 11. exports ──────────────────────────────────────────────────────────────

def test_star_import_works():
    code = ("from myclaw.knowledge import *\n"
            "import myclaw.knowledge as k\n"
            "missing = [n for n in k.__all__ if not hasattr(k, n)]\n"
            "assert not missing, missing\n"
            "assert get_backlinks and search_by_metadata and knowledge_root and set_knowledge_root\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr


# ── 12. search_notes(boolean=) ───────────────────────────────────────────────

def test_search_notes_accepts_boolean(home):
    from myclaw.knowledge import search_notes, write_note

    write_note("Alpha", content="apples and pears")
    assert [n.permalink for n in search_notes("apples", boolean=True)] == ["alpha"]
    assert [n.permalink for n in search_notes("apples")] == ["alpha"]


# ── review fixes (2026-10-23) ────────────────────────────────────────────────

def _trash(home):
    return sorted((_kdir(home) / ".trash").glob("*.md")) if (_kdir(home) / ".trash").is_dir() else []


HAND = ("---\ntags: [homelab]\nstatus: active\n---\n# Home Server\n\nMy hand-written notes: rack, UPS.\n\n"
        "## Observations\n- [spec] 64 GB RAM\n\n## Relations\n- hosts [[Nextcloud]]\n")


def test_r1_lossy_rewrite_keeps_the_old_file_in_trash(home, caplog):
    import logging

    from myclaw.knowledge import sync_knowledge, write_note

    f = _note(home, "Projects/Home Server.md", HAND)
    sync_knowledge("default")
    with caplog.at_level(logging.INFO, logger="myclaw.knowledge.storage"):
        write_note(name="Home Server", title="Home Server",
                   content="The home server runs Debian.", tags=["linux"])
    assert "runs Debian" in f.read_text()  # same note, replaced in place
    assert not (_kdir(home) / "home-server.md").exists()
    kept = _trash(home)
    assert len(kept) == 1 and kept[0].read_text() == HAND
    assert kept[0].name.startswith("Home Server-")
    assert "previous version kept in .trash/" in caplog.text


def test_r1_no_trash_copy_when_nothing_is_lost(home):
    from myclaw.knowledge import read_note, update_note, write_note

    write_note("Diary", content="Day one.", tags=["a"])
    write_note("Diary", content="Day one.\n\nDay two.", tags=["a", "b"])  # append + new tag
    update_note("diary", tags=["a", "b", "c"])
    update_note("diary")
    assert _trash(home) == []
    update_note("diary", tags=["a"])  # drops tags b, c
    assert len(_trash(home)) == 1
    update_note("diary", content="Completely new.")
    assert len(_trash(home)) == 2 and "Day two." in _trash(home)[-1].read_text()
    assert read_note("diary").tags == ["a"]


def test_r2_a_file_named_like_the_note_but_holding_another_permalink(home):
    from myclaw.knowledge import delete_note, read_note, sync_knowledge, update_note, write_note

    other = _note(home, "x.md", "---\ntitle: Y\npermalink: y\n---\n\n# Y\n\nI am note y.\n")
    sync_knowledge("default")
    assert read_note("x") is None
    assert delete_note("x") is False and update_note("x", tags=["t"]) is False
    assert other.exists() and read_note("y").title == "Y"
    write_note("x", content="I am note x.")
    assert "I am note y." in other.read_text()
    n = read_note("x")
    assert n is not None and "I am note x." in n.content and n.file_path.name == "x-2.md"
    assert read_note("y").file_path == other


def test_r3_pasted_permalink_cannot_take_over_another_note(home):
    from myclaw.knowledge import read_note, write_note
    from myclaw.knowledge.db import KnowledgeDB

    write_note("Victim", content="victim body")
    write_note("Attacker", content="---\ntitle: Attacker\npermalink: victim\n---\n\nmine\n")
    with KnowledgeDB("default") as db:
        assert Path(db.get_entity_by_permalink("victim").file_path).name == "victim.md"
        assert Path(db.get_entity_by_permalink("attacker").file_path).name == "attacker.md"
    assert "victim body" in read_note("victim").content
    assert read_note("attacker").permalink == "attacker"
    assert "permalink: attacker" in (_kdir(home) / "attacker.md").read_text()


def test_r4_whole_file_content_merges_the_other_arguments(home):
    from myclaw.knowledge import read_note
    from myclaw.tools.kb import write_to_knowledge

    write_to_knowledge("Full", "---\ntitle: Full\n---\nbody", tags="t1, t2",
                       observations="spec | 32 GB", relations="uses | Notes")
    n = read_note("full")
    assert n.tags == ["t1", "t2"] and [(o.category, o.content) for o in n.observations] == [("spec", "32 GB")]
    assert [(r.relation_type, r.target) for r in n.relations] == [("uses", "Notes")]
    assert "body" in n.content


def test_r4_a_body_starting_with_a_rule_is_text(home):
    from myclaw.knowledge import read_note, write_note

    write_note("Ruled", content="---\nNot frontmatter, just a rule above.", tags=["t"])
    n = read_note("ruled")
    assert n.tags == ["t"] and "Not frontmatter, just a rule above." in n.content
    assert n.title == "Ruled"


SECTIONS = """---
title: Sections
---

# A Different Heading

Intro text.

## Notes

Some general notes paragraph.

## Observations

- [fact] Earth is round #geo

## Tasks

- [ ] buy milk

```
- [code] not a fact
- uses [[NotALink]]
```

## Relations

- uses [[Other]]

## Further reading

- see [[Elsewhere]]
- related [[Third]]
"""


def test_r5_facts_and_links_from_every_section(home):
    from myclaw.knowledge import read_note, sync_knowledge, update_note

    f = _note(home, "sections.md", SECTIONS)
    sync_knowledge("default")
    n = read_note("sections")
    assert [(o.category, o.content) for o in n.observations] == [("fact", "Earth is round")]
    assert [(r.relation_type, r.target) for r in n.relations] == [
        ("uses", "Other"), ("see", "Elsewhere"), ("related", "Third")]
    update_note("sections")
    raw = f.read_text()
    for text in ("# A Different Heading", "Intro text.", "Some general notes paragraph.",
                 "- [ ] buy milk", "- [code] not a fact", "- uses [[NotALink]]",
                 "- [fact] Earth is round #geo", "- see [[Elsewhere]]", "- related [[Third]]"):
        assert text in raw, text
    assert raw.count("Earth is round") == 1 and raw.count("[[Elsewhere]]") == 1
    assert _trash(home) == []
    again = read_note("sections")
    assert [(r.relation_type, r.target) for r in again.relations] == [
        ("uses", "Other"), ("see", "Elsewhere"), ("related", "Third")]


def test_r5_note_titled_links_keeps_its_relations(home):
    from myclaw.knowledge import read_note, sync_knowledge, update_note

    _note(home, "links.md", "---\ntitle: Links\n---\n# Links\n\nIntro.\n\n## More\n\n- uses [[A]]\n- [k] v\n")
    sync_knowledge("default")
    assert [r.target for r in read_note("links").relations] == ["A"]
    update_note("links")
    n = read_note("links")
    assert [r.target for r in n.relations] == ["A"] and [o.content for o in n.observations] == ["v"]


GNARLY = """---
title: Nested
source:
  url: http://x
  urls:
    - a
    - b
Date Created: 2024-01-01
"quoted key": v
ключ: значение
2fa: yes
name: Custom Name
created: Jan 1 2024
tags:
  - alpha
  - beta gamma
# a comment
desc: |
  line one
  line two
deep:
  a:
    b: c
---

# Nested

Some body.
"""


def test_r6_gnarly_frontmatter_survives_a_no_op_update(home):
    from myclaw.knowledge import read_note, sync_knowledge, update_note
    from myclaw.knowledge.parser import parse_frontmatter

    f = _note(home, "nested.md", GNARLY)
    sync_knowledge("default")
    before = parse_frontmatter(GNARLY)
    assert before["source"] == {"url": "http://x", "urls": ["a", "b"]}
    assert before["deep"] == {"a": {"b": "c"}}
    update_note("nested")
    raw = f.read_text()
    after = parse_frontmatter(raw)
    for key, value in before.items():
        assert after[key] == value, key
    # unchanged keys keep their original lines
    for block in ("source:\n  url: http://x\n  urls:\n    - a\n    - b", "Date Created: 2024-01-01",
                  '"quoted key": v', "ключ: значение", "2fa: yes", "name: Custom Name",
                  "created: Jan 1 2024", "tags:\n  - alpha\n  - beta gamma",
                  "# a comment\ndesc: |\n  line one\n  line two", "deep:\n  a:\n    b: c"):
        assert block in raw, block
    n = read_note("nested")
    assert n.name == "Custom Name" and n.created_at is None and "Some body." in n.content
    assert _trash(home) == []
    update_note("nested")
    assert f.read_text().split("updated:")[0] == raw.split("updated:")[0]


def test_r6_changed_values_are_written_and_read_back(home):
    from myclaw.knowledge import read_note, sync_knowledge, update_note

    _note(home, "nested.md", GNARLY)
    sync_knowledge("default")
    meta = dict(read_note("nested").metadata)
    meta["source"] = {"url": "http://y", "urls": ["c"], "deep": {"k": "v"}}
    meta["Date Created"] = "2025-02-02"
    meta["новый"] = ["x", "y z"]
    update_note("nested", metadata=meta)
    assert read_note("nested").metadata == meta


# ── performance ──────────────────────────────────────────────────────────────

def test_r7_one_index_lookup_per_batch(home, monkeypatch):
    from myclaw.knowledge import get_all_tags, search_notes, sync_knowledge
    from myclaw.knowledge import db as dbm
    from myclaw.knowledge.storage import _batch_read_notes

    for i in range(30):
        _note(home, f"folder/Note {i}.md", f"---\ntitle: Note {i}\ntags: [t{i % 3}]\n---\n\nlorem {i}\n")
    sync_knowledge("default")
    opens = {"n": 0}
    real = dbm.KnowledgeDB._init_db

    def counted(self):
        opens["n"] += 1
        return real(self)

    monkeypatch.setattr(dbm.KnowledgeDB, "_init_db", counted)
    notes = _batch_read_notes([f"note-{i}" for i in range(30)], "default")
    assert len(notes) == 30 and opens["n"] == 1
    opens["n"] = 0
    assert get_all_tags("default") == ["t0", "t1", "t2"] and opens["n"] == 2
    opens["n"] = 0
    assert len(search_notes("lorem", limit=20)) == 20 and opens["n"] == 2
