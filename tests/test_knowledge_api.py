"""Knowledge base explorer API: graph, note detail, create/edit/trash, search, bases."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOTE_A = """---
title: "Python"
permalink: python
tags: ["lang", "code"]
---

# Python

A programming language.

## Observations

- [fact] Created by Guido #history
- [tip] Use venvs #tooling

## Relations

- uses [[Pip]]
- related_to [[Rust Language]]
"""

NOTE_B = """---
title: "Pip"
permalink: pip
tags: ["tooling"]
---

# Pip

- [fact] Installs packages
"""

NOTE_C = """---
title: "Lonely"
permalink: lonely
---

# Lonely
nothing links here
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    kdir = tmp_path / ".myclaw" / "knowledge" / "default"
    kdir.mkdir(parents=True)
    (kdir / "python.md").write_text(NOTE_A)
    (kdir / "pip.md").write_text(NOTE_B)
    (kdir / "lonely.md").write_text(NOTE_C)
    (tmp_path / ".myclaw" / "knowledge" / "12345").mkdir()
    from myclaw.web import knowledge_api

    app = FastAPI()
    app.include_router(knowledge_api.router)
    return TestClient(app), kdir


def test_bases_lists_default_first(client):
    c, _ = client
    bases = c.get("/api/knowledge/bases").json()["bases"]
    assert bases[0] == {"name": "default", "notes": 3}
    assert {"name": "12345", "notes": 0} in bases


def test_graph_nodes_edges_and_missing_targets(client):
    c, _ = client
    g = c.get("/api/knowledge/graph").json()
    ids = {n["id"]: n for n in g["nodes"]}
    assert {"python", "pip", "lonely"} <= set(ids)
    # [[Pip]] resolves to the existing note by title; [[Rust Language]] does not exist yet.
    assert {"source": "python", "target": "pip", "type": "uses"} in g["edges"]
    missing = [n for n in g["nodes"] if n["missing"]]
    assert len(missing) == 1 and missing[0]["title"] == "Rust Language"
    assert ids["python"]["observations"] == 2 and ids["python"]["degree"] == 2
    assert g["stats"]["notes"] == 3 and g["stats"]["orphans"] == 1 and g["stats"]["missing"] == 1
    assert {t["name"] for t in g["tags"]} >= {"lang", "tooling", "history"}
    assert {c_["name"] for c_ in g["categories"]} == {"fact", "tip"}
    assert "A programming language" in ids["python"]["preview"]


def test_note_detail_has_links_and_backlinks(client):
    c, _ = client
    d = c.get("/api/knowledge/note/pip").json()
    assert d["title"] == "Pip" and d["observations"][0]["category"] == "fact"
    assert d["backlinks"] == [{"type": "uses", "source": "python", "label": "Python"}]
    p = c.get("/api/knowledge/note/python").json()
    assert {(x["target"], x["exists"]) for x in p["links"]} == {("pip", True), ("missing:rust-language", False)}
    assert c.get("/api/knowledge/note/nope").status_code == 404
    assert c.get("/api/knowledge/note/..%2Fetc").status_code in (400, 404)


def test_create_edit_and_trash_note(client):
    c, kdir = client
    r = c.post("/api/knowledge/note", json={"name": "Rust Language", "tags": ["lang"], "content": "Fast and safe."})
    assert r.status_code == 200, r.text
    assert r.json()["permalink"] == "rust-language" and r.json()["tags"] == ["lang"]
    assert c.post("/api/knowledge/note", json={"name": "rust language"}).status_code == 409
    # The missing node is now a real note.
    g = c.get("/api/knowledge/graph").json()
    assert not any(n["missing"] for n in g["nodes"])

    new = NOTE_C.replace("nothing links here", "- part_of [[Python]]")
    r = c.put("/api/knowledge/note/lonely", json={"content": new})
    assert r.status_code == 200 and r.json()["links"][0]["target"] == "python"
    assert c.put("/api/knowledge/note/lonely", json={"content": "  "}).status_code == 400

    r = c.delete("/api/knowledge/note/lonely")
    assert r.status_code == 200
    assert not (kdir / "lonely.md").exists()
    assert len(list((kdir / ".trash").glob("lonely-*.md"))) == 1


def test_search_and_bad_base(client):
    c, _ = client
    assert "python" in c.get("/api/knowledge/search", params={"q": "Guido"}).json()["results"]
    assert c.get("/api/knowledge/search", params={"q": ""}).json()["results"] == []
    assert c.get("/api/knowledge/graph", params={"base": "../x"}).status_code == 400
    assert c.post("/api/knowledge/sync").json()["ok"] is True
