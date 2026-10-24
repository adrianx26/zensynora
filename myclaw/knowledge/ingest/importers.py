"""Importers: chat exports, MCP memory files, Markdown folders (KB phase 5, 2026-10-24).

* **ChatGPT** export (``conversations.json``, also inside the export ``.zip``)
* **Claude.ai** export (``conversations.json`` / ``projects.json``, zip)
* **MCP memory** server file (``memory.json`` / JSONL: entities with
  observations → notes with facts; relations → links)
* **Markdown folder / Obsidian vault** (folder or zip): the files are copied
  into ``<base>/imported/<folder-name>/`` keeping their frontmatter and text;
  the sync indexes them.

Conversations become one note each (title, date, a short transcript), grouped
by month under one overview note. Names come from the export's ids, so
importing the same export again — also after a conversation was renamed —
updates the notes instead of duplicating them.

2026-10-24 (KB phase 5, review): notes the user wrote are never replaced (a
note of another origin with the same permalink → the import is written as
``…-imported``), notes edited by hand since the import are kept, a Markdown
import updates its own earlier copy (tracked in ``<base>/.ingest/``) and only
writes beside a copy the user edited, symlinks are never followed, archives
are read one entry at a time with size caps, and nothing is written after a
cancel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .model import Budget, Document, IngestError, IngestResult, NoteRef, Section
from .notes import (
    Cancelled, _open_fence, _Plan, _write, claim_permalink, clean_body, clean_fact, may_write,
    short_hash, slug, write_document_notes,
)
from .safety import is_secret_file, redact, safe_relpath

logger = logging.getLogger(__name__)

__all__ = ["KINDS", "IngestError", "detect_kind", "import_export", "normalize_kind",
           "import_conversations", "import_mcp_memory", "import_markdown",
           "import_claude_projects", "MAX_IMPORT_NOTES"]

KINDS = ("auto", "chatgpt", "claude", "mcp_memory", "markdown", "document")
_KIND_ALIASES = {"mcp": "mcp_memory", "memory": "mcp_memory", "mcp-memory": "mcp_memory",
                 "obsidian": "markdown", "md": "markdown", "markdown_folder": "markdown",
                 "claude_ai": "claude", "claude.ai": "claude", "openai": "chatgpt", "doc": "document",
                 "file": "document"}
# 2026-10-24 (KB phase 5, review): memory limits. A conversations.json is
# streamed when the optional ``ijson`` package is installed; without it a file
# up to MAX_JSON_BYTES is read at once.
MAX_JSON_BYTES = 150_000_000
MAX_MD_FILE = 2_000_000           # one Markdown note
MAX_MD_TOTAL = 100_000_000        # all Markdown notes of one import
TRANSCRIPT_CHARS = 8_000          # transcript kept per conversation
MESSAGE_CHARS = 1_500             # text kept per message
MAX_IMPORT_NOTES = 10_000         # default note limit of one import


def normalize_kind(kind: str) -> str:
    k = (kind or "auto").strip().lower().replace(" ", "_")
    k = _KIND_ALIASES.get(k, k)
    if k not in KINDS:
        raise IngestError(f"Unknown import kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    return k


def _stop(budget: Budget, result: IngestResult, what: str, left: int) -> bool:
    """True when the run must stop (cancel / time); the reason is reported once."""
    if not may_write(budget) or budget.out_of_time(margin=5.0):
        result.stopped = result.stopped or budget.stop_reason() or "time limit reached"
        if left > 0:
            result.skipped.append(f"{left} {what} not imported ({result.stopped}); import again "
                                  "to continue")
        return True
    return False


# ── reading exports ──────────────────────────────────────────────────────────
def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _find_member(zf: zipfile.ZipFile, name: str) -> Optional[zipfile.ZipInfo]:
    """The shallowest member whose file name is ``name`` (case-insensitive)."""
    hits = [i for i in zf.infolist() if not i.is_dir() and not _is_symlink_entry(i)
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
        if any(True for _ in _iter_md(path, limit=1)):
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
                if any(i.filename.lower().endswith((".md", ".markdown")) for i in zf.infolist()):
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


def _open_json(path: Path, member: str):
    """(binary file object, size, name) of a JSON file or of a zip member; None if absent."""
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        info = _find_member(zf, member)
        if info is None:
            zf.close()
            return None
        f = zf.open(info)
        orig_close = f.close

        def close() -> None:
            orig_close()
            zf.close()

        f.close = close  # type: ignore[method-assign]
        return f, info.file_size, info.filename
    return open(path, "rb"), path.stat().st_size, path.name


def _load_json(path: Path, member: str) -> Any:
    """JSON from a file, or from the member ``member`` of a zip (size-capped)."""
    opened = _open_json(path, member)
    if opened is None:
        return None
    f, size, name = opened
    with f:
        if size > MAX_JSON_BYTES:
            raise IngestError(f"'{name}' is {size // 1_000_000} MB; files over "
                              f"{MAX_JSON_BYTES // 1_000_000} MB need the 'ijson' package "
                              "(pip install ijson) to be read piece by piece.")
        data = f.read(MAX_JSON_BYTES + 1)
    return _parse_json(data.decode("utf-8", errors="replace"), name)


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


def _array_source(path: Path, member: str) -> Optional[Callable[[], Iterator[Any]]]:
    """A function giving a fresh iterator over the items of a top-level JSON array.

    With ``ijson`` installed the file is streamed (any size, little memory);
    otherwise it is loaded once (up to MAX_JSON_BYTES).
    """
    opened = _open_json(path, member)
    if opened is None:
        return None
    f, size, name = opened
    f.close()
    try:
        import ijson  # optional
    except ImportError:
        ijson = None
    if ijson is not None and size > 20_000_000:
        def stream() -> Iterator[Any]:
            got = _open_json(path, member)
            if got is None:
                return
            fh = got[0]
            with fh:
                yield from ijson.items(fh, "item")
        return stream
    data = _load_json(path, member)
    if not isinstance(data, list):
        raise IngestError(f"'{name}' does not hold a list of conversations.")
    return lambda: iter(data)


# ── conversations ────────────────────────────────────────────────────────────
def _ts(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        if not isinstance(value, str):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)  # float / Decimal (ijson)
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except ValueError:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError, TypeError):
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
    else:  # no current node: follow the last child from the root
        root = next((k for k, v in mapping.items() if not (v or {}).get("parent")), None)
        node_id = root
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            chain.append(mapping[node_id])
            kids = mapping[node_id].get("children") or []
            node_id = kids[-1] if kids else None
    out: List[Tuple[str, str]] = []
    for node in chain:
        msg = (node or {}).get("message") or {}
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
        if not isinstance(m, dict):
            continue
        sender = (m.get("sender") or "").lower()
        role = "user" if sender in ("human", "user") else "assistant"
        text = m.get("text") or ""
        if not text and m.get("content"):
            text = "\n".join(str(c.get("text")) for c in m["content"]
                             if isinstance(c, dict) and c.get("type", "text") == "text" and c.get("text"))
        for a in m.get("attachments") or []:
            if isinstance(a, dict) and a.get("file_name"):
                text += f"\n[attachment: {a['file_name']}]"
        if text.strip():
            out.append((role, text.strip()))
    return out


def _fit_message(text: str) -> str:
    """At most MESSAGE_CHARS; a code block cut in the middle is closed."""
    if len(text) <= MESSAGE_CHARS:
        cut = text
    else:
        cut = text[:MESSAGE_CHARS].rstrip()
        fence = _open_fence(cut)
        cut += ("\n" + fence[:3] + "\n" if fence else "") + " …"
    fence = _open_fence(cut)
    return cut + ("\n" + fence[:3] if fence else "")


def _transcript(messages: List[Tuple[str, str]], assistant: str) -> str:
    """'**You:**' / '**<assistant>:**' blocks, each message's code blocks closed."""
    lines: List[str] = []
    used = 0
    for i, (role, text) in enumerate(messages):
        who = "You" if role == "user" else assistant
        block = f"**{who}:**\n\n{_fit_message(text)}"
        if used + len(block) > TRANSCRIPT_CHARS:
            lines.append(f"*(transcript shortened: {len(messages) - i} more messages)*")
            break
        lines.append(block)
        used += len(block)
    return "\n\n".join(lines)


