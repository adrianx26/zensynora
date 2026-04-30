"""Backend factory — pick a vector backend by name + config.

Used by callers that don't want to import every concrete backend just to
honor a config string. The factory resolves the name lazily so an
unselected backend's optional deps don't have to be installed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .base import VectorBackend

logger = logging.getLogger(__name__)


def make_backend(
    name: str,
    config: Optional[Dict[str, Any]] = None,
) -> VectorBackend:
    """Construct a backend from a name and config dict.

    ``name`` is one of ``"memory"``, ``"sqlite"``, ``"qdrant"``. ``config``
    is forwarded to the backend constructor — see each backend's ``__init__``
    for accepted keys.

    Falls back to ``SQLiteBackend`` if Qdrant is requested but its client
    isn't installed; the caller sees a logged warning, not a runtime crash.
    """
    name = (name or "sqlite").lower().strip()
    cfg = dict(config or {})

    if name == "memory":
        from .memory import InMemoryBackend
        return InMemoryBackend()

    if name == "sqlite":
        from .sqlite_backend import SQLiteBackend
        db_path = cfg.get("db_path") or (Path.home() / ".myclaw" / "vectors.db")
        return SQLiteBackend(db_path=Path(db_path), table=cfg.get("table", "vectors"))

    if name == "qdrant":
        from .qdrant_backend import QdrantBackend, is_qdrant_available
        if not is_qdrant_available():
            logger.warning(
                "qdrant-client not installed; falling back to SQLiteBackend. "
                "Install with `pip install qdrant-client` to enable Qdrant."
            )
            from .sqlite_backend import SQLiteBackend
            db_path = cfg.get("fallback_db_path") or (Path.home() / ".myclaw" / "vectors.db")
            return SQLiteBackend(db_path=Path(db_path))
        return QdrantBackend(
            collection=cfg.get("collection", "zensynora"),
            url=cfg.get("url", "http://localhost:6333"),
            api_key=cfg.get("api_key"),
            vector_size=cfg.get("vector_size", 1536),
            distance=cfg.get("distance", "Cosine"),
        )

    raise ValueError(
        f"Unknown vector backend: {name!r}. Choose one of: memory, sqlite, qdrant."
    )
