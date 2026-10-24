"""Entry points of the ingestion (KB phase 5, 2026-10-24).

:func:`assimilate` (URL, GitHub repository, local file or folder) and
:func:`import_file` (chat exports, MCP memory, Markdown folders, single
documents) run synchronously and return an :class:`IngestResult`; input
problems raise :class:`IngestError` with a message for the user. Every run is
logged (:mod:`.log`). :func:`run_bounded` runs one of them in a worker thread
with a time limit and returns the partial result when the limit is reached
(used by the agent tools).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import log as ingest_log
from .importers import MAX_IMPORT_NOTES, import_export
from .model import Budget, IngestError, IngestResult
from .notes import write_document_notes
from .sources import fetch_github, fetch_url, is_url, local_source, parse_github

logger = logging.getLogger(__name__)

BASE_RE = re.compile(r"^[\w.@-]{1,64}$")
MAX_NOTES_LIMIT = 500
LISTED_NOTES = 12


def check_base(base: str) -> str:
    """A knowledge base name (a plain folder name) or :class:`IngestError`."""
    from myclaw.knowledge.scope import kb_user

    b = (base or "default").strip() or "default"
    if not BASE_RE.match(b) or b.startswith("."):
        raise IngestError(f"'{base}' is not a knowledge base name (letters, digits, '.', '@', '-', '_').")
    return kb_user(b)


def _clamp(n: object, default: int, top: int) -> int:
    try:
        v = int(n)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        v = default
    return max(1, min(v, top))


def assimilate(source: str, base: str = "default", max_notes: int = 40,
               budget: Optional[Budget] = None, trusted: bool = False) -> IngestResult:
    """Read a URL, GitHub repository or local file/folder into linked notes.

    ``trusted`` skips the allowed-roots check for local paths (CLI only).
    """
    source = (source or "").strip().strip('"').strip("'")
    if not source:
        raise IngestError("Give a web address, a GitHub repository URL or a file/folder path.")
    base = check_base(base)
    max_notes = _clamp(max_notes, 40, MAX_NOTES_LIMIT)
    budget = budget or Budget(max_notes=max_notes)
    budget.max_notes = min(budget.max_notes, max_notes)
    result = IngestResult(base=base, source=source, kind="")
    started = time.monotonic()
    error = ""
    try:
        if is_url(source):
            if parse_github(source):
                result.kind = "github"
                budget.report("reading the GitHub repository")
                doc = fetch_github(source, budget, max_files=min(80, max(1, max_notes - 1)))
            else:
                result.kind = "web"
                budget.report("fetching the page")
                doc = fetch_url(source, budget)
        else:
            result.kind = "local"
            doc = local_source(source, budget, max_files=min(80, max(1, max_notes - 1)),
                               trusted=trusted)
        result.kind = doc.kind
        result.source = doc.source
        budget.report("writing notes")
        write_document_notes(doc, base, max_notes=max_notes, budget=budget, result=result)
        return result
    except IngestError as e:
        error = str(e)
        raise
    except Exception as e:  # a fault in ZenSynora: logged with traceback
        error = f"{type(e).__name__}: {e}"
        logger.exception("Knowledge assimilate of %s failed", source)
        raise
    finally:
        result.duration = time.monotonic() - started
        ingest_log.append(result, error)


def import_file(path: str, base: str = "default", kind: str = "auto",
                max_notes: int = MAX_IMPORT_NOTES, budget: Optional[Budget] = None,
                trusted: bool = False, display_name: str = "") -> IngestResult:
    """Import an export file / zip / folder (see :mod:`.importers`).

    ``trusted``: the path is not checked against the allowed roots (CLI, Web
    UI uploads). ``display_name`` is the original file name of an upload.
    """
    from .safety import check_local_path, is_secret_file

    base = check_base(base)
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        raise IngestError("Give the path of the export file (.json / .zip) or of a folder of notes.")
    p = Path(raw).expanduser().resolve() if trusted else check_local_path(raw)
    if not p.exists():
        raise IngestError(f"'{raw}' does not exist.")
    if p.is_file() and is_secret_file(p.name):
        raise IngestError(f"'{p.name}' looks like a file with keys or passwords; it is never "
                          "imported into the knowledge base.")
    max_notes = _clamp(max_notes, MAX_IMPORT_NOTES, 20_000)
    budget = budget or Budget(max_notes=max_notes, max_bytes=2_000_000_000, seconds=None)
    budget.max_notes = min(budget.max_notes, max_notes)
    result = IngestResult(base=base, source=display_name or str(p), kind=kind)
    started = time.monotonic()
    error = ""
    try:
        import_export(p, base, kind, budget, result, display_name=display_name)
        return result
    except IngestError as e:
        error = str(e)
        raise
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.exception("Knowledge import of %s failed", raw)
        raise
    finally:
        result.duration = time.monotonic() - started
        ingest_log.append(result, error)


def run_bounded(work: Callable[[Budget], IngestResult], budget: Budget,
                seconds: float) -> Tuple[Optional[IngestResult], str]:
    """Run ``work(budget)`` in a thread for at most about ``seconds``.

    Returns ``(result, error)``: the result (complete or partial), or None and
    the error message. The budget's own deadline makes the work stop by
    itself; if it still runs after the limit, the cancel flag is set and the
    work goes on in the background until its next check.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = work(budget)
        except IngestError as e:
            box["error"] = str(e)
        except Exception as e:  # already logged by the entry point
            box["error"] = (f"The import failed unexpectedly ({type(e).__name__}: {e}). "
                            "Details are in the log; try again or report it.")

    t = threading.Thread(target=target, name="kb-ingest", daemon=True)
    t.start()
    t.join(seconds + 15)
    if t.is_alive():
        budget.cancel.set()
        t.join(10)
    if t.is_alive():
        return None, (f"Still working after {int(seconds)} s; it continues in the background and "
                      "the notes appear when it is done. Check the Knowledge page later.")
    return box.get("result"), box.get("error", "")


