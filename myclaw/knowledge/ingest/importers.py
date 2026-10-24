"""Importers: chat exports, MCP memory files, Markdown folders (KB phase 5, 2026-10-24).

* **ChatGPT** export (``conversations.json``, also inside the export ``.zip``)
* **Claude.ai** export (``conversations.json`` / ``projects.json``, zip)
* **MCP memory** server file (``memory.json`` / JSONL: entities with
  observations → notes with facts; relations → links)
* **Markdown folder / Obsidian vault** (folder or zip): the files are copied
  into ``<base>/imported/<folder-name>/`` keeping their frontmatter and text,
  never over an existing file; the sync indexes them.

Conversations become one note each (title, date, a short transcript), grouped
by month under one overview note. Names are stable (from the export's ids), so
importing the same export again updates the notes instead of duplicating them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import Budget, Document, IngestError, IngestResult, NoteRef, Section
from .notes import _Plan, _write, clean_body, clean_fact, short_hash, slug, write_document_notes
from .safety import is_secret_file, redact, safe_relpath

logger = logging.getLogger(__name__)

KINDS = ("auto", "chatgpt", "claude", "mcp_memory", "markdown", "document")
_KIND_ALIASES = {"mcp": "mcp_memory", "memory": "mcp_memory", "mcp-memory": "mcp_memory",
                 "obsidian": "markdown", "md": "markdown", "markdown_folder": "markdown",
                 "claude_ai": "claude", "claude.ai": "claude", "openai": "chatgpt", "doc": "document",
                 "file": "document"}
MAX_JSON_BYTES = 600_000_000      # a conversations.json larger than this is refused
MAX_ZIP_MEMBER = 25_000_000       # one Markdown file in a zip
MAX_ZIP_TOTAL = 800_000_000       # all files read from one zip
TRANSCRIPT_CHARS = 8_000          # transcript kept per conversation
MESSAGE_CHARS = 1_500             # text kept per message
MAX_IMPORT_NOTES = 2_000          # default note limit of one import


def normalize_kind(kind: str) -> str:
    k = (kind or "auto").strip().lower().replace(" ", "_")
    k = _KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise IngestError(f"Unknown import kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    return k


# ── reading exports ──────────────────────────────────────────────────────────
def _zip_names(zf: zipfile.ZipFile) -> List[str]:
    return [i.filename for i in zf.infolist() if not i.is_dir()]


def _find_member(zf: zipfile.ZipFile, name: str) -> Optional[zipfile.ZipInfo]:
    """The shallowest member whose file name is ``name`` (case-insensitive)."""
    hits = [i for i in zf.infolist() if not i.is_dir()
            and i.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == name.lower()
            and "__MACOSX" not in i.filename]
    return min(hits, key=lambda i: i.filename.count("/")) if hits else None


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise IngestError(f"'{info.filename}' in the zip is {info.file_size // 1_000_000} MB; "
                          f"the limit is {limit // 1_000_000} MB.")
    with zf.open(info) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise IngestError(f"'{info.filename}' in the zip is larger than its declared size.")
    return data


def _peek(path: Path, n: int = 262_144) -> str:
    with open(path, "rb") as f:
        return f.read(n).decode("utf-8", errors="replace")


def _peek_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, n: int = 262_144) -> str:
    with zf.open(info) as f:
        return f.read(n).decode("utf-8", errors="replace")


def _json_kind(head: str) -> str:
    if '"mapping"' in head and ('"create_time"' in head or '"title"' in head):
        return "chatgpt"
    if '"chat_messages"' in head:
        return "claude"
    if '"prompt_template"' in head or ('"docs"' in head and '"uuid"' in head):
        return "claude"
    if '"entityType"' in head or re.search(r'"type"\s*:\s*"entity"', head) or '"entities"' in head:
        return "mcp_memory"
    return ""


def detect_kind(path: Path) -> str:
    """What an export file / folder is (one of :data:`KINDS` without 'auto')."""
    if path.is_dir():
        if any(p.suffix.lower() in (".md", ".markdown") for p in _iter_md(path, limit=1)):
            return "markdown"
        raise IngestError(f"'{path.name}' has no Markdown files. To read a folder of code or "
                          "documents use assimilate instead.")
    ext = path.suffix.lower()
    if ext == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                conv = _find_member(zf, "conversations.json")
                if conv is not None:
                    k = _json_kind(_peek_member(zf, conv))
                    if k:
                        return k
                if _find_member(zf, "projects.json") is not None:
                    return "claude"
                for name in ("memory.json", "memory.jsonl"):
                    if _find_member(zf, name) is not None:
                        return "mcp_memory"
                if any(n.lower().endswith((".md", ".markdown")) for n in _zip_names(zf)):
                    return "markdown"
        except zipfile.BadZipFile as e:
            raise IngestError(f"'{path.name}' is not a valid zip file.") from e
        raise IngestError(f"'{path.name}' is not a ChatGPT or Claude export, an MCP memory file "
                          "or a zip of Markdown notes.")
    if ext in (".json", ".jsonl"):
        k = _json_kind(_peek(path))
        if k:
            return k
        if ext == ".jsonl":
            head = _peek(path, 65_536)
            if '"name"' in head and '"observations"' in head:
                return "mcp_memory"
    return "document"


def _load_json(path: Path, member: str) -> Any:
    """JSON from a file, or from the member ``member`` of a zip."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            info = _find_member(zf, member)
            if info is None:
                return None
            data = _read_member(zf, info, MAX_JSON_BYTES)
        return _parse_json(data.decode("utf-8", errors="replace"), member)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise IngestError(f"'{path.name}' is larger than {MAX_JSON_BYTES // 1_000_000} MB.")
    return _parse_json(path.read_text(encoding="utf-8", errors="replace"), path.name)


