"""Give knowledge notes written as plain text a proper note structure (2026-10-23).

Until 2026-10-23 ``write_note(content=…)`` stored only the raw text: no
frontmatter (title, tags, dates), and tags / facts / links passed with it were
dropped. This migration rewrites every such note in ``~/.myclaw/knowledge/*/``
as frontmatter + ``# Title`` + text, turns ``- [category] text`` and
``- relation [[Note]]`` lines already in the text into facts and links, and
fixes literal ``\\n`` sequences. Nothing is removed: the full text stays in the
note, and the ``.trash`` folders are left alone. Notes that already start with
frontmatter are not touched, so running it twice changes nothing.
"""

import time
from datetime import datetime
from pathlib import Path

ID = "0002_knowledge_note_structure"
DESCRIPTION = "Add frontmatter/title to plain-text knowledge notes; keep their text"
REVERSIBLE = True  # only adds structure around the existing text


def _title(path: Path, text: str) -> str:
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    if first.startswith("# "):
        return first[2:].strip()[:120]
    words = path.stem.replace("-", " ").replace("_", " ").strip()
    return (words[:1].upper() + words[1:])[:120] or path.stem


def convert(path: Path) -> bool:
    """Rewrite one note if it has no frontmatter. Returns True when changed."""
    from myclaw.knowledge.parser import Note, generate_markdown, parse_observations, parse_relations
    from myclaw.knowledge.storage import normalize_text

    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.lstrip().startswith("---"):
        return False
    body = normalize_text(raw)
    title = _title(path, body)
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    note = Note(name=title, permalink=path.stem, title=title, content=body, tags=[],
                created_at=mtime, updated_at=mtime,
                observations=parse_observations(body), relations=parse_relations(body), file_path=path)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(generate_markdown(note), encoding="utf-8")
    tmp.replace(path)
    return True


def apply(ctx):
    root = ctx.data_dir / "knowledge"
    if not root.is_dir():
        return
    total = 0
    for base in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        changed = 0
        for f in sorted(base.glob("*.md")):
            try:
                if convert(f):
                    changed += 1
            except Exception as e:  # one unreadable note must not stop the update
                ctx.log.warning("  knowledge note %s left as it is: %s", f.name, e)
        if changed:
            total += changed
            ctx.log.info("  knowledge/%s: %d note(s) given a title and frontmatter", base.name, changed)
            try:  # refresh the search index for the rewritten files
                from myclaw.knowledge.sync import sync_knowledge

                sync_knowledge(base.name, force=True)
            except Exception as e:
                ctx.log.warning("  could not re-index knowledge/%s (run: zensynora knowledge sync): %s",
                                base.name, e)
    if total:
        ctx.log.info("  %d knowledge note(s) converted at %s", total, time.strftime("%H:%M:%S"))
