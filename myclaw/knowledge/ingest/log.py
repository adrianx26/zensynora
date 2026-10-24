"""Ingestion log: one JSON line per run in ``~/.myclaw/logs/knowledge_ingest.jsonl``
(KB phase 5, 2026-10-24) — time, base, source, kind, notes created/updated,
errors, duration. The doctor can read it with :func:`read_recent`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from .model import IngestResult

logger = logging.getLogger(__name__)
MAX_LOG_BYTES = 5_000_000   # then the file is moved to .1 (one old file is kept)
_lock = threading.Lock()


def log_path() -> Path:
    return Path.home() / ".myclaw" / "logs" / "knowledge_ingest.jsonl"


def append(result: IngestResult, error: str = "") -> None:
    """Add one line for a finished (or failed) run. Never raises."""
    entry: Dict[str, Any] = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base": result.base, "source": result.source, "kind": result.kind,
        "created": result.count("created"), "updated": result.count("updated"),
        "unchanged": result.count("unchanged"),
        "overview": result.overview.permalink if result.overview else None,
        "errors": ([error] if error else []) + list(result.errors)[:20],
        "skipped": len(result.skipped), "stopped": result.stopped,
        "duration": round(result.duration, 2),
    }
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                path.replace(path.with_suffix(".jsonl.1"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Knowledge import: could not write the import log: %s", e)


def read_recent(limit: int = 50) -> List[Dict[str, Any]]:
    """The last ``limit`` runs, newest last (broken lines are skipped)."""
    path = log_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
