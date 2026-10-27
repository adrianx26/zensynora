"""Turn a :class:`Document` into linked notes (KB phase 5, 2026-10-24).

One **overview** note (title = document title; summary, contents, a Mermaid
diagram when there is structure, facts: source, kind, size, date) and one note
per substantial **section**, each with ``part_of [[overview]]``. Links between
sections (``[[...]]``, markdown links between files, imports in code) become
relations. Notes are written only through ``storage.write_note`` (frontmatter,
facts, links, ``.trash`` backups and indexing are done there).

Names are stable: the overview's permalink comes from the source (URL / path)
and each part's from the overview's plus the section key, so ingesting the
same source again **updates** the same notes (unchanged notes are not
rewritten). Links use permalinks (``[[permalink]]``): unambiguous in the index,
in the Web UI graph and in Obsidian (the file is ``<permalink>.md``).
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .model import Budget, Document, IngestResult, MAX_NOTE_CHARS, NoteRef, Section
from .safety import redact

logger = logging.getLogger(__name__)

MIN_PART_CHARS = 200         # shorter sections stay in the overview instead of a note
SMALL_IN_OVERVIEW = 4_000    # at most this much short-section text in the overview
SUMMARY_CHARS = 2_500        # summary text in the overview
MAX_DIAGRAM_NODES = 40
MAX_LINKS_LISTED = 40

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_FACT_LINE = re.compile(r"^(\s*)- \[(?![ xX]\])([^\]]+)\] ")
_SECTION_HEAD = re.compile(r"^(#{1,6}\s+)(Observations|Relations)\s*$", re.IGNORECASE | re.MULTILINE)


# ── names ────────────────────────────────────────────────────────────────────
def slug(text: str, limit: int = 60) -> str:
    """Lower-case words joined by '-' (the permalink alphabet), at most ``limit`` chars."""
    s = re.sub(r"[^\w]+", "-", (text or "").lower(), flags=re.UNICODE)
    s = re.sub(r"-+", "-", s.replace("_", "-")).strip("-")
    if len(s) > limit:
        s = s[:limit].rstrip("-")
    return s


def short_hash(text: str, n: int = 6) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:n]


def overview_permalink(doc: Document) -> str:
    """Stable permalink of a document's overview: readable part + hash of the source."""
    hint = slug(doc.permalink_hint or f"{doc.kind}-{doc.title}", 60) or doc.kind
    return f"{hint}-{short_hash(doc.source)}"


