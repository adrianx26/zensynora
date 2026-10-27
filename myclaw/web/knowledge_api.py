"""Knowledge base explorer for the Web UI: graph, notes, tags, search, editing.

Routes
------
GET    /api/knowledge/bases                 knowledge bases on this computer (folders) + counts
GET    /api/knowledge/graph?base=           every note as a node, relations as edges, plus stats,
                                            tags, observation categories and relation types
GET    /api/knowledge/note/{permalink}      one note: markdown, observations, links out, backlinks
POST   /api/knowledge/note                  create a note ({name, title?, tags?, content?})
PUT    /api/knowledge/note/{permalink}      replace a note's markdown ({content})
DELETE /api/knowledge/note/{permalink}      move a note to the base's .trash folder (undo: move it back)
GET    /api/knowledge/search?q=&base=       full-text search
POST   /api/knowledge/sync?base=            re-index the markdown files into the database

The markdown files are the source of truth, so the graph is read from them;
a relation to a note that does not exist (yet) is returned as a "missing"
node. The notes are private, so every route uses the settings guard (this
computer, or the admin API key).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myclaw.web.settings_api import guard

logger = logging.getLogger(__name__)
router = APIRouter()

BASE_RE = re.compile(r"^[\w.@-]{1,64}$")
MAX_NODES = 2000


def _root() -> Path:
    # 2026-10-23 (KB phase 0): honour knowledge.knowledge_dir (set_knowledge_root).
    from myclaw.knowledge.storage import knowledge_root

    return knowledge_root()


def _base(base: str | None) -> str:
    base = (base or "default").strip() or "default"
    if not BASE_RE.match(base) or base.startswith("."):
        raise HTTPException(status_code=400, detail="Unknown knowledge base.")
    return base


def _dir(base: str) -> Path:
    from myclaw.knowledge.storage import get_knowledge_dir

    return get_knowledge_dir(base)


def _note_files(base: str) -> list[Path]:
    return sorted(p for p in _dir(base).glob("*.md") if p.is_file())


def _parse_all(base: str) -> list[Any]:
    from myclaw.knowledge.parser import parse_note

    notes = []
    for f in _note_files(base)[:MAX_NODES]:
        try:
            notes.append(parse_note(f))
        except Exception as e:  # a broken file must not hide the others
            logger.warning("Knowledge: cannot read %s: %s", f.name, e)
    return notes


def _slug(text: str) -> str:
    from myclaw.knowledge.storage import validate_permalink

    try:
        return validate_permalink(text)
    except ValueError:
        return ""


def _aliases(n: Any) -> list[str]:
    return [str(a).strip() for a in (getattr(n, "aliases", None) or []) if str(a).strip()]


class _Resolver:
    """Find the note a relation target ([[...]]) points to.

    2026-10-23 (KB phase 0): same order as ``KnowledgeDB.resolve_target`` —
    permalink, slug, name, title, alias (case-insensitive, no fuzzy matching) —
    so a link resolves in the graph exactly as in the database. Within one
    step the first note (by file name) wins.
    """

    def __init__(self, notes: list[Any]):
        # One lookup table per step, checked in order.
        self.tiers: list[dict[str, str]] = [{} for _ in range(6)]
        perm, slug, name, title, alias, extra = self.tiers
        for n in notes:
            pl = n.permalink
            self._add(perm, (pl or "").strip().lower(), pl)
            self._add(perm, (n.file_path.stem.lower() if n.file_path else ""), pl)
            self._add(slug, _slug(pl or ""), pl)
            self._add(name, (n.name or "").strip().lower(), pl)
            self._add(title, (n.title or "").strip().lower(), pl)
            for a in _aliases(n):
                self._add(alias, a.lower(), pl)
            # Before 0.7.5 phase 0 a target was also matched by the slug of a
            # name/title; kept as the last step so existing links still resolve.
            for k in (_slug(n.name or ""), _slug(n.title or ""), *(_slug(a) for a in _aliases(n))):
                self._add(extra, k, pl)

    @staticmethod
    def _add(table: dict[str, str], key: str, permalink: str) -> None:
        if key and key not in table:
            table[key] = permalink

    def __call__(self, target: str) -> tuple[str, bool]:
        t = (target or "").strip()
        low, sl = t.lower(), _slug(t)
        perm, slug, name, title, alias, extra = self.tiers
        for table, key in ((perm, low), (slug, sl), (name, low), (title, low), (alias, low),
                           (extra, sl)):
            if key and key in table:
                return table[key], True
        return "missing:" + (sl or low), False


def _iso(dt: Any) -> str | None:
    try:
        return dt.isoformat() if dt else None
    except Exception:
        return None


def _note_tags(n: Any) -> list[str]:
    return [str(t) for t in (n.tags or []) if str(t).strip()]


def _body(content: str) -> str:
    """The note text without frontmatter, observations and relations (for previews)."""
    from myclaw.knowledge.parser import FRONTMATTER_PATTERN, OBSERVATION_PATTERN, RELATION_PATTERN

    text = FRONTMATTER_PATTERN.sub("", content or "", count=1)
    text = OBSERVATION_PATTERN.sub("", text)
    text = RELATION_PATTERN.sub("", text)
    text = re.sub(r"^#+ .*$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{2,}", "\n", text).strip()


def build_graph(base: str) -> dict[str, Any]:
    notes = _parse_all(base)
    resolve = _Resolver(notes)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    tags: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    rel_types: Counter[str] = Counter()
    obs_total = 0
    for n in notes:
        ntags = _note_tags(n)
        tags.update(ntags)
        for o in n.observations:
            categories[o.category] += 1
            tags.update(o.tags or [])
        obs_total += len(n.observations)
        stat = n.file_path.stat() if n.file_path else None
        nodes[n.permalink] = {
            "id": n.permalink, "title": n.title or n.name or n.permalink, "tags": ntags,
            "observations": len(n.observations), "relations": len(n.relations),
            "updated": _iso(n.updated_at) or (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)) if stat else None),
            "size": stat.st_size if stat else 0, "missing": False, "degree": 0,
            "preview": _body(n.content)[:240],
        }
    seen: set[tuple[str, str, str]] = set()
    for n in notes:
        for r in n.relations:
            target, found = resolve(r.target)
            if not found and target not in nodes:
                nodes[target] = {"id": target, "title": r.target, "tags": [], "observations": 0, "relations": 0,
                                 "updated": None, "size": 0, "missing": True, "degree": 0, "preview": ""}
            key = (n.permalink, target, r.relation_type)
            if key in seen or target == n.permalink:
                continue
            seen.add(key)
            rel_types[r.relation_type] += 1
            edges.append({"source": n.permalink, "target": target, "type": r.relation_type})
            nodes[n.permalink]["degree"] += 1
            nodes[target]["degree"] += 1
    return {
        "base": base,
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {"notes": len(notes), "observations": obs_total, "relations": len(edges),
                  "missing": sum(1 for x in nodes.values() if x["missing"]),
                  "orphans": sum(1 for x in nodes.values() if not x["missing"] and x["degree"] == 0),
                  "tags": len(tags)},
        "tags": [{"name": k, "count": v} for k, v in tags.most_common()],
        "categories": [{"name": k, "count": v} for k, v in categories.most_common()],
        "relation_types": [{"name": k, "count": v} for k, v in rel_types.most_common()],
        "truncated": len(_note_files(base)) > MAX_NODES,
    }


@router.get("/api/knowledge/bases", dependencies=guard)
async def list_bases():
    root = _root()
    out = []
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and BASE_RE.match(d.name):
                out.append({"name": d.name, "notes": sum(1 for _ in d.glob("*.md"))})
    if not any(b["name"] == "default" for b in out):
        out.insert(0, {"name": "default", "notes": 0})
    out.sort(key=lambda b: (b["name"] != "default", b["name"]))
    return {"bases": out}


@router.get("/api/knowledge/graph", dependencies=guard)
async def graph(base: str | None = None):
    return await asyncio.to_thread(build_graph, _base(base))


def _note_file(base: str, permalink: str) -> Path:
    slug = _slug(permalink)
    if not slug or slug != permalink.lower():
        raise HTTPException(status_code=400, detail="Invalid note name.")
    d = _dir(base)
    for cand in (d / f"{permalink}.md", d / f"{slug}.md"):
        if cand.is_file():
            return cand
    # The file name can differ from the permalink in the frontmatter.
    from myclaw.knowledge.parser import parse_note

    for f in d.glob("*.md"):
        try:
            if parse_note(f).permalink == permalink:
                return f
        except Exception:
            continue
    raise HTTPException(status_code=404, detail="Note not found.")


def _jsonable(value: Any) -> Any:
    """Frontmatter values (dates…) as JSON-safe data."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return {}


