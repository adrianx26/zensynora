"""Versioned prompt template registry.

The framework was hard-coding prompts inline (e.g. inside
``Agent._build_context``). That made it impossible to A/B variants, audit
prompt changes, or share templates across agents.

This module gives prompts a name, a version, free-form metadata, and a
file-backed JSONL store at ``~/.myclaw/prompts.jsonl`` (one record per
line — easy to diff, easy to grep). The newest version wins by default.

Rendering uses **Jinja2** when installed (sandboxed environment, autoescape
off — these are LLM prompts, not HTML), and falls back to
``string.Template`` for dollar-sign substitution otherwise.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PROMPTS_PATH = Path.home() / ".myclaw" / "prompts.jsonl"

# ── Optional dependency: Jinja2 ───────────────────────────────────────────

try:  # pragma: no cover - import guard
    from jinja2 import Environment, BaseLoader, StrictUndefined, TemplateError, meta as _jinja_meta

    _JINJA_AVAILABLE = True
    _JINJA_ENV = Environment(
        loader=BaseLoader(),
        undefined=StrictUndefined,
        autoescape=False,  # LLM prompts; HTML escaping would corrupt them.
        keep_trailing_newline=True,
    )
except Exception:
    Environment = BaseLoader = StrictUndefined = TemplateError = None  # type: ignore[assignment]
    _jinja_meta = None  # type: ignore[assignment]
    _JINJA_AVAILABLE = False
    _JINJA_ENV = None


@dataclass
class PromptTemplate:
    """A versioned prompt template.

    ``body`` is the raw template string. ``variables`` lists the names the
    template expects; if empty, they are inferred from the body when
    Jinja2 is available.
    """
    name: str
    version: int
    body: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    variables: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptTemplate":
        # Tolerate forward-compatible extra keys.
        return cls(
            name=data["name"],
            version=int(data["version"]),
            body=data["body"],
            description=data.get("description", ""),
            tags=list(data.get("tags", [])),
            variables=list(data.get("variables", [])),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )

    def render(self, **kwargs: Any) -> str:
        """Render the template with the supplied variables.

        Raises:
            KeyError: A required variable was not provided.
            ValueError: Template has a syntax error.
        """
        if _JINJA_AVAILABLE:
            try:
                tpl = _JINJA_ENV.from_string(self.body)
                return tpl.render(**kwargs)
            except TemplateError as e:  # pragma: no cover - depends on jinja
                raise ValueError(f"Template error in {self.name}@{self.version}: {e}") from e
        # Fallback: stdlib string.Template ($var / ${var}). Strict on missing.
        try:
            return Template(self.body).substitute(kwargs)
        except KeyError as e:
            raise KeyError(
                f"Missing variable {e} for template {self.name}@{self.version}. "
                f"Install jinja2 for richer template syntax."
            ) from e

    def detect_variables(self) -> List[str]:
        """Return the variable names the template references.

        Uses Jinja2's AST when available, otherwise a simple ``$var`` scan.
        """
        if _JINJA_AVAILABLE:
            try:
                ast = _JINJA_ENV.parse(self.body)
                return sorted(_jinja_meta.find_undeclared_variables(ast))
            except Exception:
                return list(self.variables)
        # Fallback heuristic: collect $name and ${name} tokens.
        import re
        return sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", self.body)))


class PromptRegistry:
    """Append-only, file-backed registry of prompt templates.

    The on-disk format is JSONL: each line is one ``PromptTemplate`` record.
    Updating a prompt creates a new line with an incremented version; the
    history is preserved for audit and rollback. ``get`` returns the
    latest version unless a specific version is requested.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or DEFAULT_PROMPTS_PATH
        # In-memory index: name -> {version -> template}
        self._by_name: Dict[str, Dict[int, PromptTemplate]] = {}
        self._lock = threading.RLock()
        self._loaded = False

    # ── persistence ────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.path.exists():
                try:
                    for line in self.path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            tpl = PromptTemplate.from_dict(data)
                            self._by_name.setdefault(tpl.name, {})[tpl.version] = tpl
                        except Exception as parse_err:
                            logger.warning(
                                "Skipping malformed prompt record",
                                exc_info=parse_err,
                            )
                except Exception as read_err:
                    logger.warning("Failed to read prompt registry", exc_info=read_err)
            self._loaded = True

    def _append_line(self, tpl: PromptTemplate) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(tpl.to_dict(), ensure_ascii=False) + "\n")

    # ── public API ─────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        body: str,
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> PromptTemplate:
        """Register a new version of a prompt. Returns the stored template.

        If a prompt with this name already exists, the new record gets
        ``max_existing_version + 1``; otherwise it starts at 1. The body
        is also auto-scanned for variables when Jinja2 is available.
        """
        if not name or not name.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError(f"Invalid prompt name: {name!r}")
        self._ensure_loaded()
        with self._lock:
            existing = self._by_name.get(name, {})
            version = (max(existing.keys()) + 1) if existing else 1
            tpl = PromptTemplate(
                name=name,
                version=version,
                body=body,
                description=description,
                tags=list(tags or []),
            )
            tpl.variables = tpl.detect_variables()
            self._by_name.setdefault(name, {})[version] = tpl
            self._append_line(tpl)
            return tpl

    def get(self, name: str, version: Optional[int] = None) -> Optional[PromptTemplate]:
        """Return a template by name. ``version=None`` returns the latest."""
        self._ensure_loaded()
        versions = self._by_name.get(name)
        if not versions:
            return None
        if version is None:
            return versions[max(versions.keys())]
        return versions.get(version)

    def render(self, name: str, version: Optional[int] = None, **kwargs: Any) -> str:
        tpl = self.get(name, version)
        if tpl is None:
            raise KeyError(f"No prompt named {name!r} (version={version})")
        return tpl.render(**kwargs)

    def list_names(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self._by_name.keys())

    def list_versions(self, name: str) -> List[int]:
        self._ensure_loaded()
        return sorted(self._by_name.get(name, {}).keys())

    def all_latest(self) -> List[PromptTemplate]:
        self._ensure_loaded()
        return [v[max(v.keys())] for v in self._by_name.values() if v]


# ── Module-level singleton ───────────────────────────────────────────────

_default_registry: Optional[PromptRegistry] = None
_singleton_lock = threading.Lock()


def get_registry(path: Optional[Path] = None) -> PromptRegistry:
    """Return the process-wide registry, creating it on first call."""
    global _default_registry
    if _default_registry is not None and path is None:
        return _default_registry
    with _singleton_lock:
        if path is not None:
            return PromptRegistry(path=path)
        if _default_registry is None:
            _default_registry = PromptRegistry()
        return _default_registry