def _clip_title(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("[[", "[").replace("]]", "]")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ── text hygiene ─────────────────────────────────────────────────────────────
def clean_body(text: str) -> str:
    """Make extracted text safe to store as a note body.

    * secrets are redacted;
    * lines that would read as facts (``- [x] …`` style with another
      category) are escaped (``- \\[``), so a document's bullet ``- [1] ref``
      does not become a fact;
    * ``## Observations`` / ``## Relations`` headings are renamed (the parser
      removes those headings — the text would be lost on the next save);
    * code fences are balanced (an open fence would swallow the generated
      facts and links).
    """
    text = redact(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    out: List[str] = []
    fence: Optional[str] = None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * 3
            elif mark.startswith(fence):
                fence = None
            out.append(line)
            continue
        if fence is None:
            line = _FACT_LINE.sub(lambda mm: f"{mm.group(1)}- \\[{mm.group(2)}] ", line)
        out.append(line)
    if fence is not None:
        out.append(fence)
    body = "\n".join(out)
    body = _SECTION_HEAD.sub(lambda m: f"{m.group(1)}{m.group(2)} (from the source)", body)
    return re.sub(r"\n{4,}", "\n\n\n", body).strip()


def clean_fact(text: str, limit: int = 300) -> str:
    """One line, no secrets, no accidental '#tags' (URL fragments are dropped)."""
    text = redact(re.sub(r"\s+", " ", str(text or ""))).strip()
    text = re.sub(r"(https?://\S+?)#\S*", r"\1", text)
    text = re.sub(r"#(?=\w)", "# ", text)
    text = text.replace("[[", "[").replace("]]", "]")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def chunk_text(text: str, limit: int = MAX_NOTE_CHARS) -> List[str]:
    """Split text into pieces of at most ``limit`` chars at paragraph/line ends.

    A piece that ends inside a code fence is closed and the next one reopens
    it, so every piece is valid Markdown on its own.
    """
    text = text or ""
    if len(text) <= limit:
        return [text]
    limit = max(500, limit)
    pieces: List[str] = []
    rest = text
    reopen = ""
    while rest:
        room = limit - len(reopen) - 8  # space for a closing fence
        if len(rest) <= room:
            pieces.append(reopen + rest)
            break
        cut = rest.rfind("\n\n", 0, room)
        if cut < room // 2:
            cut = rest.rfind("\n", 0, room)
        if cut < room // 2:
            cut = room
        piece, rest = reopen + rest[:cut], rest[cut:].lstrip("\n")
        fence = _open_fence(piece)
        if fence:
            piece += "\n" + fence[:3]
            reopen = fence + "\n"
        else:
            reopen = ""
        pieces.append(piece)
    return pieces


def _open_fence(text: str) -> str:
    """The opening fence line still open at the end of ``text`` ('' if none)."""
    fence: Optional[str] = None
    opener = ""
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if not m:
            continue
        mark = m.group(1)
        if fence is None:
            fence, opener = mark[0] * 3, line.strip()
        elif mark.startswith(fence):
            fence, opener = None, ""
    return opener


# ── Mermaid ──────────────────────────────────────────────────────────────────
def mermaid_label(text: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    for a, b in (('"', "'"), ("[", "("), ("]", ")"), ("{", "("), ("}", ")"), ("<", "‹"),
                 (">", "›"), ("|", "/"), ("`", "'"), ("#", "")):
        text = text.replace(a, b)
    return text or "…"


def sections_diagram(title: str, parts: Sequence[Tuple[str, str]],
                     edges: Sequence[Tuple[str, str, str]] = ()) -> str:
    """``graph TD`` of a document: root → parts, plus dotted links between parts.

    ``parts`` = [(key, label)], ``edges`` = [(key, relation, key)].
    """
    if len(parts) < 2:
        return ""
    lines = ["graph TD", f'  root["{mermaid_label(title, 60)}"]']
    ids: Dict[str, str] = {}
    for i, (key, label) in enumerate(parts[:MAX_DIAGRAM_NODES]):
        ids[key] = f"p{i}"
        lines.append(f'  root --> p{i}["{mermaid_label(label)}"]')
    if len(parts) > MAX_DIAGRAM_NODES:
        lines.append(f'  root --> more["… {len(parts) - MAX_DIAGRAM_NODES} more"]')
    seen = set()
    for a, rel, b in edges:
        if a in ids and b in ids and a != b and (a, b) not in seen:
            seen.add((a, b))
            lines.append(f"  {ids[a]} -.->|{mermaid_label(rel, 20)}| {ids[b]}")
    return "\n".join(lines)


# ── writing ──────────────────────────────────────────────────────────────────
class _Plan:
    """One note to write."""

    def __init__(self, permalink: str, title: str, body: str, tags: List[str],
                 facts: List[Tuple[str, str]], links: List[Tuple[str, str]],
                 key: str = "", heading: str = ""):
        self.permalink = permalink
        self.key = key          # the section this note comes from (continuation)
        self.heading = heading
        self.title = title
        self.body = body
        self.tags = tags
        self.facts = facts
        self.links = links  # (relation, target permalink)


def _norm_tags(tags: Sequence[str]) -> List[str]:
    out: List[str] = []
    for t in tags:
        t = slug(str(t), 40)
        if t and t not in out:
            out.append(t)
    return out


def _source_line(doc: Document) -> str:
    src = doc.source
    if src.startswith(("http://", "https://")):
        return f"*Source: <{src}>*"
    return f"*Source: `{src}`*"


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"


def plan_document(doc: Document, max_notes: int, max_chars: int = MAX_NOTE_CHARS,
                  earlier: Optional[Dict[str, Dict[str, object]]] = None
                  ) -> Tuple[_Plan, List[_Plan], List[str], str]:
    """Plan the overview and the part notes (nothing is written).

    Returns ``(overview, parts, left_out, diagram)``; ``parts`` holds at most
    ``max_notes - 1`` notes. ``earlier``: sections written by an unfinished
    earlier run (key → {perm, heading, parts}); they are not planned again but
    stay in the contents.
    """
    earlier = dict(earlier or {})
    over_perm = overview_permalink(doc)
    title = _clip_title(doc.title) or over_perm
    sections = [s for s in doc.norm_sections() if (s.text or "").strip()
                and (s.key or s.heading) not in earlier]
    kind_tag = slug(doc.kind, 30)
    tags = _norm_tags(["assimilated", kind_tag, *doc.tags])

    # sections → part notes; short ones stay in the overview (up to
    # SMALL_IN_OVERVIEW chars in all — beyond that they get notes too)
    is_big = [len(s.text.strip()) >= MIN_PART_CHARS or bool(s.links) or bool(s.facts)
              for s in sections]
    small_total = sum(len(s.text) for s, b in zip(sections, is_big) if not b)
    if small_total > SMALL_IN_OVERVIEW or doc.split_all:
        is_big = [True] * len(sections)
    if (not doc.split_all and sum(is_big) == 1 and len(sections) == 1
            and len(sections[0].text) <= max_chars // 2):
        is_big = [False]  # a short single-part document: one note is enough
    big = [s for s, b in zip(sections, is_big) if b]
    small = [s for s, b in zip(sections, is_big) if not b]

    used: Dict[str, int] = {}
    key_to_perm: Dict[str, str] = {}
    first_perm: Dict[str, str] = {}
    parts: List[_Plan] = []
    left_out: List[str] = []
    titles_used: Dict[str, int] = {}
    room = max(0, max_notes - 1)
    planned: List[Tuple[Section, List[str], List[str]]] = []  # (section, perms, chunks)
    for s in big:
        key = s.key or s.heading
        base_perm = slug(f"{over_perm}-{slug(key, 50) or 'part'}", 110)
        n = used.get(base_perm, 0) + 1
        used[base_perm] = n
        perm = base_perm if n == 1 else f"{base_perm}-{n}"
        chunks = chunk_text(clean_body(s.text), max_chars - 400)
        remaining = room - sum(len(p[2]) for p in planned)
        if remaining <= 0:
            left_out.append(s.heading)
            continue
        if len(chunks) > remaining:
            left_out.append(f"{s.heading} (only the first {remaining} of {len(chunks)} parts)")
            chunks = chunks[:remaining]
        perms = [perm if i == 0 else f"{perm}-part-{i + 1}" for i in range(len(chunks))]
        planned.append((s, perms, chunks))
        key_to_perm.setdefault(key, perm)
        key_to_perm.setdefault(key.lower(), perm)
        first_perm[s.heading.lower()] = perm

    for k, info in earlier.items():  # links to parts written by the earlier run
        key_to_perm.setdefault(k, str(info.get("perm")))
        key_to_perm.setdefault(k.lower(), str(info.get("perm")))

    # links between sections: by key, by heading, by file stem
    def resolve(key: str) -> Optional[str]:
        k = (key or "").strip()
        return (key_to_perm.get(k) or key_to_perm.get(k.lower())
                or first_perm.get(k.lower()))

    edges: List[Tuple[str, str, str]] = []
    for s, perms, chunks in planned:
        base_title = _clip_title(f"{title} — {s.heading}", 150)
        n = titles_used.get(base_title.lower(), 0) + 1
        titles_used[base_title.lower()] = n
        if n > 1:
            base_title = f"{base_title} ({n})"
        links: List[Tuple[str, str]] = []
        for rel, target_key in s.links:
            target = resolve(target_key)
            if target and target != perms[0] and (rel, target) not in links:
                links.append((slug(rel, 30).replace("-", "_") or "links_to", target))
                edges.append((s.key or s.heading, rel, target_key))
        for i, (perm, chunk) in enumerate(zip(perms, chunks)):
            part_title = base_title if len(chunks) == 1 else f"{base_title} ({i + 1}/{len(chunks)})"
            rels = [("part_of", over_perm)]
            if i == 0:
                rels += links
            else:
                rels.append(("continues", perms[i - 1]))
            facts = [(c, clean_fact(t)) for c, t in s.facts] if i == 0 else []
            body = f"{_source_line(doc)} · part of [[{over_perm}]]\n\n{chunk}"
            parts.append(_Plan(perm, part_title, body, _norm_tags([*tags, *s.tags]),
                               [f for f in facts if f[1]], rels, key=s.key or s.heading,
                               heading=s.heading))

    # the overview
    diagram = (doc.diagram or "").strip()
    if not diagram:
        diagram = sections_diagram(title, [(s.key or s.heading, s.heading) for s, _, _ in planned],
                                   edges)
    summary = (doc.summary or "").strip()
    if not summary and sections:
        summary = sections[0].text.strip()
    summary = clean_body(summary)
    if len(summary) > SUMMARY_CHARS:
        summary = chunk_text(summary, SUMMARY_CHARS)[0].rstrip() + "\n\n…"
    lines = [_source_line(doc), "", summary] if summary else [_source_line(doc)]
    if small:
        small_text = "\n\n".join(f"### {s.heading}\n\n{clean_body(s.text)}" if s.heading else
                                 clean_body(s.text) for s in small)
        lines += ["", small_text]
    if planned or earlier:
        lines += ["", "## Contents", ""]
        entries = [(str(v.get("perm")), str(v.get("heading") or k), int(v.get("parts") or 1))  # type: ignore[call-overload]
                   for k, v in earlier.items()]
        entries += [(perms[0], s.heading, len(chunks)) for s, perms, chunks in planned]
        for i, (perm0, heading, n_parts) in enumerate(entries, 1):
            more = f" ({n_parts} notes)" if n_parts > 1 else ""
            lines.append(f"{i}. [[{perm0}]] — {mermaid_label(heading, 100)}{more}")
    if left_out:
        lines += ["", f"Left out (limit of {max_notes} notes per run): "
                  + "; ".join(left_out[:30]) + (" …" if len(left_out) > 30 else "")]
    if diagram:
        lines += ["", "## Diagram", "", "```mermaid", diagram, "```"]
    if doc.links:
        from .safety import clean_url

        lines += ["", "## Links", ""]
        lines += [f"- <{clean_url(u)}>" for u in doc.links[:MAX_LINKS_LISTED]]
        if len(doc.links) > MAX_LINKS_LISTED:
            lines.append(f"- … and {len(doc.links) - MAX_LINKS_LISTED} more")
    if doc.errors:
        lines += ["", "## Problems while reading", ""]
        lines += [f"* {clean_fact(e)}" for e in doc.errors[:20]]
    body = "\n".join(lines)
    if len(body) > max_chars:
        # keep the contents and the diagram; shorten the summary / small sections
        over = len(body) - max_chars + 200
        cut = max(200, len(summary) - over)
        if summary:
            body = body.replace(summary, chunk_text(summary, cut)[0] + "\n\n…", 1)
        if len(body) > max_chars:
            body = chunk_text(body, max_chars)[0]

    facts: List[Tuple[str, str]] = [("source", clean_fact(doc.source)), ("kind", doc.kind)]
    if doc.size:
        facts.append(("size", _human_size(doc.size)))
    if doc.date:
        facts.append(("date", clean_fact(doc.date)))
    for c, t in doc.facts:
        t = clean_fact(t)
        if t and (c, t) not in facts:
            facts.append((slug(c, 30).replace("-", "_") or "fact", t))
    overview = _Plan(over_perm, title, body, tags, facts, [])
    return overview, parts, left_out, diagram


def _owned(old, meta: Dict[str, object]) -> bool:
    """Was ``old`` written by the ingester for the same source / kind?"""
    om = getattr(old, "metadata", None) or {}
    return (bool(om.get("ingest_kind"))
            and str(om.get("ingest_kind")) == str(meta.get("ingest_kind"))
            and str(om.get("source")) == str(meta.get("source")))


def claim_permalink(perm: str, base: str, meta: Dict[str, object],
                    taken: Optional[set] = None) -> str:
    """``perm`` if it is free or ours (same source/kind), else ``perm-imported[-n]``.

    2026-10-24 (KB phase 5, review): an import never replaces a note the user
    wrote (or another source's note) that happens to have the same permalink.
    """
    from myclaw.knowledge.storage import read_note

    taken = taken if taken is not None else set()
    cand, n = perm, 1
    while True:
        if cand not in taken:
            old = read_note(cand, base)
            if old is None or _owned(old, meta):
                return cand
        n += 1
        cand = f"{perm}-imported" if n == 2 else f"{perm}-imported-{n - 1}"


def _fingerprint(body: str, facts, rels) -> str:
    """A hash of what the ingester wrote (text, facts, links) — to notice user edits."""
    f = sorted(f"{o.category}:{o.content}" for o in facts)
    r = sorted(f"{x.relation_type}:{x.target}" for x in rels)
    return short_hash(_squash(body) + "\x00" + "\x01".join(f) + "\x00" + "\x01".join(r), 16)


def may_write(budget: Optional[Budget]) -> bool:
    """False once the run was cancelled (the tool returned / the user pressed Stop)."""
    return budget is None or not budget.cancel.is_set()


def _write(plan: _Plan, base: str, metadata: Dict[str, object],
           budget: Optional[Budget] = None) -> str:
    """Write one planned note; returns created / updated / unchanged / kept.

    "kept": the note was changed by hand since the ingester wrote it, so it is
    left as it is (2026-10-24, KB phase 5 review: re-ingesting discarded the
    user's edits). Nothing is written once the budget is cancelled.
    """
    from myclaw.knowledge.parser import Relation, extract_body, parse_observations
    from myclaw.knowledge.storage import read_note, write_note

    old = read_note(plan.permalink, base)
    # facts go through the parser, exactly as they will be read back (a
    # trailing "(source: …)" becomes the fact's provenance once KB phase 2 is in)
    facts = parse_observations("\n".join(f"- [{c}] {t}" for c, t in plan.facts if t and c))
    rels = [Relation(relation_type=r, target=t) for r, t in plan.links]
    tags = list(plan.tags)
    meta = dict(metadata)
    meta["ingest_hash"] = _fingerprint(plan.body, facts, rels)
    if old is not None:
        old_body = extract_body(old.content, old.title)
        prev = str(old.metadata.get("ingest_hash") or "")
        if prev and _fingerprint(old_body, old.observations, old.relations) != prev:
            return "kept"
        # user-added tags and metadata keys are kept
        for t in old.tags:
            if t not in tags:
                tags.append(t)
        meta = {**old.metadata, **meta}
        same = (
            _squash(old_body) == _squash(plan.body)
            and old.title == plan.title
            and {(o.category, o.content) for o in old.observations} == {(o.category, o.content) for o in facts}
            and {(r.relation_type, r.target) for r in old.relations} == {(r.relation_type, r.target) for r in rels}
            and set(old.tags) == set(tags)
            and all(str(old.metadata.get(k)) == str(v) for k, v in meta.items() if k != "ingested")
        )
        if same:
            return "unchanged"
    if not may_write(budget):
        raise Cancelled()
    # 2026-10-25 (KB phase 6): an import is a background writer — one note at a
    # time under the base's slot of the shared write budget
    from myclaw.knowledge import budget as write_budget

    with write_budget.background(base, "import", write_budget.PRIO_INGEST, timeout=60):
        write_note(name=plan.permalink, title=plan.title, content=plan.body, observations=facts,
                   relations=rels, tags=tags, user_id=base, metadata=meta)
    return "created" if old is None else "updated"


class Cancelled(Exception):
    """The run was cancelled: no more notes are written."""


# ── continuation state (which parts an unfinished run already wrote) ─────────
def _state_path(base: str, source: str):
    from myclaw.knowledge.storage import get_knowledge_dir

    return get_knowledge_dir(base) / ".ingest" / f"{short_hash(source, 16)}.json"


def load_state(base: str, source: str) -> Dict[str, object]:
    import json

    try:
        data = json.loads(_state_path(base, source).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("source") == source else {}
    except (OSError, ValueError):
        return {}


def earlier_parts(base: str, source: str) -> Dict[str, Dict[str, object]]:
    """Parts written by an unfinished earlier run of ``source`` ({} if none)."""
    state = load_state(base, source)
    done = state.get("done") if state and not state.get("complete", True) else None
    return dict(done) if isinstance(done, dict) else {}


def save_state(base: str, source: str, done: Dict[str, Dict[str, object]], complete: bool) -> None:
    import json

    path = _state_path(base, source)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"source": source, "complete": complete, "done": done}),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as e:  # only continuation is lost
        logger.info("Ingest: could not save the progress of %s: %s", source, e)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def write_document_notes(doc: Document, base: str = "default", max_notes: int = 60,
                         budget: Optional[Budget] = None, max_chars: int = MAX_NOTE_CHARS,
                         result: Optional[IngestResult] = None,
                         earlier: Optional[Dict[str, Dict[str, object]]] = None,
                         track: bool = True) -> IngestResult:
    """Write ``doc`` as an overview note plus linked part notes.

    The overview is written last (it lists what was written). When the time
    budget runs out, the remaining parts are skipped and the overview still
    written; ``result.stopped`` says why. After a cancel nothing more is
    written. ``earlier`` / ``track``: parts of an unfinished earlier run are
    kept and the next batch is taken; the progress is saved per source, so
    "run it again" continues (2026-10-24, KB phase 5 review).
    """
    from .safety import clean_source

    doc.source = clean_source(doc.source)  # never store credentials of a URL
    budget = budget or Budget(max_notes=max_notes, seconds=None)
    max_notes = max(1, min(int(max_notes), budget.max_notes - budget.notes_written))
    result = result or IngestResult(base=base, source=doc.source, kind=doc.kind)
    result.source = doc.source
    if earlier is None:
        earlier = earlier_parts(base, doc.source) if track else {}
    overview, parts, left_out, diagram = plan_document(doc, max_notes, max_chars, earlier)
    result.diagram = diagram
    result.errors.extend(doc.errors)
    result.skipped.extend(doc.skipped)
    if doc.stopped:
        result.stopped = doc.stopped
    if left_out:
        result.skipped.extend(f"section not imported (note limit): {s}" for s in left_out)
        result.stopped = result.stopped or f"note limit ({max_notes})"
    if doc.metadata.get("files_left"):
        result.stopped = result.stopped or f"note limit ({max_notes})"
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    meta_base: Dict[str, object] = {"source": doc.source, "ingest_kind": doc.kind,
                                    "ingested": stamp}
    for k, v in (doc.metadata or {}).items():
        if isinstance(v, (str, int, float, bool)) and re.match(r"^[A-Za-z_][\w-]{0,40}$", str(k)):
            meta_base.setdefault(str(k), clean_fact(str(v), 200) if isinstance(v, str) else v)

    # names: a permalink that belongs to someone else's note gets "-imported"
    renamed: Dict[str, str] = {}
    taken: set = set()
    for plan in [overview, *parts]:
        new = claim_permalink(plan.permalink, base, meta_base, taken)
        taken.add(new)
        if new != plan.permalink:
            renamed[plan.permalink] = new
    if renamed:
        for plan in [overview, *parts]:
            plan.permalink = renamed.get(plan.permalink, plan.permalink)
            plan.links = [(r, renamed.get(t, t)) for r, t in plan.links]
            for a, b in renamed.items():
                plan.body = plan.body.replace(f"[[{a}]]", f"[[{b}]]")
        result.skipped.append(f"{len(renamed)} notes written under another name because one of "
                              "your notes already has the name: "
                              + ", ".join(f"{a} → {b}" for a, b in list(renamed.items())[:5]))

    written_parts: List[_Plan] = []
    done: Dict[str, Dict[str, object]] = dict(earlier)
    kept = 0
    for i, plan in enumerate(parts):
        if not may_write(budget) or budget.out_of_time(margin=3.0):
            result.stopped = budget.stop_reason() or "time limit reached"
            result.skipped.append(f"{len(parts) - i} part note(s) not written ({result.stopped})")
            break
        try:
            action = _write(plan, base, {**meta_base, "part_of": overview.permalink}, budget)
        except Cancelled:
            result.stopped = "cancelled"
            break
        except Exception as e:  # one bad note must not stop the others
            logger.warning("Ingest: could not write note %s: %s", plan.permalink, e)
            result.errors.append(f"could not write {plan.title}: {e}")
            continue
        if action == "kept":
            kept += 1
        budget.notes_written += 1
        written_parts.append(plan)
        result.notes.append(NoteRef(plan.permalink, plan.title, action))
        info = done.setdefault(plan.key, {"perm": plan.permalink, "heading": plan.heading,
                                          "parts": 0})
        info["parts"] = int(info.get("parts") or 0) + 1  # type: ignore[call-overload]
        budget.report(f"{len(written_parts)}/{len(parts)} notes written")
    if kept:
        result.skipped.append(f"kept your edits: {kept} notes were changed by hand and were not "
                              "updated (move or delete them to get the new version)")
    if len(written_parts) < len(parts):
        # list only the notes that exist
        exist = {p.permalink for p in written_parts} | {str(v.get("perm")) for v in earlier.values()}
        overview.body = "\n".join(
            line for line in overview.body.split("\n")
            if not re.match(r"^\d+\. \[\[([^\]]+)\]\]", line)
            or re.match(r"^\d+\. \[\[([^\]]+)\]\]", line).group(1) in exist)  # type: ignore[union-attr]
        overview.body += f"\n\nStopped early ({result.stopped}); run it again to continue."
    elif result.stopped:
        overview.body += (f"\n\nNot everything was read ({result.stopped}); run it again to read "
                          "the next part.")
    if not may_write(budget):
        result.stopped = "cancelled"
        return result  # nothing is written after a cancel
    try:
        action = _write(overview, base, meta_base, budget)
    except Cancelled:
        result.stopped = "cancelled"
        return result
    budget.notes_written += 1
    ref = NoteRef(overview.permalink, overview.title, action)
    result.notes.insert(0, ref)
    result.overview = ref
    if track:
        save_state(base, doc.source, done, complete=not result.stopped)
    return result
