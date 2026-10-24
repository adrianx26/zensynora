"""KB phase 5 (2026-10-24): ingestion into the knowledge base.

Covers myclaw/knowledge/ingest/ (document → linked notes, sources, readers,
importers, log), the agent tools in myclaw/tools/ingest.py, the Web UI routes
in myclaw/web/knowledge_import_api.py and the CLI commands. No network: the
HTTP fetcher is replaced; optional readers are skipped when not installed.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from myclaw.knowledge.ingest import Budget, Document, IngestError, Section, write_document_notes
from myclaw.knowledge.ingest import notes as notes_mod
from myclaw.knowledge.ingest import sources


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from myclaw.knowledge import storage
    from myclaw.knowledge.sync import clear_note_cache, reset_sync_failures
    from myclaw.tools import core

    storage.set_knowledge_root(None)
    reset_sync_failures()
    clear_note_cache()
    ws = tmp_path / ".myclaw" / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(core, "WORKSPACE", ws)
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    return tmp_path


def kdir(home: Path) -> Path:
    return home / ".myclaw" / "knowledge" / "default"


def index_links():
    """{(source, relation, target)} of resolved links in the index."""
    from myclaw.knowledge.db import KnowledgeDB

    with KnowledgeDB("default") as db:
        rows = db._get_connection().execute(
            "SELECT a.permalink AS s, r.relation_type AS t, b.permalink AS d FROM relations r "
            "JOIN entities a ON a.id = r.from_entity_id JOIN entities b ON b.id = r.to_entity_id"
        ).fetchall()
    return {(r["s"], r["t"], r["d"]) for r in rows}


def note(permalink):
    from myclaw.knowledge.storage import read_note

    return read_note(permalink, "default")


def sample_doc(extra: str = "") -> Document:
    return Document(
        title="Field guide", source="/data/field-guide.pdf", kind="pdf", size=4096,
        date="2026-01-02",
        sections=[
            Section("Birds", "Birds have feathers. " * 30 + extra, key="birds",
                    links=[("links_to", "Trees")]),
            Section("Trees", "Trees have leaves. " * 30, key="trees"),
            ("Glossary", "Short words."),
        ])


# ── document → notes ─────────────────────────────────────────────────────────
def test_document_becomes_overview_and_linked_parts(home):
    r = write_document_notes(sample_doc(), "default")
    assert r.overview is not None and r.count("created") == 3
    over = note(r.overview.permalink)
    assert over.title == "Field guide"
    assert {"assimilated", "pdf"} <= set(over.tags)
    facts = {(o.category, o.content) for o in over.observations}
    assert ("source", "/data/field-guide.pdf") in facts and ("kind", "pdf") in facts
    assert ("size", "4.0 KB") in facts and ("date", "2026-01-02") in facts
    assert "```mermaid\ngraph TD" in over.content and "Short words." in over.content
    parts = [n.permalink for n in r.notes if n.permalink != r.overview.permalink]
    links = index_links()
    for p in parts:  # part_of links resolve in the index
        assert (p, "part_of", r.overview.permalink) in links
    birds, trees = parts
    assert (birds, "links_to", trees) in links
    assert note(birds).metadata.get("source") == "/data/field-guide.pdf"


def test_reingest_updates_instead_of_duplicating(home):
    first = write_document_notes(sample_doc(), "default")
    files = sorted(p.name for p in kdir(home).glob("*.md"))
    again = write_document_notes(sample_doc(), "default")
    assert again.count("unchanged") == 3 and again.count("created") == 0
    changed = write_document_notes(sample_doc(extra="New sentence about owls."), "default")
    assert sorted(p.name for p in kdir(home).glob("*.md")) == files
    assert changed.count("created") == 0 and changed.count("updated") >= 1
    assert [n.permalink for n in changed.notes] == [n.permalink for n in first.notes]
    birds = next(n.permalink for n in changed.notes if n.permalink.endswith("birds"))
    assert "owls" in note(birds).content


def test_long_sections_are_chunked_and_fences_stay_closed(home):
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(4000)) + "\n```"
    doc = Document(title="Big", source="big.txt", kind="text",
                   sections=[Section("Code", code, key="code"), Section("Other", "y " * 400)])
    r = write_document_notes(doc, "default", max_chars=6000)
    parts = [n for n in r.notes if n.permalink != r.overview.permalink]
    assert len(parts) > 3
    for ref in parts:
        n = note(ref.permalink)
        body = n.content.split("\n---\n", 1)[1]
        assert len(body) <= 6000 + 400
        assert body.count("```") % 2 == 0
        assert any(rel.relation_type == "part_of" for rel in n.relations)  # not swallowed by a fence
    assert any(rel.relation_type == "continues" for rel in note(parts[1].permalink).relations)


def test_note_limit_is_respected_and_reported(home):
    doc = Document(title="Many", source="many", kind="text",
                   sections=[Section(f"S{i}", f"text {i} " * 60, key=f"s{i}") for i in range(10)])
    r = write_document_notes(doc, "default", max_notes=4)
    assert len(r.notes) == 4 and r.stopped.startswith("note limit")
    assert any("S9" in s for s in r.skipped)
    assert "Left out" in note(r.overview.permalink).content


def test_time_limit_writes_the_overview_and_stops(home):
    budget = Budget(max_notes=50, seconds=None)
    budget.cancel.set()
    r = write_document_notes(sample_doc(), "default", budget=budget)
    assert [n.permalink for n in r.notes] == [r.overview.permalink]
    assert r.stopped and "Stopped early" in note(r.overview.permalink).content


def test_clean_body_escapes_fact_lines_and_reserved_headings():
    body = notes_mod.clean_body("- [1] a reference\n## Observations\n```\n- [x] keep\n")
    assert "- \\[1] a reference" in body
    assert "## Observations (from the source)" in body
    assert body.rstrip().endswith("```") and body.count("```") == 2


def test_mermaid_labels_are_escaped():
    d = notes_mod.sections_diagram('A "quoted" [title]', [("a", "x|y <b>"), ("b", "{c}")],
                                   [("a", "links_to", "b")])
    assert 'root["A \'quoted\' (title)"]' in d
    assert "x/y ‹b›" in d and "(c)" in d and "p0 -.->|links_to| p1" in d


# ── secrets and paths ────────────────────────────────────────────────────────
def test_secrets_are_skipped_and_redacted(home):
    from myclaw.knowledge.ingest import assimilate

    proj = home / "proj"
    proj.mkdir()
    (proj / ".env").write_text("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\n")
    (proj / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n")
    (proj / "credentials.json").write_text('{"token": "abc"}')
    (proj / "README.md").write_text("# Proj\n\nThe token is ghp_abcdefghijklmnopqrstuvwxyz0123 here.\n"
                                    "password = hunter2secret\n" + "More text. " * 40)
    (proj / "app.py").write_text("API_KEY = 'sk-zyxwvutsrqponmlkjihgfedcba987654'\nprint('hi')\n" * 3)
    r = assimilate(str(proj), max_notes=10)
    texts = "\n".join(p.read_text() for p in kdir(home).glob("*.md"))
    for secret in ("sk-abcdefghij", "ghp_abcdefghij", "hunter2secret", "sk-zyxwvuts", "OPENSSH"):
        assert secret not in texts
    assert "[redacted]" in texts
    assert any("id_rsa" in s for s in r.skipped) and any("credentials.json" in s for s in r.skipped)


def test_local_paths_outside_the_allowed_roots_are_refused(home, tmp_path_factory):
    from myclaw.knowledge.ingest.safety import check_local_path
    from myclaw.tools.ingest import assimilate as tool

    with pytest.raises(IngestError, match="outside the folders"):
        check_local_path("/etc")
    ssh = home / ".ssh"
    ssh.mkdir()
    (ssh / "notes.txt").write_text("x")
    with pytest.raises(IngestError, match="hidden folder"):
        check_local_path(str(ssh / "notes.txt"))
    with pytest.raises(IngestError, match="hidden folder"):
        check_local_path("~/.myclaw/config.json")
    (home / "doc.txt").write_text("fine")
    assert check_local_path("~/doc.txt") == (home / "doc.txt").resolve()
    ws_file = home / ".myclaw" / "workspace" / "w.txt"
    ws_file.write_text("workspace file")
    assert check_local_path("w.txt") == ws_file.resolve()  # relative = workspace
    msg = tool("/etc/hostname")
    assert msg.startswith("Could not assimilate") and "home folder" in msg


def test_folder_walk_honours_gitignore_and_skips_tool_folders(home):
    root = home / "repo"
    (root / "build").mkdir(parents=True)
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / ".gitignore").write_text("logs/\n*.tmp\n/secret_dir\n")
    for rel in ("main.py", "build/out.py", "node_modules/x/i.js", "logs/a.log", "a.tmp", "keep.md"):
        (root / rel).write_text("x")
    entries, _ = sources.walk_folder(root, Budget())
    assert sorted(e.path for e in entries) == ["keep.md", "main.py"]


# ── GitHub (mocked REST API) ─────────────────────────────────────────────────
def _fake_github(monkeypatch, status=200, headers=None):
    files = {
        "README.md": "# Demo\n\nA demo project.\n\nSee [the guide](docs/guide.md).\n",
        "pyproject.toml": '[project]\nname="demo"\ndescription="Demo app"\ndependencies=["httpx>=0.27", "rich"]\n'
                          '[project.scripts]\ndemo = "demo.cli:main"\n',
        "demo/__init__.py": "",
        "demo/core.py": "from demo import util\nfrom .util import helper\n\ndef run():\n    return helper()\n",
        "demo/util.py": "def helper():\n    return 1\n",
        "app/main.py": "import demo.core\n\ndemo.core.run()\n",
        "docs/guide.md": "# Guide\n\nHow to use it. " + "Words. " * 50,
        ".env": "SECRET=1",
    }
    calls = []

    def fake_get(url, headers=None, max_bytes=0, timeout=0):
        calls.append((url, dict(headers or {})))
        if status != 200:
            return sources.HttpResponse(status, url, b"{}", headers or {})
        if url == "https://api.github.com/repos/owner/demo":
            body = {"default_branch": "main", "description": "Demo repository", "language": "Python",
                    "license": {"spdx_id": "MIT"}, "stargazers_count": 3, "topics": ["demo"]}
        elif url.startswith("https://api.github.com/repos/owner/demo/git/trees/main"):
            body = {"tree": [{"type": "blob", "path": p, "size": len(t)} for p, t in files.items()]
                    + [{"type": "tree", "path": "demo"}], "truncated": False}
        elif url.startswith("https://raw.githubusercontent.com/owner/demo/main/"):
            path = url.split("/main/", 1)[1]
            return sources.HttpResponse(200, url, files[path].encode(), {})
        else:
            return sources.HttpResponse(404, url, b"", {})
        return sources.HttpResponse(200, url, json.dumps(body).encode(), {"content-type": "application/json"})

    monkeypatch.setattr(sources, "http_get", fake_get)
    return calls


def test_github_repo_gives_architecture_note_and_diagram(home, monkeypatch):
    from myclaw.knowledge.ingest import assimilate

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtokentesttokentesttoken123")
    calls = _fake_github(monkeypatch)
    r = assimilate("https://github.com/owner/demo", max_notes=20)
    assert r.kind == "github" and r.overview.title == "owner/demo (GitHub)"
    over = note(r.overview.permalink)
    facts = {(o.category, o.content) for o in over.observations}
    assert ("language", "Python: 4 files") in facts
    assert ("entry_point", "app/main.py") in facts and ("license", "MIT") in facts
    assert any(c == "dependency" and t.startswith("httpx>=0.27") for c, t in facts)
    assert "## Architecture" in over.content and "Demo repository" in over.content
    assert "```mermaid" in over.content and "-.->|imports|" in r.diagram  # app → demo
    links = index_links()
    core = next(n.permalink for n in r.notes if n.permalink.endswith("demo-core-py"))
    util = next(n.permalink for n in r.notes if n.permalink.endswith("demo-util-py"))
    assert (core, "imports", util) in links
    readme = next(n.permalink for n in r.notes if n.permalink.endswith("readme-md"))
    guide = next(n.permalink for n in r.notes if n.permalink.endswith("docs-guide-md"))
    assert (readme, "links_to", guide) in links
    assert not any(".env" in u for u, _ in calls)  # secret files are never fetched
    assert all(h.get("Authorization", "").startswith("Bearer ") for _, h in calls)
    # the same repository again: updated in place
    again = assimilate("https://github.com/owner/demo", max_notes=20)
    assert again.count("created") == 0


def test_github_errors_are_actionable(home, monkeypatch):
    from myclaw.knowledge.ingest import assimilate

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _fake_github(monkeypatch, status=404)
    with pytest.raises(IngestError, match="GITHUB_TOKEN"):
        assimilate("https://github.com/owner/demo")
    _fake_github(monkeypatch, status=403, headers={"x-ratelimit-reset": "1700000000"})
    with pytest.raises(IngestError, match="rate limit"):
        assimilate("https://github.com/owner/demo")
    assert sources.parse_github("https://github.com/o/r/tree/dev/src/x") == ("o", "r", "dev", "src/x")
    assert sources.parse_github("https://github.com/o/r.git") == ("o", "r", None, "")
    assert sources.parse_github("https://gitlab.com/o/r") is None


# ── web pages ────────────────────────────────────────────────────────────────
PAGE = """<html><head><title>Garden notes</title><meta name="description" content="All about gardens"></head>
<body><nav><a href="/menu">Menu</a></nav><main><h1>Garden notes</h1><p>Intro text.</p>
<h2>Soil</h2><p>Soil needs compost. <a href="https://example.org/compost#top">Compost</a></p>
<ul><li>sand</li><li>clay</li></ul><h2>Water</h2><pre>water(daily)</pre><script>evil()</script>
</main><footer>Footer text</footer></body></html>"""


def test_url_page_becomes_notes(home, monkeypatch):
    from myclaw.knowledge.ingest import assimilate

    monkeypatch.setattr(sources, "http_get", lambda url, **kw: sources.HttpResponse(
        200, url, PAGE.encode(), {"content-type": "text/html; charset=utf-8"}))
    r = assimilate("https://garden.example.com/notes", max_notes=10)
    over = note(r.overview.permalink)
    assert over.title == "Garden notes" and r.kind == "web"
    text = "\n".join(p.read_text() for p in kdir(home).glob("*.md"))
    assert "Soil needs compost." in text and "water(daily)" in text
    assert "evil()" not in text and "Footer text" not in text and "Menu" not in text
    assert "https://example.org/compost" in over.content


def test_private_and_local_addresses_are_refused(monkeypatch):
    with pytest.raises(IngestError):
        sources.check_url("http://localhost:8000/")
    with pytest.raises(IngestError):
        sources.check_url("file:///etc/passwd")
    monkeypatch.setattr(sources.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 80))])
    with pytest.raises(IngestError, match="private"):
        sources.check_url("http://intranet.example.com/")


# ── documents ────────────────────────────────────────────────────────────────
def test_text_markdown_csv_json_readers(home):
    from myclaw.knowledge.ingest.documents import read_document

    md = home / "guide.md"
    md.write_text("---\ntags: x\n---\n# The Guide\n\nIntro.\n\n## One\n\nFirst part.\n\n## Two\n\nSecond.\n")
    d = read_document(md)
    assert d.title == "The Guide" and [s.heading for s in d.norm_sections()] == ["One", "Two"]
    csvf = home / "people.csv"
    csvf.write_text("name,age\nAda,36\nBob,40\n")
    d = read_document(csvf)
    sec = d.norm_sections()[0]
    assert "| name | age |" in sec.text and ("columns", "people: name, age") in sec.facts
    js = home / "data.json"
    js.write_text(json.dumps({"a": 1, "b": [1, 2]}))
    assert ("keys", "a, b") in read_document(js).facts
    with pytest.raises(IngestError, match="cannot be imported"):
        (home / "x.bin").write_bytes(b"\x00\x01")
        read_document(home / "x.bin")


def _tiny_pdf(text: str) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"]
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_pdf_reader(home):
    pytest.importorskip("pypdf")
    from myclaw.knowledge.ingest.documents import read_document

    p = home / "hello.pdf"
    p.write_bytes(_tiny_pdf("Hello from a PDF"))
    d = read_document(p)
    assert d.kind == "pdf" and "Hello from a PDF" in d.norm_sections()[0].text
    assert ("pages", "1") in d.facts


def test_docx_reader(home):
    docx = pytest.importorskip("docx")
    from myclaw.knowledge.ingest.documents import read_document

    d = docx.Document()
    d.add_heading("Plan", 0)
    d.add_paragraph("Opening words.")
    d.add_heading("Budget", 1)
    d.add_paragraph("Costs are low.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Item", "Cost", "Pen", "2"
    d.add_heading("Risks", 1)
    d.add_paragraph("None.")
    path = home / "plan.docx"
    d.save(str(path))
    doc = read_document(path)
    assert doc.title == "Plan" and [s.heading for s in doc.norm_sections()] == ["Budget", "Risks"]
    assert "| Item | Cost |" in doc.norm_sections()[0].text and "Opening words." in doc.summary


def test_xlsx_reader(home):
    openpyxl = pytest.importorskip("openpyxl")
    from myclaw.knowledge.ingest.documents import read_document

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Total"])
    ws.append(["North", 10])
    ws.append(["South", 20])
    path = home / "sales.xlsx"
    wb.save(str(path))
    doc = read_document(path)
    sec = doc.norm_sections()[0]
    assert sec.heading == "Sales" and "| North | 10 |" in sec.text
    assert ("columns", "Sales: Region, Total") in sec.facts and ("rows", "Sales: 2 rows") in sec.facts


def test_missing_optional_reader_says_how_to_install(home, monkeypatch):
    import builtins

    from myclaw.knowledge.ingest.documents import read_document
    from myclaw.knowledge.ingest.model import MissingDependency

    real = builtins.__import__

    def no_pypdf(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    p = home / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(MissingDependency) as e:
        read_document(p)
    assert "pip install 'pypdf" in str(e.value) and "zensynora[ingest]" in str(e.value)


def test_image_note_has_file_details(home):
    PIL = pytest.importorskip("PIL.Image")
    from myclaw.knowledge.ingest import assimilate

    img = home / "photo.png"
    PIL.new("RGB", (32, 16)).save(img)
    r = assimilate(str(img))
    facts = {(o.category, o.content) for o in note(r.overview.permalink).observations}
    assert ("dimensions", "32 × 16 px") in facts and ("kind", "image") in facts


# ── importers ────────────────────────────────────────────────────────────────
CHATGPT = [{
    "title": "Trip to Rome", "create_time": 1714560000.0, "update_time": 1714563600.0,
    "conversation_id": "c-1", "current_node": "n3", "default_model_slug": "gpt-4o",
    "mapping": {
        "n0": {"id": "n0", "message": None, "parent": None, "children": ["n1"]},
        "n1": {"id": "n1", "parent": "n0", "children": ["n2"], "message": {
            "author": {"role": "user"}, "content": {"content_type": "text", "parts": ["Plan 3 days in Rome"]}}},
        "n2": {"id": "n2", "parent": "n1", "children": ["n3"], "message": {
            "author": {"role": "system"}, "content": {"content_type": "text", "parts": ["hidden system"]}}},
        "n3": {"id": "n3", "parent": "n2", "children": [], "message": {
            "author": {"role": "assistant"}, "content": {"content_type": "text",
                                                         "parts": ["Day 1: Colosseum. - [tip] go early"]}}},
    }}, {
    "title": "Cooking", "create_time": 1717200000.0, "conversation_id": "c-2", "mapping": {
        "a": {"id": "a", "parent": None, "children": ["b"], "message": None},
        "b": {"id": "b", "parent": "a", "children": [], "message": {
            "author": {"role": "user"}, "content": {"content_type": "text", "parts": ["Pasta?"]}}}}}]

CLAUDE = [{"uuid": "u-1", "name": "Garden ideas", "created_at": "2025-03-01T10:00:00Z",
           "updated_at": "2025-03-01T11:00:00Z", "chat_messages": [
               {"sender": "human", "text": "What grows in shade?", "created_at": "2025-03-01T10:00:00Z"},
               {"sender": "assistant", "text": "", "content": [{"type": "text", "text": "Ferns and hostas."}],
                "created_at": "2025-03-01T10:00:05Z"}]}]
PROJECTS = [{"uuid": "p-1", "name": "Novel", "description": "My book", "prompt_template": "Be concise.",
             "created_at": "2025-01-01T00:00:00Z",
             "docs": [{"uuid": "d-1", "filename": "outline.md", "content": "Chapter 1. " * 40}]}]


def _zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data if isinstance(data, (str, bytes)) else json.dumps(data))
    return path


def test_chatgpt_export_json_and_zip(home):
    from myclaw.knowledge.ingest import import_file

    f = home / "conversations.json"
    f.write_text(json.dumps(CHATGPT))
    r = import_file(str(f))
    assert r.kind == "chatgpt" and r.extra["conversations"] == 2
    rome = next(n for n in r.notes if "Trip to Rome" in n.title)
    n = note(rome.permalink)
    assert "Plan 3 days in Rome" in n.content and "Colosseum" in n.content
    assert "hidden system" not in n.content
    assert not any(o.category == "tip" for o in n.observations)  # message text is not a fact
    assert {"imported", "chatgpt"} <= set(n.tags) and n.metadata["conversation_id"] == "c-1"
    assert ("model", "gpt-4o") in {(o.category, o.content) for o in n.observations}
    links = index_links()
    assert (rome.permalink, "part_of", "chatgpt-conversations-2024-05") in links
    assert ("chatgpt-conversations-2024-05", "part_of", "chatgpt-conversations") in links
    # the same export as a zip: nothing new
    z = _zip(home / "export.zip", {"conversations.json": CHATGPT, "chat.html": "<html/>"})
    again = import_file(str(z))
    assert again.kind == "chatgpt" and again.count("created") == 0
    assert len(list(kdir(home).glob("chatgpt-*.md"))) == 2 + 2 + 1  # conversations, months, root


def test_claude_export_conversations_and_projects(home):
    from myclaw.knowledge.ingest import import_file

    z = _zip(home / "claude.zip", {"data/conversations.json": CLAUDE, "data/projects.json": PROJECTS,
                                   "data/users.json": [{"uuid": "x"}]})
    r = import_file(str(z))
    assert r.kind == "claude"
    conv = next(n for n in r.notes if "Garden ideas" in n.title)
    assert "Ferns and hostas." in note(conv.permalink).content
    proj = next(n for n in r.notes if n.title == "Novel (Claude project)")
    pn = note(proj.permalink)
    assert "Be concise." in pn.content and "claude" in pn.tags
    outline = next(n for n in r.notes if n.title.endswith("outline.md"))
    assert (outline.permalink, "part_of", proj.permalink) in index_links()
    assert import_file(str(z)).count("created") == 0


def test_mcp_memory_jsonl_and_json(home):
    from myclaw.knowledge.ingest import import_file

    lines = [{"type": "entity", "name": "Ada Lovelace", "entityType": "person",
              "observations": ["Wrote the first program", "Born 1815"]},
             {"type": "entity", "name": "Analytical Engine", "entityType": "machine", "observations": []},
             {"type": "relation", "from": "Ada Lovelace", "to": "Analytical Engine", "relationType": "worked on"}]
    f = home / "memory.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    r = import_file(str(f))
    assert r.kind == "mcp_memory" and r.extra["entities"] == 2
    ada = next(n for n in r.notes if n.title == "Ada Lovelace")
    engine = next(n for n in r.notes if n.title == "Analytical Engine")
    n = note(ada.permalink)
    assert {("fact", "Wrote the first program"), ("fact", "Born 1815")} <= {(o.category, o.content) for o in n.observations}
    assert n.metadata["type"] == "person" and "person" in n.tags
    assert (ada.permalink, "worked_on", engine.permalink) in index_links()
    g = home / "memory.json"
    g.write_text(json.dumps({"entities": lines[:2], "relations": lines[2:]}))
    assert import_file(str(g), kind="mcp").count("created") == 0
    with pytest.raises(IngestError, match="No entities"):
        bad = home / "empty.json"
        bad.write_text(json.dumps({"entities": []}))
        import_file(str(bad), kind="mcp_memory")


def _vault(root: Path) -> Path:
    (root / "Projects").mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.json").write_text("{}")
    (root / "Home.md").write_text("---\ntags: [start]\ncustom: kept\n---\n\nSee [[Garden]].\n")
    (root / "Projects" / "Garden.md").write_text("# Garden\n\nBack to [[Home]].\n\n- links_to [[Home]]\n")
    (root / "Projects" / "photo.png").write_bytes(b"\x89PNG")
    return root


def test_markdown_folder_import_keeps_frontmatter_and_never_overwrites(home):
    from myclaw.knowledge.ingest import import_file
    from myclaw.knowledge.storage import write_note

    write_note("home", title="My own home note", content="Mine.")  # permalink 'home' is taken
    vault = _vault(home / "Vault")
    r = import_file(str(vault))
    assert r.kind == "markdown" and r.extra["copied"] == 2 and r.extra["permalinks_changed"] == 1
    dest = kdir(home) / "imported" / "vault"
    home_md = (dest / "Home.md").read_text()
    assert "custom: kept" in home_md and "tags: [start]" in home_md and "See [[Garden]]." in home_md
    assert "permalink: vault-home" in home_md
    assert (dest / "Projects" / "Garden.md").read_text() == (vault / "Projects" / "Garden.md").read_text()
    assert not (dest / ".obsidian").exists() and not (dest / "Projects" / "photo.png").exists()
    assert note("home").title == "My own home note"  # the user's note is untouched
    garden = note("garden")
    assert garden is not None and "Back to [[Home]]" in garden.content
    # again: nothing changes
    again = import_file(str(vault))
    assert again.extra["copied"] == 0 and again.extra["unchanged_files"] == 2
    # a changed note is saved beside the earlier copy, never over it
    (vault / "Projects" / "Garden.md").write_text("# Garden\n\nChanged.\n")
    third = import_file(str(vault))
    assert third.extra["kept_beside_existing"] == 1
    assert "Back to [[Home]]" in (dest / "Projects" / "Garden.md").read_text()
    assert "Changed." in (dest / "Projects" / "Garden-2.md").read_text()


def test_markdown_zip_import(home):
    from myclaw.knowledge.ingest import import_file

    z = _zip(home / "notes.zip", {"vault/a.md": "# A\n\nAlpha text.\n", "vault/sub/b.md": "Beta.",
                                  "vault/.obsidian/x.md": "hidden", "../evil.md": "escape",
                                  "vault/img.png": b"\x89PNG"})
    r = import_file(str(z), kind="markdown")
    dest = kdir(home) / "imported" / "notes"
    assert (dest / "a.md").exists() and (dest / "sub" / "b.md").exists()
    assert not (dest / ".obsidian").exists()
    assert not (kdir(home) / "imported" / "evil.md").exists() and not (kdir(home) / "evil.md").exists()
    assert r.extra["copied"] == 2


def test_detect_kind_and_unknown_kind(home):
    from myclaw.knowledge.ingest.importers import detect_kind, normalize_kind

    (home / "c.json").write_text(json.dumps(CHATGPT))
    (home / "k.json").write_text(json.dumps(CLAUDE))
    (home / "p.json").write_text(json.dumps(PROJECTS))
    (home / "other.json").write_text(json.dumps({"x": 1}))
    assert [detect_kind(home / n) for n in ("c.json", "k.json", "p.json", "other.json")] == \
        ["chatgpt", "claude", "claude", "document"]
    assert normalize_kind("Obsidian") == "markdown" and normalize_kind("mcp") == "mcp_memory"
    with pytest.raises(IngestError, match="Unknown import kind"):
        normalize_kind("word")


# ── log, tools, API, CLI ─────────────────────────────────────────────────────
def test_every_run_is_logged(home):
    from myclaw.knowledge.ingest import assimilate
    from myclaw.knowledge.ingest.log import log_path, read_recent

    f = home / "a.txt"
    f.write_text("Some text " * 50)
    assimilate(str(f))
    with pytest.raises(IngestError):
        assimilate(str(home / "missing.txt"))
    rows = read_recent()
    assert log_path() == home / ".myclaw" / "logs" / "knowledge_ingest.jsonl"
    assert rows[0]["created"] == 1 and rows[0]["kind"] == "text" and rows[0]["base"] == "default"
    assert rows[1]["errors"] and "does not exist" in rows[1]["errors"][0]
    assert {"time", "source", "updated", "duration"} <= set(rows[0])


def test_agent_tools_are_registered_and_answer_every_input(home):
    from myclaw.tools import TOOLS, refresh_tool_schemas, TOOL_SCHEMAS

    refresh_tool_schemas()
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "assimilate")
    assert schema["function"]["parameters"]["properties"]["max_notes"]["type"] == "integer"
    assert "user_id" not in schema["function"]["parameters"]["properties"]
    assimilate = TOOLS["assimilate"]["func"]
    import_knowledge = TOOLS["import_knowledge"]["func"]
    doc = home / "report.md"
    doc.write_text("# Report\n\n## Findings\n\n" + "Finding. " * 60 + "\n\n## Next steps\n\n" + "Step. " * 60)
    out = assimilate(str(doc))
    assert out.startswith("✅") and "memory://" in out and "```mermaid" in out
    assert "Could not assimilate" in assimilate("") and "Could not assimilate" in assimilate("~/nope.pdf")
    out = import_knowledge("~/nope.zip")
    assert out.startswith("Could not import") and "chatgpt" in out
    (home / "c.json").write_text(json.dumps(CHATGPT))
    assert "Unknown import kind" in import_knowledge("~/c.json", kind="word")
    out = import_knowledge("~/c.json")
    assert out.startswith("✅ Imported") and "conversations: 2" in out


def test_tools_use_the_knowledge_base_of_the_turn(home):
    from myclaw.knowledge.scope import KB_USER
    from myclaw.tools.ingest import assimilate

    (home / "n.txt").write_text("Text " * 80)
    token = KB_USER.set("alice")
    try:
        out = assimilate("~/n.txt", user_id="default")
    finally:
        KB_USER.reset(token)
    assert "'alice'" in out
    assert list((home / ".myclaw" / "knowledge" / "alice").glob("*.md"))


def test_tool_returns_partial_result_at_the_time_limit(home, monkeypatch):
    from myclaw.tools import ingest as tool_mod

    monkeypatch.setattr(tool_mod, "TOOL_SECONDS", 10.5)  # the budget gets 0.5 s
    folder = home / "many"
    folder.mkdir()
    for i in range(40):
        (folder / f"f{i:02}.md").write_text(f"# F{i}\n\n" + "Text. " * 100)
    real = notes_mod._write

    def slow(*a, **k):
        time.sleep(0.2)
        return real(*a, **k)

    monkeypatch.setattr(notes_mod, "_write", slow)
    out = tool_mod.assimilate(str(folder), max_notes=40)
    assert "Stopped early" in out and "Overview note" in out


@pytest.fixture
def web(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import knowledge_import_api

    app = FastAPI()
    app.include_router(knowledge_import_api.router)
    return TestClient(app)


def _wait(client, job_id, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        j = client.get(f"/api/knowledge/import/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_api_assimilate_and_upload_jobs(web, home):
    (home / "docs").mkdir()
    (home / "docs" / "a.md").write_text("# A\n\n## One\n\n" + "x " * 300 + "\n\n## Two\n\n" + "y " * 300)
    r = web.post("/api/knowledge/assimilate", json={"base": "default", "source": "~/docs/a.md"})
    assert r.status_code == 200
    j = _wait(web, r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["created"] == 3 and j["result"]["overview"]
    r = web.post("/api/knowledge/assimilate", json={"source": "/etc"})
    j = _wait(web, r.json()["job_id"])
    assert j["status"] == "error" and "outside the folders" in j["error"]
    # upload (raw body, as the Web UI sends it)
    r = web.post("/api/knowledge/import?base=default&kind=auto&filename=conversations.json",
                 content=json.dumps(CHATGPT).encode(), headers={"Content-Type": "application/octet-stream"})
    j = _wait(web, r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["kind"] == "chatgpt" and j["result"]["created"] == 5
    assert web.get("/api/knowledge/import/nope").status_code == 404
    assert web.post("/api/knowledge/assimilate", json={"source": "x", "base": "../x"}).status_code == 400
    r = web.post("/api/knowledge/import?kind=word&filename=a.json", content=b"{}")
    assert r.status_code == 400 and "Unknown import kind" in r.json()["detail"]


def test_api_upload_limit_and_temp_cleanup(web, home, monkeypatch):
    import tempfile

    from myclaw.web import knowledge_import_api as mod

    monkeypatch.setattr(tempfile, "tempdir", str(home / "tmp"))
    (home / "tmp").mkdir()
    monkeypatch.setattr(mod, "MAX_UPLOAD_BYTES", 100)
    r = web.post("/api/knowledge/import?filename=big.json", content=b"x" * 500)
    assert r.status_code == 413
    monkeypatch.setattr(mod, "MAX_UPLOAD_BYTES", 10_000_000)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("v/a.md", "# A\n\ntext")
    r = web.post("/api/knowledge/import?kind=markdown&filename=v.zip", content=z.getvalue())
    j = _wait(web, r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["copied"] == 1
    time.sleep(0.1)
    assert list((home / "tmp").iterdir()) == []  # the upload folder is removed


def test_cli_assimilate_and_import(home):
    from click.testing import CliRunner

    from myclaw.cli import cli

    f = home / "notes.txt"
    f.write_text("Plain text. " * 60)
    res = CliRunner().invoke(cli, ["knowledge", "assimilate", str(f), "--max-notes", "5"])
    assert res.exit_code == 0, res.output
    assert "✅ Assimilated" in res.output and "memory://" in res.output
    c = home / "c.json"
    c.write_text(json.dumps(CHATGPT))
    res = CliRunner().invoke(cli, ["knowledge", "import", str(c), "--kind", "chatgpt", "-b", "work"])
    assert res.exit_code == 0, res.output
    assert "(chatgpt)" in res.output and list((home / ".myclaw" / "knowledge" / "work").glob("chatgpt-*.md"))
    res = CliRunner().invoke(cli, ["knowledge", "import", str(home / "missing.zip")])
    assert res.exit_code != 0 and "does not exist" in res.output