def _conv_key(c: Dict[str, Any]) -> str:
    cid = str(c.get("conversation_id") or c.get("id") or c.get("uuid") or "")
    if cid:
        return cid
    return f"{c.get('title') or c.get('name')}|{c.get('create_time') or c.get('created_at')}"


def _conv_when(c: Dict[str, Any], source_name: str) -> datetime:
    when = _ts(c.get("create_time") if source_name == "chatgpt" else c.get("created_at"))
    return when or datetime(1970, 1, 1, tzinfo=timezone.utc)


def import_conversations(convs: Any, source_name: str, base: str,
                         budget: Budget, result: IngestResult, group_by_month: bool = True) -> None:
    """ChatGPT (``source_name='chatgpt'``) or Claude conversations → notes.

    ``convs`` is a list or a function returning a fresh iterator (a streamed
    export is read twice: once to choose, once to write).
    """
    source = convs if callable(convs) else (lambda: iter(convs))
    label = "ChatGPT" if source_name == "chatgpt" else "Claude"
    getter = _chatgpt_messages if source_name == "chatgpt" else _claude_messages
    owner = {"source": f"{source_name}-export", "ingest_kind": source_name}

    # pass 1: which conversations (newest first when the note limit cuts)
    index: List[Tuple[datetime, int]] = []
    for i, c in enumerate(source()):
        if isinstance(c, dict):
            index.append((_conv_when(c, source_name), i))
    index.sort(key=lambda x: x[0], reverse=True)
    room = budget.max_notes - budget.notes_written
    chosen: List[Tuple[datetime, int]] = []
    months_used: set = set()
    for when, i in index:
        month = when.strftime("%Y-%m") if when.year > 1970 else "undated"
        extra = (1 if month not in months_used else 0) + (1 if group_by_month and not months_used else 0)
        if len(chosen) + 1 + (len(months_used) + extra if group_by_month else 0) > room:
            break
        chosen.append((when, i))
        months_used.add(month)
    dropped = len(index) - len(chosen)
    if dropped:
        groups = len(months_used) + 1 if group_by_month else 0
        result.skipped.append(f"{dropped} older conversations not imported: the limit is "
                              f"{budget.max_notes} notes ({groups} of them month/overview notes) — "
                              "import again with a higher limit (--max-notes)")
        result.stopped = result.stopped or "note limit"
    wanted = {i for _, i in chosen}

    # names (a note of the user with the same name is never replaced)
    taken: set = set()
    root_perm = claim_permalink(f"{source_name}-conversations", base, owner, taken)
    taken.add(root_perm)
    month_perm: Dict[str, str] = {}
    for m in sorted(months_used):
        month_perm[m] = claim_permalink(f"{source_name}-conversations-{m}", base, owner, taken)
        taken.add(month_perm[m])

    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    months: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    written = kept = 0
    for i, c in enumerate(source()):
        if i not in wanted or not isinstance(c, dict):
            continue
        if _stop(budget, result, "conversations", len(wanted) - written - kept):
            break
        when = _conv_when(c, source_name)
        key = _conv_key(c)
        title = re.sub(r"\s+", " ", str(c.get("title") or c.get("name") or "")).strip() or "Untitled"
        date = when.strftime("%Y-%m-%d") if when.year > 1970 else ""
        # 2026-10-24 (KB phase 5, review): the name comes from the conversation
        # id only — a renamed conversation updates the same note
        perm = claim_permalink(
            f"{source_name}-{date.replace('-', '')}-{short_hash(key, 10)}" if date
            else f"{source_name}-{short_hash(key, 10)}", base, owner, taken)
        taken.add(perm)
        msgs = getter(c)
        month = when.strftime("%Y-%m") if when.year > 1970 else "undated"
        note_title = f"{title[:100]} ({label}, {date})" if date else f"{title[:100]} ({label})"
        body = (f"*Imported {label} conversation" + (f" of {date}" if date else "")
                + f" · {len(msgs)} messages*\n\n" + clean_body(_transcript(msgs, label)))
        facts = [("date", date)] if date else []
        facts.append(("messages", str(len(msgs))))
        model = c.get("default_model_slug") or c.get("model")
        if model:
            facts.append(("model", clean_fact(str(model))))
        links = [("part_of", month_perm[month])] if group_by_month else []
        plan = _Plan(perm, note_title, body, ["imported", source_name], facts, links)
        meta = {**owner, "conversation_id": clean_fact(key, 120), "ingested": stamp}
        try:
            action = _write(plan, base, meta, budget)
        except Cancelled:
            result.stopped = "cancelled"
            break
        except Exception as e:
            logger.warning("Import: could not write conversation %s: %s", perm, e)
            result.errors.append(f"could not write '{title}': {e}")
            continue
        kept += action == "kept"
        written += action != "kept"
        budget.notes_written += 1
        result.notes.append(NoteRef(perm, note_title, action))
        months[month].append((perm, note_title))
        if (written + kept) % 25 == 0:
            budget.report(f"{written + kept}/{len(wanted)} conversations")
    result.extra["conversations"] = written + kept
    if kept:
        result.skipped.append(f"kept your edits: {kept} conversation notes were changed by hand "
                              "and were not updated")
    if group_by_month and months and may_write(budget):
        for month, convs_in in sorted(months.items()):
            body = (f"*{len(convs_in)} {label} conversations from {month}, imported from an "
                    "export.*\n\n" + "\n".join(f"{n}. [[{p}]] — {t}" for n, (p, t) in
                                                enumerate(sorted(convs_in, key=lambda x: x[1]), 1)))
            _write_group(_Plan(month_perm[month], f"{label} conversations {month}", body,
                               ["imported", source_name], [("conversations", str(len(convs_in)))],
                               [("part_of", root_perm)]), base, stamp, owner, budget, result)
        body = (f"*Conversations imported from a {label} export.*\n\n"
                + "\n".join(f"* [[{month_perm[m]}]] — {len(v)} conversations"
                            for m, v in sorted(months.items(), reverse=True)))
        _write_group(_Plan(root_perm, f"{label} conversations", body, ["imported", source_name],
                           [], []), base, stamp, owner, budget, result, root=True)


