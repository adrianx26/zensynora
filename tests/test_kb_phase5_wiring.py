"""Web UI API shows notes in sub-folders (imports) and resolves [[x|label]] / [[x#y]] (2026-10-24)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from myclaw.web import knowledge_api

    app = FastAPI()
    app.include_router(knowledge_api.router)
    return TestClient(app)


def test_subfolder_notes_and_link_forms_in_graph(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    d = tmp_path / ".myclaw" / "knowledge" / "default"
    (d / "imported" / "vault").mkdir(parents=True)
    (d / ".trash").mkdir()
    (d / "imported" / "vault" / "pi.md").write_text('---\ntitle: "Pi"\npermalink: pi\n---\n\n# Pi\n\nSmall.\n')
    (d / ".trash" / "gone.md").write_text('---\ntitle: "Gone"\npermalink: gone\n---\n\n# Gone\n')
    (d / "home.md").write_text('---\ntitle: "Home"\npermalink: home\n---\n\n# Home\n\n## Relations\n\n'
                               "- has [[Pi|the pi]]\n- see [[pi#Specs]]\n- about [[C# tips]]\n")
    g = c.get("/api/knowledge/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert {"home", "pi"} <= ids and "gone" not in ids
    targets = sorted(e["target"] for e in g["edges"] if e["source"] == "home")
    assert targets.count("pi") == 2 and any(t.startswith("missing:") for t in targets)
    assert c.get("/api/knowledge/note/pi").status_code == 200
    bases = {b["name"]: b["notes"] for b in c.get("/api/knowledge/bases").json()["bases"]}
    assert bases["default"] == 2
