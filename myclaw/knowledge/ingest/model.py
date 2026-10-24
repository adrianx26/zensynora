"""Data model of the ingestion pipeline (KB phase 5, 2026-10-24).

Every source (web page, GitHub repository, folder, PDF/DOCX/XLSX, chat
export…) is first turned into a :class:`Document`; :mod:`.notes` then writes
it as linked notes. :class:`Budget` carries the limits of one run (notes,
bytes, time) and the cancel flag, so long work stops by itself and returns
what it has done so far.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Limits (defaults; every entry point takes them as arguments).
MAX_NOTES = 60            # notes written by one assimilate run
MAX_NOTE_CHARS = 12_000   # text of one note; longer sections are split
MAX_TOTAL_BYTES = 8_000_000  # text read from the source in one run
MAX_SECONDS = 120.0       # one run (agent tool); CLI / Web UI jobs allow more


class IngestError(Exception):
    """A problem with the input (bad URL, path not allowed, unknown format).

    The message is shown to the user / agent as it is, so it must say what
    is wrong and what to do.
    """


class MissingDependency(IngestError):
    """An optional reader (pypdf, python-docx, openpyxl) is not installed."""

    def __init__(self, what: str, package: str):
        self.package = package
        super().__init__(
            f"{what} support is not installed. Install it with: "
            f"{sys.executable} -m pip install '{package}' "
            f"(or pip install 'zensynora[ingest]' for PDF, Word and Excel support), "
            f"then try again.")


@dataclass
class Section:
    """One part of a document: a chapter, a page range, a file of a repository."""
    heading: str
    text: str
    key: str = ""                 # stable id inside the document (file path, anchor…)
    links: List[Tuple[str, str]] = field(default_factory=list)  # (relation, key of another section)
    tags: List[str] = field(default_factory=list)
    facts: List[Tuple[str, str]] = field(default_factory=list)  # (category, text)


SectionLike = Union[Section, Tuple[str, str]]


@dataclass
class Document:
    """Content extracted from one source, ready to be written as notes."""
    title: str
    source: str                   # URL or path (shown in the notes, used for stable names)
    kind: str                     # url, github, folder, pdf, docx, xlsx, text, markdown, …
    sections: List[SectionLike] = field(default_factory=list)
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    links: List[str] = field(default_factory=list)       # outgoing URLs
    facts: List[Tuple[str, str]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    diagram: str = ""             # Mermaid code (no fences); generated when empty
    size: int = 0                 # bytes of the source
    date: str = ""                # date of the source (modified / published), if known
    errors: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # files left out (secrets, binaries, limits)
    permalink_hint: str = ""      # readable start of the permalinks (e.g. "github-owner-repo")
    split_all: bool = False       # every section gets its own note (files of a repository)
    stopped: str = ""             # reading stopped early (time / size limit)

    def norm_sections(self) -> List[Section]:
        """Sections as :class:`Section` objects ((heading, text) tuples accepted)."""
        out: List[Section] = []
        for s in self.sections:
            if isinstance(s, Section):
                out.append(s)
            else:
                heading, text = s
                out.append(Section(heading=str(heading), text=str(text)))
        return out


@dataclass
class NoteRef:
    permalink: str
    title: str
    action: str   # created | updated | unchanged


@dataclass
class IngestResult:
    """What one run did (returned by every entry point, logged as one line)."""
    base: str
    source: str
    kind: str
    notes: List[NoteRef] = field(default_factory=list)
    overview: Optional[NoteRef] = None
    diagram: str = ""
    errors: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    stopped: str = ""             # why the run stopped early ("time limit", "note limit"…)
    duration: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def count(self, action: str) -> int:
        return sum(1 for n in self.notes if n.action == action)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base": self.base, "source": self.source, "kind": self.kind,
            "created": self.count("created"), "updated": self.count("updated"),
            "unchanged": self.count("unchanged"),
            "notes": [{"permalink": n.permalink, "title": n.title, "action": n.action}
                      for n in self.notes],
            "overview": ({"permalink": self.overview.permalink, "title": self.overview.title}
                         if self.overview else None),
            "diagram": self.diagram, "errors": list(self.errors),
            "skipped": list(self.skipped), "stopped": self.stopped,
            "duration": round(self.duration, 2), **self.extra,
        }


class Budget:
    """Limits of one run: notes, bytes read, time; plus a cancel flag.

    ``progress`` (optional) is called with a short text after each step (the
    Web UI job shows it).
    """

    def __init__(self, max_notes: int = MAX_NOTES, max_bytes: int = MAX_TOTAL_BYTES,
                 seconds: Optional[float] = MAX_SECONDS,
                 progress: Optional[Callable[[str], None]] = None):
        self.max_notes = max(1, int(max_notes))
        self.max_bytes = max(1, int(max_bytes))
        self.deadline = (time.monotonic() + float(seconds)) if seconds else None
        self.cancel = threading.Event()
        self.bytes_read = 0
        self.notes_written = 0
        self._progress = progress
        self.started = time.monotonic()

    def time_left(self) -> float:
        if self.deadline is None:
            return float("inf")
        return self.deadline - time.monotonic()

    def out_of_time(self, margin: float = 0.0) -> bool:
        return self.cancel.is_set() or self.time_left() <= margin

    def stop_reason(self) -> str:
        if self.cancel.is_set():
            return "cancelled or time limit reached"
        if self.time_left() <= 0:
            return "time limit reached"
        return ""

    def take_bytes(self, n: int) -> bool:
        """Count ``n`` bytes read; False when the byte limit is used up."""
        if self.bytes_read + n > self.max_bytes:
            return False
        self.bytes_read += n
        return True

    def report(self, text: str) -> None:
        if self._progress:
            try:
                self._progress(text)
            except Exception:  # a progress display must never stop the work
                pass
