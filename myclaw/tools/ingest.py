"""
Tools — Knowledge base ingestion (KB phase 5, 2026-10-24)

``assimilate`` reads a web page, a GitHub repository or a local file/folder
into linked notes plus a Mermaid diagram in one call ("analyze this repo and
make a diagram"); ``import_knowledge`` imports ChatGPT / Claude.ai exports, MCP
memory files, Markdown folders (Obsidian vaults) and single documents. Both
use the knowledge base of the current turn (like the other knowledge tools),
run in a worker thread and stop at a time limit with the partial result.
"""

# No "from __future__ import annotations": the tool schemas compare the real
# annotation types (int → "integer").
import functools
import inspect
import logging
import threading
from typing import Callable, Optional, Tuple

from ..knowledge.ingest import Budget, IngestResult, assimilate as _assimilate
from ..knowledge.ingest import import_file as _import_file
from ..knowledge.ingest import run_bounded
from ..knowledge.ingest.run import format_summary
from ..knowledge.ingest.importers import KINDS, MAX_IMPORT_NOTES
from ..knowledge.ingest.safety import clean_source

logger = logging.getLogger(__name__)

# One tool call may take this long (the agent's task limit,
# timeouts.task_seconds, is 600 s by default — this stays well below it).
TOOL_SECONDS = 120.0
# 2026-10-24 (KB phase 5, review): at most this many ingestion runs from agent
# tools at a time, and never two for the same source (a run that outlived its
# tool call keeps its slot until it has stopped).
MAX_PARALLEL = 2
_active: set = set()
_active_lock = threading.Lock()


def _bounded(key: str, work: Callable[[Budget], IngestResult], budget: Budget
             ) -> Optional[Tuple[Optional[IngestResult], str]]:
    """``run_bounded`` holding a slot for ``key``; None (not run) when busy.

    The slot is freed by the worker thread when the work has really ended — a
    run that is still stopping after the tool returned keeps it.
    """
    with _active_lock:
        if key in _active or len(_active) >= MAX_PARALLEL:
            return None
        _active.add(key)

    def guarded(b: Budget) -> IngestResult:
        try:
            return work(b)
        finally:
            with _active_lock:
                _active.discard(key)

    try:
        return run_bounded(guarded, budget, TOOL_SECONDS)
    except Exception:  # the thread could not start
        with _active_lock:
            _active.discard(key)
        raise


def _busy_message(what: str) -> str:
    return (f"Not started: an import of {what} is still running, or {MAX_PARALLEL} imports are "
            "running already. Wait until they finish (their notes appear in the knowledge base), "
            "then try again.")


def _scoped(fn):
    """The knowledge base of the current turn, as in tools/kb.py."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        import time

        from ..knowledge import usage
        from ..knowledge.scope import for_tool

        bound = sig.bind_partial(*args, **kwargs)
        bound.arguments["user_id"] = for_tool(bound.arguments.get("user_id"))
        if not usage.enabled():
            return fn(*bound.args, **bound.kwargs)
        # 2026-10-27: usage log (agent writes from imports; never raises)
        t0 = time.perf_counter()
        out = fn(*bound.args, **bound.kwargs)
        usage.record_tool(fn.__name__, dict(bound.arguments), out,
                          (time.perf_counter() - t0) * 1000.0, str(bound.arguments["user_id"]))
        return out

    return wrapper


def assimilate(source: str, max_notes: int = 40, user_id: str = "default") -> str:
    """
    Read a web page, a GitHub repository or a local file/folder into the knowledge
    base as linked notes, with a Mermaid diagram of its structure — in one call.

    Use it for "analyze this repo / site / document and make a diagram", or to
    remember a document. Re-running it on the same source updates the notes.

    source: a URL (https://…), a GitHub repository URL (https://github.com/owner/repo,
            also …/tree/branch/folder), or a file/folder path (relative paths are in the
            agent workspace; ~/… is the home folder). PDF, Word, Excel, text, Markdown,
            HTML, CSV, JSON, code and images are supported.
    max_notes: at most this many notes (default 40, at most 500)
    user_id: knowledge base (set automatically)
    """
    try:
        n = max(1, min(int(max_notes), 500))
    except (TypeError, ValueError):
        n = 40
    shown = clean_source(str(source or ""))

    got = _bounded(f"{user_id}:{shown}",
                   lambda b: _assimilate(source, base=user_id, max_notes=n, budget=b),
                   Budget(max_notes=n, seconds=TOOL_SECONDS - 10))
    if got is None:
        return _busy_message(f"'{shown}'")
    result, error = got
    if result is None:
        logger.info("assimilate %s: %s", shown, error)
        return _assimilate_failed(shown, error)
    return format_summary("Assimilated", f"'{shown}'", result)


def _assimilate_failed(shown: str, error: str) -> str:
    return (f"Could not assimilate '{shown}': {error}\n"
                "Tips: a web page must be public; for a GitHub repository use "
                "https://github.com/owner/repo; local paths must be in your home folder or the "
                "agent workspace (not in hidden folders).")


def import_knowledge(path: str, kind: str = "auto", user_id: str = "default") -> str:
    """
    Import an export or a folder of notes into the knowledge base.

    path: the file or folder (relative paths are in the agent workspace; ~/… is the home
          folder): a ChatGPT export (.zip or conversations.json), a Claude.ai export
          (.zip, conversations.json or projects.json), an MCP memory file (memory.json /
          .jsonl), a folder or .zip of Markdown notes (Obsidian vault), or one document
    kind: auto (detect), chatgpt, claude, mcp_memory, markdown or document
    user_id: knowledge base (set automatically)
    """
    k = (kind or "auto").strip().lower()
    shown = clean_source(str(path or ""))

    got = _bounded(f"{user_id}:{shown}",
                   lambda b: _import_file(path, base=user_id, kind=k, max_notes=MAX_IMPORT_NOTES,
                                          budget=b),
                   Budget(max_notes=MAX_IMPORT_NOTES, max_bytes=2_000_000_000,
                          seconds=TOOL_SECONDS - 10))
    if got is None:
        return _busy_message(f"'{shown}'")
    result, error = got
    if result is None:
        logger.info("import_knowledge %s: %s", shown, error)
        return _import_failed(shown, error)
    return format_summary("Imported", f"'{shown}' ({result.kind})", result, with_diagram=False)


def _import_failed(shown: str, error: str) -> str:
    return (f"Could not import '{shown}': {error}\n"
                f"Kinds: {', '.join(KINDS)}. Large exports can also be uploaded on the Knowledge "
                "page (Import) or imported with: zensynora knowledge import FILE")


assimilate = _scoped(assimilate)
import_knowledge = _scoped(import_knowledge)
