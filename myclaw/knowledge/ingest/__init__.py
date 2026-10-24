"""Ingestion into the knowledge base (KB phase 5, 2026-10-24).

* :func:`assimilate` — a web page, a GitHub repository, a local file or
  folder (PDF / Word / Excel / text / code / images) → an overview note plus
  linked part notes and a Mermaid diagram.
* :func:`import_file` — ChatGPT and Claude.ai exports, MCP memory files,
  Markdown folders / Obsidian vaults, single documents.
* :func:`write_document_notes` — the shared step: :class:`Document` → notes.

Notes are written only through ``myclaw.knowledge.storage.write_note``; the
Markdown folder import copies files into ``<base>/imported/<name>/`` and runs
the sync. Every run is logged in ``~/.myclaw/logs/knowledge_ingest.jsonl``.
"""

from .model import (
    Budget, Document, IngestError, IngestResult, MissingDependency, NoteRef, Section,
)
from .notes import write_document_notes
from .run import assimilate, import_file, run_bounded

__all__ = [
    "Budget", "Document", "IngestError", "IngestResult", "MissingDependency", "NoteRef",
    "Section", "assimilate", "import_file", "run_bounded", "write_document_notes",
]