def _note_detail(base: str, permalink: str) -> dict[str, Any]:
    from myclaw.knowledge.parser import parse_note

    f = _note_file(base, permalink)
    n = parse_note(f)
    notes = _parse_all(base)
    resolve = _Resolver(notes)
    links = []
    for r in n.relations:
        target, found = resolve(r.target)
        links.append({"type": r.relation_type, "target": target, "label": r.target, "exists": found})
    backlinks = []
    for other in notes:
        if other.permalink == n.permalink:
            continue
        for r in other.relations:
            if resolve(r.target)[0] == n.permalink:
                backlinks.append({"type": r.relation_type, "source": other.permalink,
                                  "label": other.title or other.permalink})
    st = f.stat()
    return {
        "permalink": n.permalink, "title": n.title, "name": n.name, "tags": _note_tags(n),
        "content": n.content, "body": _body(n.content),
        "observations": [{"category": o.category, "content": o.content, "tags": o.tags or []} for o in n.observations],
        "links": links, "backlinks": backlinks,
        "created": _iso(n.created_at), "updated": _iso(n.updated_at),
        # 2026-10-23 (KB phase 0): additive — frontmatter aliases and other keys.
        "aliases": _aliases(n), "metadata": _jsonable(getattr(n, "metadata", None) or {}),
        "file": str(f), "size": st.st_size,
        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
    }


