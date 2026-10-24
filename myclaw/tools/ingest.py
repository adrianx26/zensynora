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

from ..knowledge.ingest import Budget, assimilate as _assimilate
from ..knowledge.ingest import import_file as _import_file
from ..knowledge.ingest import run_bounded
from ..knowledge.ingest.run import format_summary
from ..knowledge.ingest.importers import KINDS

logger = logging.getLogger(__name__)

# One tool call may take this long (the agent's task limit,
# timeouts.task_seconds, is 600 s by default — this stays well below it).
TOOL_SECONDS = 120.0


def _scoped(fn):
    """The knowledge base of the current turn, as in tools/kb.py."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from ..knowledge.scope import for_tool

        bound = sig.bind_partial(*args, **kwargs)
        bound.arguments["user_id"] = for_tool(bound.arguments.get("user_id"))
        return fn(*bound.args, **bound.kwargs)

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
    budget = Budget(max_notes=n, seconds=TOOL_SECONDS - 10)
    result, error = run_bounded(lambda b: _assimilate(source, base=user_id, max_notes=n, budget=b),
                                budget, TOOL_SECONDS)
    if result is None:
        logger.info("assimilate %s: %s", source, error)
        return (f"Could not assimilate '{source}': {error}\n"
                "Tips: a web page must be public; for a GitHub repository use "
                "https://github.com/owner/repo; local paths must be in your home folder or the "
                "agent workspace (not in hidden folders).")
    return format_summary("Assimilated", f"'{source}'", result)


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
    budget = Budget(max_notes=2000, max_bytes=2_000_000_000, seconds=TOOL_SECONDS - 10)
    result, error = run_bounded(
        lambda b: _import_file(path, base=user_id, kind=k, max_notes=2000, budget=b),
        budget, TOOL_SECONDS)
    if result is None:
        logger.info("import_knowledge %s: %s", path, error)
        return (f"Could not import '{path}': {error}\n"
                f"Kinds: {', '.join(KINDS)}. Large exports can also be uploaded on the Knowledge "
                "page (Import) or imported with: zensynora knowledge import FILE")
    return format_summary("Imported", f"'{path}' ({result.kind})", result, with_diagram=False)


assimilate = _scoped(assimilate)
import_knowledge = _scoped(import_knowledge)