def _parse_json(text: str, name: str) -> Any:
    text = text.lstrip("﻿")
    try:
        return json.loads(text)
    except ValueError:
        # JSON Lines (MCP memory)
        rows = []
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise IngestError(f"'{name}' is not valid JSON (line {i}: {e}).") from e
        return rows


# ── conversations ────────────────────────────────────────────────────────────
def _ts(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, OSError, OverflowError):
        return None


def _chatgpt_messages(conv: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(role, text) of the conversation's visible branch, in order."""
    mapping = conv.get("mapping") or {}
    if not isinstance(mapping, dict):
        return []
    chain: List[Dict[str, Any]] = []
    node_id = conv.get("current_node")
    seen = set()
    if node_id in mapping:
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            chain.append(mapping[node_id])
            node_id = mapping[node_id].get("parent")
        chain.reverse()
    else:  # no current node: follow the first child from the root
        root = next((k for k, v in mapping.items() if not v.get("parent")), None)
        node_id = root
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            chain.append(mapping[node_id])
            kids = mapping[node_id].get("children") or []
            node_id = kids[0] if kids else None
    out: List[Tuple[str, str]] = []
    for node in chain:
        msg = node.get("message") or {}
        if not msg:
            continue
        meta = msg.get("metadata") or {}
        if meta.get("is_visually_hidden_from_conversation"):
            continue
        role = ((msg.get("author") or {}).get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content") or {}
        ctype = content.get("content_type")
        text = ""
        if ctype in ("text", "multimodal_text"):
            bits = []
            for p in content.get("parts") or []:
                if isinstance(p, str):
                    bits.append(p)
                elif isinstance(p, dict):
                    bits.append("[image]" if "image" in str(p.get("content_type", "")) else "[attachment]")
            text = "\n".join(b for b in bits if b)
        elif ctype == "code":
            text = f"```{content.get('language') or ''}\n{content.get('text') or ''}\n```"
        if text.strip():
            out.append((role, text.strip()))
    return out


def _claude_messages(conv: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for m in conv.get("chat_messages") or []:
        sender = (m.get("sender") or "").lower()
        role = "user" if sender in ("human", "user") else "assistant"
        text = m.get("text") or ""
        if not text and m.get("content"):
            text = "\n".join(str(c.get("text")) for c in m["content"]
                             if isinstance(c, dict) and c.get("text"))
        for a in m.get("attachments") or []:
            if isinstance(a, dict) and a.get("file_name"):
                text += f"\n[attachment: {a['file_name']}]"
        if text.strip():
            out.append((role, text.strip()))
    return out


def _transcript(messages: List[Tuple[str, str]], assistant: str) -> str:
    lines: List[str] = []
    used = 0
    for i, (role, text) in enumerate(messages):
        if len(text) > MESSAGE_CHARS:
            text = text[:MESSAGE_CHARS].rstrip() + " …"
        who = "You" if role == "user" else assistant
        block = f"**{who}:** {text}"
        if used + len(block) > TRANSCRIPT_CHARS:
            lines.append(f"*(transcript shortened: {len(messages) - i} more messages)*")
            break
        lines.append(block)
        used += len(block)
    return "\n\n".join(lines)


def import_conversations(convs: List[Dict[str, Any]], source_name: str, base: str,
                         budget: Budget, result: IngestResult, group_by_month: bool = True) -> None:
    """ChatGPT (``source_name='chatgpt'``) or Claude conversations → notes."""
    label = "ChatGPT" if source_name == "chatgpt" else "Claude"
    getter = _chatgpt_messages if source_name == "chatgpt" else _claude_messages
    items: List[Tuple[datetime, Dict[str, Any]]] = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        when = _ts(c.get("create_time") if source_name == "chatgpt" else c.get("created_at"))
        items.append((when or datetime(1970, 1, 1, tzinfo=timezone.utc), c))
    items.sort(key=lambda x: x[0], reverse=True)  # newest first when a limit cuts the import
    root_perm = f"{source_name}-conversations"
    months: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    room = budget.max_notes - budget.notes_written
    groups_needed = 1 + len({w.strftime("%Y-%m") for w, _ in items}) if group_by_month else 0
    limit = max(0, room - groups_needed)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    written = 0
    for when, c in items:
        if written >= limit:
            result.skipped.append(f"{len(items) - written} older conversations not imported "
                                  f"(limit of {budget.max_notes} notes)")
            result.stopped = result.stopped or "note limit"
            break
        if budget.out_of_time(margin=5.0):
            result.stopped = budget.stop_reason() or "time limit reached"
            result.skipped.append(f"{len(items) - written} conversations not imported "
                                  f"({result.stopped}); import again to continue")
            break
        cid = str(c.get("conversation_id") or c.get("id") or c.get("uuid") or "")
        title = re.sub(r"\s+", " ", str(c.get("title") or c.get("name") or "")).strip() or "Untitled"
        date = when.strftime("%Y-%m-%d") if when.year > 1970 else ""
        key = cid or f"{title}|{c.get('create_time') or c.get('created_at')}"
        perm = slug(f"{source_name}-{date.replace('-', '')}-{slug(title, 40)}-{short_hash(key, 8)}", 110)
        msgs = getter(c)
        month = when.strftime("%Y-%m") if when.year > 1970 else "undated"
        month_perm = f"{source_name}-conversations-{month}"
        note_title = f"{title[:100]} ({label}, {date})" if date else f"{title[:100]} ({label})"
        body = (f"*Imported {label} conversation" + (f" of {date}" if date else "")
                + f" · {len(msgs)} messages*\n\n" + clean_body(_transcript(msgs, label)))
        facts = [("date", date)] if date else []
        facts.append(("messages", str(len(msgs))))
        model = c.get("default_model_slug") or c.get("model")
        if model:
            facts.append(("model", clean_fact(str(model))))
        links = [("part_of", month_perm)] if group_by_month else []
        plan = _Plan(perm, note_title, body, ["imported", source_name], facts, links)
        meta = {"source": f"{source_name}-export", "conversation_id": cid, "ingest_kind": source_name,
                "ingested": stamp}
        try:
            action = _write(plan, base, meta)
        except Exception as e:
            logger.warning("Import: could not write conversation %s: %s", perm, e)
            result.errors.append(f"could not write '{title}': {e}")
            continue
        written += 1
        budget.notes_written += 1
        result.notes.append(NoteRef(perm, note_title, action))
        months[month].append((perm, note_title))
        if written % 25 == 0:
            budget.report(f"{written}/{len(items)} conversations")
    result.extra["conversations"] = written
    if group_by_month and months:
        for month, convs_in in sorted(months.items()):
            perm = f"{source_name}-conversations-{month}"
            title = f"{label} conversations {month}"
            body = (f"*{len(convs_in)} {label} conversations from {month}, imported from an "
                    "export.*\n\n" + "\n".join(f"{i}. [[{p}]] — {t}" for i, (p, t) in
                                                enumerate(sorted(convs_in, key=lambda x: x[1]), 1)))
            _write_group(_Plan(perm, title, body, ["imported", source_name],
                               [("conversations", str(len(convs_in)))], [("part_of", root_perm)]),
                         base, stamp, source_name, budget, result)
        body = (f"*Conversations imported from a {label} export.*\n\n"
                + "\n".join(f"* [[{source_name}-conversations-{m}]] — {len(v)} conversations"
                            for m, v in sorted(months.items(), reverse=True)))
        _write_group(_Plan(root_perm, f"{label} conversations", body, ["imported", source_name],
                           [], []), base, stamp, source_name, budget, result, root=True)


def _write_group(plan: _Plan, base: str, stamp: str, source_name: str, budget: Budget,
                 result: IngestResult, root: bool = False) -> None:
    from myclaw.knowledge.storage import read_note

    old = read_note(plan.permalink, base)
    if old is not None:
        # keep the entries of earlier imports (another export of other months)
        old_items = re.findall(r"^(?:\d+\.|\*) \[\[([^\]]+)\]\].*$", old.content, re.M)
        new_items = set(re.findall(r"\[\[([^\]]+)\]\]", plan.body))
        missing = [line for line in old.content.splitlines()
                   if re.match(r"^(?:\d+\.|\*) \[\[([^\]]+)\]\]", line)
                   and re.findall(r"\[\[([^\]]+)\]\]", line)[0] not in new_items]
        if old_items and missing:
            plan.body += "\n" + "\n".join(missing)
    try:
        action = _write(plan, base, {"source": f"{source_name}-export", "ingest_kind": source_name,
                                     "ingested": stamp})
    except Exception as e:
        result.errors.append(f"could not write '{plan.title}': {e}")
        return
    budget.notes_written += 1
    ref = NoteRef(plan.permalink, plan.title, action)
    result.notes.insert(0, ref) if root else result.notes.append(ref)
    if root:
        result.overview = ref


def import_claude_projects(projects: List[Dict[str, Any]], base: str, budget: Budget,
                           result: IngestResult) -> None:
    """Claude.ai projects: one overview per project, one note per project document."""
    n = 0
    for p in projects:
        if not isinstance(p, dict):
            continue
        if budget.notes_written >= budget.max_notes or budget.out_of_time(margin=5.0):
            result.stopped = result.stopped or "note or time limit"
            result.skipped.append(f"{len(projects) - n} projects not imported ({result.stopped})")
            break
        name = str(p.get("name") or "Untitled project").strip()
        pid = str(p.get("uuid") or name)
        summary = "\n\n".join(x for x in (str(p.get("description") or "").strip(),
                                          ("**Project instructions:**\n\n" + str(p["prompt_template"]).strip())
                                          if p.get("prompt_template") else "") if x)
        sections = [Section(heading=str(d.get("filename") or "document"),
                            text=str(d.get("content") or ""),
                            key=str(d.get("uuid") or d.get("filename") or i))
                    for i, d in enumerate(p.get("docs") or []) if isinstance(d, dict)]
        doc = Document(title=f"{name} (Claude project)", source=f"claude-project:{pid}",
                       kind="claude-project", summary=summary or f"Claude project {name}.",
                       sections=list(sections), tags=["imported", "claude"],
                       date=(_ts(p.get("created_at")) or datetime(1970, 1, 1)).strftime("%Y-%m-%d")
                       if p.get("created_at") else "",
                       permalink_hint=f"claude-project-{name}", metadata={"project_id": pid},
                       split_all=True)
        sub = write_document_notes(doc, base, max_notes=budget.max_notes - budget.notes_written,
                                   budget=budget)
        result.notes.extend(sub.notes)
        result.errors.extend(sub.errors)
        result.overview = result.overview or sub.overview
        n += 1
    result.extra["projects"] = n


# ── MCP memory ───────────────────────────────────────────────────────────────
def _mcp_records(data: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    entities: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        entities = [e for e in data.get("entities") or [] if isinstance(e, dict)]
        relations = [r for r in data.get("relations") or [] if isinstance(r, dict)]
    elif isinstance(data, list):
        for rec in data:
            if not isinstance(rec, dict):
                continue
            t = rec.get("type")
            if t == "relation" or ("from" in rec and "to" in rec):
                relations.append(rec)
            elif t == "entity" or "observations" in rec or "entityType" in rec:
                entities.append(rec)
    return entities, relations


def import_mcp_memory(data: Any, base: str, budget: Budget, result: IngestResult) -> None:
    """Entities → notes (observations → facts), relations → links."""
    entities, relations = _mcp_records(data)
    if not entities:
        raise IngestError("No entities found. An MCP memory file has lines like "
                          '{"type": "entity", "name": …, "entityType": …, "observations": […]}.')
    names: Dict[str, str] = {}
    for e in entities:
        name = str(e.get("name") or e.get("entityName") or e.get("id") or "").strip()
        if name:
            names[name] = slug(f"mcp-{slug(name, 60)}-{short_hash(name, 4)}", 100)
    by_source: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for r in relations:
        src = str(r.get("from") or r.get("from_id") or "").strip()
        dst = str(r.get("to") or r.get("to_id") or "").strip()
        rel = slug(str(r.get("relationType") or r.get("relation_type") or "relates_to"), 40)
        rel = rel.replace("-", "_") or "relates_to"
        if src and dst:
            by_source[src].append((rel, names.get(dst, dst)))
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    done = 0
    for e in entities:
        name = str(e.get("name") or e.get("entityName") or e.get("id") or "").strip()
        if not name:
            result.skipped.append("an entity without a name")
            continue
        if budget.notes_written >= budget.max_notes or budget.out_of_time(margin=5.0):
            result.stopped = result.stopped or ("note limit" if budget.notes_written >= budget.max_notes
                                                else "time limit reached")
            result.skipped.append(f"{len(entities) - done} entities not imported ({result.stopped})")
            break
        etype = str(e.get("entityType") or e.get("entity_type") or "entity").strip()
        obs = [clean_fact(o, 500) for o in (e.get("observations") or []) if str(o).strip()]
        perm = names[name]
        body = f"*Imported from an MCP memory file · type: {clean_fact(etype, 60)}*"
        plan = _Plan(perm, name[:150], body, ["imported", "mcp-memory", slug(etype, 30) or "entity"],
                     [("fact", o) for o in obs if o], list(dict.fromkeys(by_source.get(name, []))))
        try:
            action = _write(plan, base, {"type": etype, "source": "mcp-memory",
                                         "ingest_kind": "mcp_memory", "ingested": stamp})
        except Exception as ex:
            result.errors.append(f"could not write '{name}': {ex}")
            continue
        done += 1
        budget.notes_written += 1
        result.notes.append(NoteRef(perm, name, action))
    result.extra.update({"entities": done, "relations": sum(len(v) for v in by_source.values())})


# ── Markdown folder / Obsidian vault ─────────────────────────────────────────
def _iter_md(folder: Path, limit: Optional[int] = None) -> Iterable[Path]:
    n = 0
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__MACOSX")
        for f in sorted(filenames):
            if f.lower().endswith((".md", ".markdown")) and not f.startswith("."):
                yield Path(dirpath) / f
                n += 1
                if limit and n >= limit:
                    return


def _set_permalink(text: str, permalink: str) -> str:
    """The note text with ``permalink:`` set in its frontmatter (added if missing)."""
    m = re.match(r"^﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", text, re.S)
    if not m:
        return f"---\npermalink: {permalink}\n---\n\n{text}"
    fm = m.group(1)
    if re.search(r"^permalink\s*:", fm, re.M):
        fm = re.sub(r"^permalink\s*:.*$", f"permalink: {permalink}", fm, count=1, flags=re.M)
    else:
        fm = fm + f"\npermalink: {permalink}"
    return f"---\n{fm}\n---{m.group(2)}" + text[m.end():]


def _same_import(dest: Path, text: str) -> bool:
    """Is ``dest`` this note as an earlier import wrote it (maybe with a new permalink)?"""
    try:
        old = dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if old == text:
        return True
    m = re.search(r"^permalink:\s*(\S+)\s*$", old, re.M)
    return bool(m) and _set_permalink(text, m.group(1)) == old


def _write_new(path: Path, data: bytes) -> None:
    """Create ``path`` (never replaces an existing file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.import.tmp")
    tmp.write_bytes(data)
    try:
        os.link(tmp, path)  # fails if the file appeared meanwhile
    except FileExistsError:
        raise
    except OSError:  # no hard links (some file systems): exclusive create instead
        with open(path, "xb") as f:
            f.write(data)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def import_markdown(source: Path, base: str, budget: Budget, result: IngestResult,
                    folder_name: str = "") -> None:
    """Copy Markdown notes (folder or zip) into ``<base>/imported/<folder-name>/``."""
    from myclaw.knowledge.parser import parse_note_text
    from myclaw.knowledge.storage import get_knowledge_dir, read_note
    from myclaw.knowledge.sync import sync_knowledge

    name = slug(Path(folder_name).stem if folder_name else source.stem, 60) or "notes"
    kdir = get_knowledge_dir(base)
    dest_root = kdir / "imported" / name
    files: List[Tuple[str, bytes]] = []
    attachments = 0
    if source.is_dir():
        for p in _iter_md(source):
            rel = p.relative_to(source).as_posix()
            if is_secret_file(p.name):
                result.skipped.append(f"{rel} (may contain secrets)")
                continue
            size = p.stat().st_size
            if size > MAX_ZIP_MEMBER or not budget.take_bytes(size):
                result.skipped.append(f"{rel} (too large)")
                continue
            files.append((rel, p.read_bytes()))
        attachments = sum(1 for _ in source.rglob("*") if _.is_file()
                          and _.suffix.lower() not in (".md", ".markdown")
                          and not any(part.startswith(".") for part in _.relative_to(source).parts))
    else:
        try:
            zf = zipfile.ZipFile(source)
        except zipfile.BadZipFile as e:
            raise IngestError(f"'{source.name}' is not a valid zip file.") from e
        with zf:
            total = 0
            names = [i for i in zf.infolist() if not i.is_dir()]
            # a zip of one top folder: strip it ("vault/note.md" → "note.md")
            tops = {safe_relpath(i.filename).split("/", 1)[0] for i in names
                    if safe_relpath(i.filename) and "/" in (safe_relpath(i.filename) or "")}
            strip = (next(iter(tops)) + "/") if len(tops) == 1 and all(
                (safe_relpath(i.filename) or "").startswith(next(iter(tops)) + "/") for i in names
                if safe_relpath(i.filename)) else ""
            for info in names:
                rel = safe_relpath(info.filename)
                if rel is None or "__MACOSX" in info.filename:
                    continue
                if not rel.lower().endswith((".md", ".markdown")):
                    attachments += 1
                    continue
                rel = rel[len(strip):] if strip and rel.startswith(strip) else rel
                if is_secret_file(rel.rsplit("/", 1)[-1]):
                    result.skipped.append(f"{rel} (may contain secrets)")
                    continue
                total += info.file_size
                if total > MAX_ZIP_TOTAL or not budget.take_bytes(info.file_size):
                    result.skipped.append(f"{rel} and the rest (size limit)")
                    break
                files.append((rel, _read_member(zf, info, MAX_ZIP_MEMBER)))
    if not files:
        raise IngestError(f"No Markdown notes found in '{source.name}'.")
    if attachments:
        result.skipped.append(f"{attachments} attachments (images, PDFs…) not copied — "
                              "only Markdown notes are imported")

    taken: Dict[str, str] = {}
    copied: List[Tuple[Path, str]] = []
    unchanged = kept_beside = renamed = redacted = 0
    for rel, data in files:
        if budget.notes_written >= budget.max_notes:
            result.stopped = result.stopped or "note limit"
            result.skipped.append(f"{len(files) - len(copied) - unchanged} notes not imported "
                                  f"(limit of {budget.max_notes})")
            break
        if budget.out_of_time(margin=5.0):
            result.stopped = budget.stop_reason() or "time limit reached"
            result.skipped.append("the rest: time limit; import again to continue")
            break
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        clean = redact(text)
        if clean != text:
            redacted += 1
        dest = dest_root / rel
        if dest.exists():
            if _same_import(dest, clean):
                unchanged += 1
                note = parse_note_text(dest.read_text(encoding="utf-8", errors="replace"), dest)
                result.notes.append(NoteRef(note.permalink, note.title, "unchanged"))
                taken[note.permalink] = str(dest)
                continue
            n = 2
            while dest.exists():
                cand = dest.with_name(f"{Path(rel).stem}-{n}{dest.suffix}")
                if cand.exists() and _same_import(cand, clean):
                    dest = cand
                    break
                dest = cand
                n += 1
            if dest.exists():  # an identical earlier copy
                unchanged += 1
                note = parse_note_text(dest.read_text(encoding="utf-8", errors="replace"), dest)
                result.notes.append(NoteRef(note.permalink, note.title, "unchanged"))
                taken[note.permalink] = str(dest)
                continue
            kept_beside += 1
        note = parse_note_text(clean, dest)
        perm = note.permalink
        other = read_note(perm, base)
        clash = (perm in taken) or (other is not None and other.file_path is not None
                                    and Path(other.file_path).resolve() != dest.resolve())
        if clash:
            # the same permalink as another note: the index would skip this one
            new_perm = slug(f"{name}-{perm}", 100)
            k = 2
            while new_perm in taken or read_note(new_perm, base) is not None:
                new_perm = slug(f"{name}-{perm}-{k}", 100)
                k += 1
            clean = _set_permalink(clean, new_perm)
            note = parse_note_text(clean, dest)
            renamed += 1
        try:
            _write_new(dest, clean.encode("utf-8"))
        except FileExistsError:
            result.errors.append(f"{rel}: a file appeared at the same place meanwhile; not copied")
            continue
        except OSError as e:
            result.errors.append(f"{rel}: {e}")
            continue
        taken[note.permalink] = str(dest)
        copied.append((dest, note.permalink))
        budget.notes_written += 1
        result.notes.append(NoteRef(note.permalink, note.title, "created"))
    budget.report("indexing the imported notes")
    try:
        stats = sync_knowledge(base)
        if stats.get("errors"):
            result.errors.append(f"{stats['errors']} notes could not be indexed — see the doctor / "
                                 "the log for the file names")
    except Exception as e:
        result.errors.append(f"indexing failed ({e}); run 'zensynora knowledge sync'")
    result.extra.update({"folder": f"imported/{name}", "copied": len(copied),
                         "unchanged_files": unchanged, "kept_beside_existing": kept_beside,
                         "permalinks_changed": renamed, "redacted": redacted})
    if kept_beside:
        result.skipped.append(f"{kept_beside} notes differ from an earlier import; saved beside it "
                              "as <name>-2.md (existing files are never overwritten)")
    if renamed:
        result.skipped.append(f"{renamed} notes had the permalink of another note; they got "
                              f"'{name}-…' permalinks")
    if redacted:
        result.skipped.append(f"secrets were hidden in {redacted} notes ([redacted])")


# ── entry point ──────────────────────────────────────────────────────────────
def import_export(path: Path, base: str, kind: str, budget: Budget, result: IngestResult,
                  display_name: str = "") -> None:
    """Import ``path`` (file, zip or folder) of the given kind into ``base``."""
    kind = normalize_kind(kind)
    if kind == "auto":
        kind = detect_kind(path)
    result.kind = kind
    budget.report(f"importing ({kind})")
    if kind == "chatgpt":
        data = _load_json(path, "conversations.json")
        if not isinstance(data, list):
            raise IngestError("This is not a ChatGPT export: conversations.json must hold a list of "
                              "conversations. Export from ChatGPT → Settings → Data controls → Export.")
        import_conversations(data, "chatgpt", base, budget, result)
    elif kind == "claude":
        if path.suffix.lower() == ".zip":
            convs = _load_json(path, "conversations.json")
            projects = _load_json(path, "projects.json")
        else:
            data = _load_json(path, path.name)
            rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
            convs = rows if any("chat_messages" in x for x in rows) else None
            projects = rows if convs is None and any(
                "docs" in x or "prompt_template" in x for x in rows) else None
        if convs is None and projects is None:
            raise IngestError("No conversations.json or projects.json found. Export from Claude.ai "
                              "→ Settings → Privacy → Export data.")
        if isinstance(convs, list) and convs:
            import_conversations(convs, "claude", base, budget, result)
        if isinstance(projects, list) and projects:
            import_claude_projects(projects, base, budget, result)
    elif kind == "mcp_memory":
        if path.suffix.lower() == ".zip":
            data = _load_json(path, "memory.json") or _load_json(path, "memory.jsonl")
        else:
            data = _load_json(path, path.name)
        import_mcp_memory(data, base, budget, result)
    elif kind == "markdown":
        import_markdown(path, base, budget, result, folder_name=display_name)
    else:  # a single document
        from .documents import read_document

        doc = read_document(path, budget, source=display_name or str(path))
        if display_name:
            doc.permalink_hint = f"{doc.kind}-{Path(display_name).stem}"
            doc.title = doc.title if doc.title != path.stem.replace("_", " ") else Path(display_name).stem
        write_document_notes(doc, base, max_notes=budget.max_notes, budget=budget, result=result)
        result.kind = doc.kind


__all__ = ["KINDS", "detect_kind", "import_export", "normalize_kind", "import_conversations",
           "import_mcp_memory", "import_markdown", "import_claude_projects"]