@router.get("/api/knowledge/note/{permalink}", dependencies=guard)
async def get_note(permalink: str, base: str | None = None):
    return await asyncio.to_thread(_note_detail, _base(base), permalink)


def _resync(base: str, f: Path) -> None:
    from myclaw.knowledge.db import KnowledgeDB
    from myclaw.knowledge.parser import parse_note
    from myclaw.knowledge.storage import get_file_metadata

    meta = get_file_metadata(f)
    with KnowledgeDB(base) as db:
        db.sync_entity_from_note(parse_note(f), checksum=meta["checksum"], mtime=meta["mtime"], size=meta["size"])


class NewNote(BaseModel):
    name: str
    title: str = ""
    tags: list[str] = []
    content: str = ""


@router.post("/api/knowledge/note", dependencies=guard)
async def create_note(req: NewNote, base: str | None = None):
    from myclaw.knowledge.storage import write_note

    b = _base(base)
    slug = _slug(req.name)
    if not slug:
        raise HTTPException(status_code=400, detail="Give the note a name (letters or digits).")
    if (_dir(b) / f"{slug}.md").exists():
        raise HTTPException(status_code=409, detail=f"A note called '{slug}' already exists.")
    tags = [t.strip().lstrip("#") for t in req.tags if t.strip()]
    content = req.content.strip()
    if content and not content.startswith("---"):
        title = req.title.strip() or req.name.strip()
        tag_line = f"tags: [{', '.join(json.dumps(t) for t in tags)}]\n" if tags else ""
        content = f"---\ntitle: {json.dumps(title)}\npermalink: {slug}\n{tag_line}---\n\n# {title}\n\n{content}\n"
    permalink = await asyncio.to_thread(write_note, slug, req.title.strip() or req.name.strip(),
                                        None, None, tags, b, content or None)
    return await asyncio.to_thread(_note_detail, b, permalink)