def _write_group(plan: _Plan, base: str, stamp: str, owner: Dict[str, str], budget: Budget,
                 result: IngestResult, root: bool = False) -> None:
    from myclaw.knowledge.storage import read_note

    old = read_note(plan.permalink, base)
    if old is not None:
        # keep the entries of earlier imports (another export of other months)
        new_items = set(re.findall(r"\[\[([^\]]+)\]\]", plan.body))
        missing = [line for line in old.content.splitlines()
                   if re.match(r"^(?:\d+\.|\*) \[\[([^\]]+)\]\]", line)
                   and re.findall(r"\[\[([^\]]+)\]\]", line)[0] not in new_items]
        if missing:
            plan.body += "\n" + "\n".join(missing)
    try:
        action = _write(plan, base, {**owner, "ingested": stamp}, budget)
    except Cancelled:
        result.stopped = "cancelled"
        return
    except Exception as e:
        result.errors.append(f"could not write '{plan.title}': {e}")
        return
    budget.notes_written += 1
    ref = NoteRef(plan.permalink, plan.title, action)
    if root:
        result.notes.insert(0, ref)
        result.overview = ref
    else:
        result.notes.append(ref)


def import_claude_projects(projects: List[Dict[str, Any]], base: str, budget: Budget,
                           result: IngestResult) -> None:
    """Claude.ai projects: one overview per project, one note per project document."""
    n = 0
    for p in projects:
        if not isinstance(p, dict):
            continue
        if budget.notes_written >= budget.max_notes:
            result.stopped = result.stopped or "note limit"
            result.skipped.append(f"{len(projects) - n} projects not imported (note limit)")
            break
        if _stop(budget, result, "projects", len(projects) - n):
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
        result.skipped.extend(s for s in sub.skipped if s not in result.skipped)
        result.overview = result.overview or sub.overview
        if sub.stopped == "cancelled":
            result.stopped = "cancelled"
            break
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
    owner = {"source": "mcp-memory", "ingest_kind": "mcp_memory"}
    names: Dict[str, str] = {}
    taken: set = set()
    for e in entities:
        name = str(e.get("name") or e.get("entityName") or e.get("id") or "").strip()
        if name and name not in names:
            names[name] = claim_permalink(slug(f"mcp-{slug(name, 60)}-{short_hash(name, 4)}", 100),
                                          base, owner, taken)
            taken.add(names[name])
    by_source: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for r in relations:
        src = str(r.get("from") or r.get("from_id") or "").strip()
        dst = str(r.get("to") or r.get("to_id") or "").strip()
        rel = slug(str(r.get("relationType") or r.get("relation_type") or "relates_to"), 40)
        rel = rel.replace("-", "_") or "relates_to"
        if src and dst:
            by_source[src].append((rel, names.get(dst, dst)))
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    done = kept = 0
    for e in entities:
        name = str(e.get("name") or e.get("entityName") or e.get("id") or "").strip()
        if not name:
            result.skipped.append("an entity without a name")
            continue
        if budget.notes_written >= budget.max_notes:
            result.stopped = result.stopped or "note limit"
            result.skipped.append(f"{len(entities) - done - kept} entities not imported (note limit)")
            break
        if _stop(budget, result, "entities", len(entities) - done - kept):
            break
        etype = str(e.get("entityType") or e.get("entity_type") or "entity").strip()
        obs = [clean_fact(o, 500) for o in (e.get("observations") or []) if str(o).strip()]
        perm = names[name]
        body = f"*Imported from an MCP memory file · type: {clean_fact(etype, 60)}*"
        plan = _Plan(perm, name[:150], body, ["imported", "mcp-memory", slug(etype, 30) or "entity"],
                     [("fact", o) for o in obs if o], list(dict.fromkeys(by_source.get(name, []))))
        try:
            action = _write(plan, base, {**owner, "type": etype, "ingested": stamp}, budget)
        except Cancelled:
            result.stopped = "cancelled"
            break
        except Exception as ex:
            result.errors.append(f"could not write '{name}': {ex}")
            continue
        kept += action == "kept"
        done += action != "kept"
        budget.notes_written += 1
        result.notes.append(NoteRef(perm, name, action))
    if kept:
        result.skipped.append(f"kept your edits: {kept} notes were changed by hand and were not "
                              "updated")
    result.extra.update({"entities": done + kept,
                         "relations": sum(len(v) for v in by_source.values())})