def format_summary(verb: str, what: str, r: IngestResult, with_diagram: bool = True) -> str:
    """A short report of a run for the agent, the CLI and the log viewer."""
    created, updated, unchanged = r.count("created"), r.count("updated"), r.count("unchanged")
    lines: List[str] = []
    if not r.notes:
        lines.append(f"⚠️ Nothing was saved from {what}.")
    else:
        lines.append(f"✅ {verb} {what} into the knowledge base '{r.base}': {created} notes created, "
                     f"{updated} updated, {unchanged} unchanged ({r.duration:.0f} s).")
    if r.overview:
        lines.append(f"Overview note: [{r.overview.title}](memory://{r.overview.permalink}) — "
                     f"read it with read_knowledge('{r.overview.permalink}').")
    others = [n for n in r.notes if not r.overview or n.permalink != r.overview.permalink]
    if others:
        lines.append("Notes:")
        lines += [f"  • [{n.title}](memory://{n.permalink}) ({n.action})" for n in others[:LISTED_NOTES]]
        if len(others) > LISTED_NOTES:
            lines.append(f"  • … and {len(others) - LISTED_NOTES} more (search_knowledge or "
                         "list_knowledge finds them)")
    for k, v in r.extra.items():
        if isinstance(v, (int, str)) and v not in (0, ""):
            lines.append(f"{k.replace('_', ' ')}: {v}")
    if r.stopped:
        lines.append(f"Stopped early: {r.stopped}. Run the same call again to continue — "
                     "notes already saved are only updated.")
    if r.skipped:
        lines.append("Left out: " + "; ".join(r.skipped[:6]) + (" …" if len(r.skipped) > 6 else ""))
    if r.errors:
        lines.append("Problems: " + "; ".join(r.errors[:5]) + (" …" if len(r.errors) > 5 else ""))
    if with_diagram and r.diagram:
        lines += ["", "Diagram (Mermaid, also saved in the overview note):", "```mermaid",
                  r.diagram, "```"]
    return "\n".join(lines)