class NoteContent(BaseModel):
    content: str


@router.put("/api/knowledge/note/{permalink}", dependencies=guard)
async def save_note(permalink: str, req: NoteContent, base: str | None = None):
    b = _base(base)
    f = _note_file(b, permalink)
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="The note is empty — use Delete to remove it.")
    if len(req.content) > 1_000_000:
        raise HTTPException(status_code=413, detail="The note is too large (1 MB max).")
    f.write_text(req.content, encoding="utf-8")
    await asyncio.to_thread(_resync, b, f)
    return await asyncio.to_thread(_note_detail, b, permalink)


@router.delete("/api/knowledge/note/{permalink}", dependencies=guard)
async def remove_note(permalink: str, base: str | None = None):
    """Move the note into <base>/.trash (nothing is deleted for good)."""
    from myclaw.knowledge.db import KnowledgeDB

    b = _base(base)
    f = _note_file(b, permalink)
    trash = f.parent / ".trash"
    trash.mkdir(exist_ok=True)
    dest = trash / f"{f.stem}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    shutil.move(str(f), dest)
    with KnowledgeDB(b) as db:
        db.delete_entity(permalink)
    return {"ok": True, "moved_to": str(dest)}


@router.get("/api/knowledge/search", dependencies=guard)
async def search(q: str, base: str | None = None, limit: int = 30):
    """Full-text search; falls back to matching titles and text when the index finds nothing.

    2026-10-23 (KB phase 0): AND / OR / NOT and "quoted phrases" work (boolean=True).
    """
    from myclaw.knowledge.storage import search_notes

    b = _base(base)
    q = (q or "").strip()
    if not q:
        return {"results": []}
    hits: list[str] = []
    try:
        hits = [n.permalink for n in await asyncio.to_thread(
            search_notes, q, b, max(1, min(limit, 100)), None, True)]
    except Exception as e:
        logger.debug("FTS search failed: %s", e)
    if not hits:
        ql = q.lower()
        for n in await asyncio.to_thread(_parse_all, b):
            if ql in (n.title or "").lower() or ql in n.permalink or ql in (n.content or "").lower():
                hits.append(n.permalink)
    return {"results": hits[:limit]}


class EnrichRequest(BaseModel):
    limit: int = 10


@router.post("/api/knowledge/enrich", dependencies=guard)
async def enrich(req: EnrichRequest, base: str | None = None):
    """Add tags, facts and links to notes that have none (default model; text unchanged).

    Works in small batches (rate limits of free models); stops at the first
    model error. Returns what was done and how many notes are still plain.
    """
    from myclaw.config import load_config
    from myclaw.knowledge.extract import enrich_note, needs_structure
    from myclaw.llm_errors import describe_llm_error

    b = _base(base)
    notes = [n for n in await asyncio.to_thread(_parse_all, b) if needs_structure(n)]
    limit = max(1, min(int(req.limit or 10), 25))
    cfg = load_config()
    done, failed, stopped = [], [], ""
    for n in notes[:limit]:
        try:
            r = await enrich_note(cfg, b, n.permalink)
        except Exception as e:
            stopped = describe_llm_error(e)
            break
        (done if r.get("ok") else failed).append(r)
    left = len(notes) - len(done)
    return {"enriched": done, "failed": failed, "remaining": left, "stopped": stopped,
            "facts": sum(r.get("facts", 0) for r in done), "links": sum(r.get("links", 0) for r in done)}


@router.post("/api/knowledge/sync", dependencies=guard)
async def sync(base: str | None = None):
    from myclaw.knowledge.sync import sync_knowledge

    b = _base(base)
    try:
        stats = await asyncio.to_thread(sync_knowledge, b, True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)[:300]}")
    return {"ok": True, "stats": stats}