# ── Markdown folder / Obsidian vault ─────────────────────────────────────────
def _iter_md(folder: Path, limit: Optional[int] = None) -> Iterable[Path]:
    """Markdown files of a folder: no hidden folders, never through a symlink.

    2026-10-24 (KB phase 5, review): a symlinked note pointed outside the
    folder (~/.myclaw/config.json, /etc/passwd) and was copied.
    """
    n = 0
    root = folder.resolve()
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__MACOSX"
                             and not (Path(dirpath) / d).is_symlink())
        for f in sorted(filenames):
            if not f.lower().endswith((".md", ".markdown")) or f.startswith("."):
                continue
            p = Path(dirpath) / f
            if p.is_symlink():
                continue
            try:
                p.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            yield p
            n += 1
            if limit and n >= limit:
                return


def _md_entries(source: Path, budget: Budget, result: IngestResult) -> Iterator[Tuple[str, bytes]]:
    """(relative path, bytes) of each Markdown note, read one at a time, size-capped."""
    total = 0
    if source.is_dir():
        links = attachments = 0
        root = source.resolve()
        for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__MACOSX"]
            for f in filenames:
                p = Path(dirpath) / f
                if p.is_symlink():
                    links += 1
                elif not f.lower().endswith((".md", ".markdown")) and not f.startswith("."):
                    attachments += 1
        if links:
            result.skipped.append(f"{links} symbolic links not followed")
        if attachments:
            result.skipped.append(f"{attachments} attachments (images, PDFs…) not copied — only "
                                  "Markdown notes are imported")
        for p in _iter_md(source):
            rel = p.resolve().relative_to(root).as_posix()
            if is_secret_file(p.name):
                result.skipped.append(f"{rel} (may contain secrets)")
                continue
            size = p.stat().st_size
            if size > MAX_MD_FILE:
                result.skipped.append(f"{rel} ({size // 1_000_000} MB; notes over "
                                      f"{MAX_MD_FILE // 1_000_000} MB are not imported)")
                continue
            total += size
            if total > MAX_MD_TOTAL or not budget.take_bytes(size):
                result.skipped.append(f"{rel} and the rest (size limit of "
                                      f"{MAX_MD_TOTAL // 1_000_000} MB per import)")
                return
            with open(p, "rb") as fh:
                yield rel, fh.read(MAX_MD_FILE + 1)[:MAX_MD_FILE]
        return
    try:
        zf = zipfile.ZipFile(source)
    except zipfile.BadZipFile as e:
        raise IngestError(f"'{source.name}' is not a valid zip file.") from e
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        safe = {i.filename: safe_relpath(i.filename) for i in infos}
        tops = {r.split("/", 1)[0] for r in safe.values() if r and "/" in r}
        strip = ""
        if len(tops) == 1:
            top = next(iter(tops)) + "/"
            if all(r.startswith(top) for r in safe.values() if r):
                strip = top
        attachments = links = 0
        for info in infos:
            rel = safe[info.filename]
            if rel is None or "__MACOSX" in info.filename:
                continue
            if _is_symlink_entry(info):
                links += 1
                continue
            if not rel.lower().endswith((".md", ".markdown")):
                attachments += 1
                continue
            rel = rel[len(strip):] if strip and rel.startswith(strip) else rel
            if is_secret_file(rel.rsplit("/", 1)[-1]):
                result.skipped.append(f"{rel} (may contain secrets)")
                continue
            if info.file_size > MAX_MD_FILE:
                result.skipped.append(f"{rel} ({info.file_size // 1_000_000} MB; notes over "
                                      f"{MAX_MD_FILE // 1_000_000} MB are not imported)")
                continue
            total += info.file_size
            if total > MAX_MD_TOTAL or not budget.take_bytes(info.file_size):
                result.skipped.append(f"{rel} and the rest (size limit of "
                                      f"{MAX_MD_TOTAL // 1_000_000} MB per import)")
                break
            try:
                data = _read_member(zf, info, MAX_MD_FILE)
            except IngestError as e:
                result.skipped.append(str(e))
                continue
            yield rel, data
        if links:
            result.skipped.append(f"{links} symbolic links in the zip not followed")
        if attachments:
            result.skipped.append(f"{attachments} attachments (images, PDFs…) not copied — only "
                                  "Markdown notes are imported")


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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def _write_file(path: Path, data: bytes, replace: bool = False) -> None:
    """Write ``path``: a new file (never over another) or, with ``replace``, our own copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.import.tmp")
    tmp.write_bytes(data)
    # 2026-10-25 (KB phase 6): our own write — the file watcher leaves it to
    # the import's sync
    from myclaw.knowledge.watch import remember_self_write

    remember_self_write(path, data)
    try:
        if replace:
            os.replace(tmp, path)
            return
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


def _to_trash(path: Path, kdir: Path) -> None:
    """Keep the previous version of an imported copy in ``<base>/.trash/``."""
    trash = kdir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = trash / f"{path.stem}-{stamp}.md"
    n = 2
    while dest.exists():
        dest = trash / f"{path.stem}-{stamp}-{n}.md"
        n += 1
    shutil.copy2(path, dest)


def _free_beside(dest: Path) -> Path:
    n = 2
    cand = dest.with_name(f"{dest.stem}-{n}{dest.suffix}")
    while cand.exists():
        n += 1
        cand = dest.with_name(f"{dest.stem}-{n}{dest.suffix}")
    return cand


def import_markdown(source: Path, base: str, budget: Budget, result: IngestResult,
                    folder_name: str = "") -> None:
    """Copy Markdown notes (folder or zip) into ``<base>/imported/<folder-name>/``.

    A manifest in ``<base>/.ingest/`` remembers which file each source note
    was copied to and what was written. Importing again: an unchanged note is
    left alone; a changed note updates our copy (the old version goes to
    ``.trash``) — unless the copy was edited by hand, then the new version is
    written beside it once (and that beside-copy is updated next time).
    """
    from myclaw.knowledge.parser import parse_note_text
    from myclaw.knowledge.storage import get_knowledge_dir, read_note
    from myclaw.knowledge.sync import sync_knowledge

    name = slug(Path(folder_name).stem if folder_name else source.stem, 60) or "notes"
    kdir = get_knowledge_dir(base)
    dest_root = kdir / "imported" / name
    manifest_path = kdir / ".ingest" / f"markdown-{name}.json"
    try:
        manifest: Dict[str, Dict[str, str]] = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, ValueError):
        manifest = {}

    taken: Dict[str, str] = {}
    counts = {"copied": 0, "updated": 0, "unchanged": 0, "kept": 0, "renamed": 0, "redacted": 0}
    seen_any = False

    def permalink_ok(text: str, dest: Path) -> Tuple[str, Any]:
        """The text (with a new permalink if another note has this one) and its note."""
        note = parse_note_text(text, dest)
        other = read_note(note.permalink, base)
        clash = (taken.get(note.permalink, str(dest)) != str(dest)) or (
            other is not None and other.file_path is not None
            and Path(other.file_path).resolve() != dest.resolve())
        if clash:
            new_perm = slug(f"{name}-{note.permalink}", 100)
            k = 2
            while new_perm in taken or read_note(new_perm, base) is not None:
                new_perm = slug(f"{name}-{note.permalink}-{k}", 100)
                k += 1
            text = _set_permalink(text, new_perm)
            note = parse_note_text(text, dest)
            counts["renamed"] += 1
        return text, note

    def same_as_written(path: Path, sha: str) -> bool:
        try:
            return _sha(path.read_bytes()) == sha
        except OSError:
            return False

    for rel, data in _md_entries(source, budget, result):
        seen_any = True
        if budget.notes_written >= budget.max_notes:
            result.stopped = result.stopped or "note limit"
            result.skipped.append(f"the rest not imported (limit of {budget.max_notes} notes)")
            break
        if _stop(budget, result, "notes", 1):
            break
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        clean = redact(text)
        if clean != text:
            counts["redacted"] += 1
        entry = manifest.get(rel) or {}
        own = dest_root / entry["dest"] if entry.get("dest") else None
        target: Optional[Path] = None
        replace = False
        if own is not None and own.exists():
            if same_as_written(own, entry.get("sha", "")):
                target, replace = own, True          # our unedited copy: update it
            else:
                beside = dest_root / entry["beside"] if entry.get("beside") else None
                if beside is not None and beside.exists() and same_as_written(
                        beside, entry.get("beside_sha", "")):
                    target, replace = beside, True   # the copy we wrote beside last time
                elif beside is not None and beside.exists():
                    counts["kept"] += 1              # both copies edited by hand
                    result.notes.append(NoteRef(parse_note_text(own.read_text(errors="replace"), own).permalink,
                                                rel, "kept"))
                    continue
                else:
                    target = _free_beside(own)
                counts["kept"] += 1
        else:
            dest = dest_root / rel
            if dest.exists():
                current = dest.read_text(encoding="utf-8", errors="replace")
                if _same_import(dest, clean):         # imported before (no manifest yet)
                    manifest[rel] = {"dest": rel, "sha": _sha(current.encode("utf-8"))}
                    note = parse_note_text(current, dest)
                    taken[note.permalink] = str(dest)
                    counts["unchanged"] += 1
                    result.notes.append(NoteRef(note.permalink, note.title, "unchanged"))
                    budget.notes_written += 1
                    continue
                target = _free_beside(dest)          # someone else's file: never overwritten
                counts["kept"] += 1
            else:
                target = dest
        assert target is not None
        if replace:
            old_text = target.read_text(encoding="utf-8", errors="replace")
            perm_m = re.search(r"^permalink:\s*(\S+)\s*$", old_text, re.M)
            new_text = clean
            src_perm = re.search(r"^permalink:\s*(\S+)\s*$", clean, re.M)
            if perm_m and (not src_perm or src_perm.group(1) != perm_m.group(1)):
                new_text = _set_permalink(clean, perm_m.group(1))  # keep the name we gave it
            if new_text == old_text:
                note = parse_note_text(old_text, target)
                taken[note.permalink] = str(target)
                counts["unchanged"] += 1
                result.notes.append(NoteRef(note.permalink, note.title, "unchanged"))
                budget.notes_written += 1
                continue
            note = parse_note_text(new_text, target)
            out = new_text
        else:
            out, note = permalink_ok(clean, target)
        if not may_write(budget):
            result.stopped = "cancelled"
            break
        try:
            if replace:
                _to_trash(target, kdir)
            _write_file(target, out.encode("utf-8"), replace=replace)
        except FileExistsError:
            result.errors.append(f"{rel}: a file appeared at the same place meanwhile; not copied")
            continue
        except OSError as e:
            result.errors.append(f"{rel}: {e}")
            continue
        rel_target = target.relative_to(dest_root).as_posix()
        sha = _sha(out.encode("utf-8"))
        if own is not None and target != own:
            manifest[rel] = {**entry, "beside": rel_target, "beside_sha": sha}
        elif target != dest_root / rel and own is None:
            manifest[rel] = {"dest": rel_target, "sha": sha}
        else:
            manifest[rel] = {**entry, "dest": rel_target, "sha": sha}
        taken[note.permalink] = str(target)
        counts["updated" if replace else "copied"] += 1
        budget.notes_written += 1
        result.notes.append(NoteRef(note.permalink, note.title, "updated" if replace else "created"))
    if not seen_any:
        if result.skipped:
            raise IngestError(f"No Markdown notes could be imported from '{source.name}': "
                              + "; ".join(result.skipped[:3]))
        raise IngestError(f"No Markdown notes found in '{source.name}'.")
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        tmp.replace(manifest_path)
    except OSError as e:
        result.errors.append(f"could not save the import list ({e}); the next import may write copies")
    if may_write(budget):
        budget.report("indexing the imported notes")
        try:
            from myclaw.knowledge import budget  # 2026-10-25 (KB phase 6): shared write budget

            with budget.background(base, "import", budget.PRIO_INGEST):
                stats = sync_knowledge(base)
            if stats.get("errors"):
                result.errors.append(f"{stats['errors']} notes could not be indexed — see the doctor "
                                     "/ the log for the file names")
        except Exception as e:
            result.errors.append(f"indexing failed ({e}); run 'zensynora knowledge sync'")
    result.extra.update({"folder": f"imported/{name}", "copied": counts["copied"],
                         "updated_files": counts["updated"], "unchanged_files": counts["unchanged"],
                         "kept_your_edits": counts["kept"], "permalinks_changed": counts["renamed"],
                         "redacted": counts["redacted"]})
    if counts["kept"]:
        result.skipped.append(f"kept your edits: {counts['kept']} notes were changed by hand (or "
                              "are not from this import); the new version was saved beside them")
    if counts["renamed"]:
        result.skipped.append(f"{counts['renamed']} notes had the permalink of another note; they "
                              f"got '{name}-…' permalinks")
    if counts["redacted"]:
        result.skipped.append(f"secrets were hidden in {counts['redacted']} notes ([redacted])")


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
        convs = _array_source(path, "conversations.json" if path.suffix.lower() == ".zip" else path.name)
        if convs is None:
            raise IngestError("This is not a ChatGPT export: no conversations.json. Export from "
                              "ChatGPT → Settings → Data controls → Export.")
        import_conversations(convs, "chatgpt", base, budget, result)
    elif kind == "claude":
        if path.suffix.lower() == ".zip":
            convs = _array_source(path, "conversations.json")
            projects = _load_json(path, "projects.json")
        else:
            data = _load_json(path, path.name)
            rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
            convs_list = rows if any("chat_messages" in x for x in rows) else None
            convs = (lambda: iter(convs_list)) if convs_list is not None else None
            projects = rows if convs_list is None and any(
                "docs" in x or "prompt_template" in x for x in rows) else None
        if convs is None and projects is None:
            raise IngestError("No conversations.json or projects.json found. Export from Claude.ai "
                              "→ Settings → Privacy → Export data.")
        if convs is not None:
            import_conversations(convs, "claude", base, budget, result)
        if isinstance(projects, list) and projects and result.stopped != "cancelled":
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
            if doc.title == path.stem.replace("_", " "):
                doc.title = Path(display_name).stem
        write_document_notes(doc, base, max_notes=budget.max_notes, budget=budget, result=result)
        result.kind = doc.kind
