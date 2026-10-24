"""Read one file into a :class:`Document` (KB phase 5, 2026-10-24).

PDF (pypdf), Word (python-docx), Excel (openpyxl) are optional: without the
library the reader raises :class:`MissingDependency` with the install
command. Text, Markdown, HTML, CSV and JSON need nothing extra. Images give
a note with the file's details (dimensions only when Pillow is installed;
no OCR).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import Budget, Document, IngestError, MissingDependency, Section

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 50_000_000      # a document larger than this is refused
MAX_TEXT_CHARS = 400_000         # text kept from one document
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 30
PAGE_GROUP_CHARS = 10_000        # PDF pages are grouped into sections of about this size
# 2026-10-24 (KB phase 5, review): Word/Excel files are zip archives — their
# unpacked size is checked before parsing (a small "zip bomb" could fill the
# memory); rows, sheets and pages are capped and the time limit is checked.
MAX_OOXML_UNPACKED = 300_000_000
MAX_OOXML_PART = 120_000_000
MAX_XLSX_ROWS_COUNTED = 200_000
MAX_PDF_PAGES = 2_000


def check_ooxml(path: Path) -> None:
    """Refuse a .docx/.xlsx whose unpacked contents are unreasonably large."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as e:
        raise IngestError(f"'{path.name}' is damaged (not a valid Office file).") from e
    total = sum(i.file_size for i in infos)
    biggest = max((i.file_size for i in infos), default=0)
    if total > MAX_OOXML_UNPACKED or biggest > MAX_OOXML_PART:
        raise IngestError(f"'{path.name}' unpacks to {total // 1_000_000} MB, more than the "
                          f"{MAX_OOXML_UNPACKED // 1_000_000} MB that are read; save a smaller copy.")

TEXT_EXT = {".txt", ".text", ".log", ".rst", ".adoc", ".org", ".tex"}
MD_EXT = {".md", ".markdown", ".mdx"}
HTML_EXT = {".html", ".htm", ".xhtml"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".heic"}
CODE_LANG = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".swift": "swift", ".rb": "ruby", ".php": "php",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ps1": "powershell", ".lua": "lua",
    ".sql": "sql", ".r": "r", ".scala": "scala", ".dart": "dart", ".vue": "vue",
    ".svelte": "svelte", ".css": "css", ".scss": "scss", ".toml": "toml", ".yaml": "yaml",
    ".yml": "yaml", ".ini": "ini", ".cfg": "ini", ".xml": "xml", ".gradle": "groovy",
    ".tf": "hcl", ".proto": "protobuf", ".graphql": "graphql", ".ex": "elixir", ".exs": "elixir",
    ".zig": "zig", ".nim": "nim", ".jl": "julia", ".m": "objectivec", ".pl": "perl",
}
DOC_EXT = {".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".tsv", ".json", ".jsonl"}
SUPPORTED = TEXT_EXT | MD_EXT | HTML_EXT | IMAGE_EXT | DOC_EXT | set(CODE_LANG)
SPECIAL_NAMES = {"dockerfile": "dockerfile", "makefile": "makefile", "justfile": "makefile",
                 "procfile": "text", "gemfile": "ruby", "rakefile": "ruby", "license": "text",
                 "readme": "text", "changelog": "text", "contributing": "text", "notice": "text"}


def kind_of(path: Path) -> str:
    """The document kind of a file name ('' = not supported)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".json", ".jsonl"):
        return "json"
    if ext in MD_EXT:
        return "markdown"
    if ext in HTML_EXT:
        return "html"
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT:
        return "text"
    if ext in CODE_LANG or path.name.lower() in SPECIAL_NAMES:
        return "code"
    return ""


def _date(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return ""


def read_text_file(path: Path, limit: int = MAX_TEXT_CHARS) -> str:
    """Text of a file (UTF-8, falling back to Latin-1); refuses binary files."""
    with open(path, "rb") as f:
        raw = f.read(limit * 2 + 1)
    if b"\x00" in raw[:8192]:
        raise IngestError(f"'{path.name}' is a binary file, not text.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text[:limit]


def read_document(path: Path, budget: Optional[Budget] = None, source: str = "",
                  title: str = "") -> Document:
    """Read a supported file into a :class:`Document`.

    ``source`` is what the notes show as the source (default: the path).
    """
    from .safety import is_secret_file

    path = Path(path)
    if not path.is_file():
        raise IngestError(f"'{path}' is not a file.")
    if is_secret_file(path.name):  # 2026-10-24 (KB phase 5, review): every caller
        raise IngestError(f"'{path.name}' looks like a file with keys or passwords; it is never "
                          "imported into the knowledge base.")
    size = path.stat().st_size
    kind = kind_of(path)
    if not kind:
        raise IngestError(
            f"'{path.name}' has a format that cannot be imported ({path.suffix or 'no extension'}). "
            "Supported: PDF, Word (.docx), Excel (.xlsx), text, Markdown, HTML, CSV, JSON, code "
            "files and images.")
    if size > MAX_FILE_BYTES:
        raise IngestError(f"'{path.name}' is {size // 1_000_000} MB; files over "
                          f"{MAX_FILE_BYTES // 1_000_000} MB are not imported.")
    if budget is not None and kind != "image" and not budget.take_bytes(min(size, MAX_TEXT_CHARS * 2)):
        raise IngestError("The size limit of this run is reached; import the rest in another run.")
    doc = Document(title=title or path.stem.replace("_", " ").strip() or path.name,
                   source=source or str(path), kind=kind, size=size, date=_date(path),
                   permalink_hint=f"{kind}-{path.stem}")
    reader = {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "csv": _csv, "json": _json,
              "markdown": _markdown, "html": _html, "image": _image, "text": _text,
              "code": _code}[kind]
    if kind in ("pdf", "docx", "xlsx"):
        reader(path, doc, budget)  # type: ignore[operator]
    else:
        reader(path, doc)  # type: ignore[operator]
    if not title and doc.metadata.get("doc_title"):
        doc.title = str(doc.metadata.pop("doc_title"))
    return doc


# ── text-like ────────────────────────────────────────────────────────────────
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_FRONTMATTER = re.compile(r"^﻿?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def split_markdown(text: str) -> Tuple[str, List[Section], str]:
    """``(title, sections, intro)`` of Markdown: split at '#'/'##' headings.

    Headings inside code fences are ignored; '###' stays inside its section.
    """
    text = _FRONTMATTER.sub("", text, count=1)
    title = ""
    intro: List[str] = []
    sections: List[Section] = []
    current: Optional[Section] = None
    buf: List[str] = []
    fence = False
    levels = [len(m.group(1)) for line in text.split("\n") if (m := _HEADING.match(line))]
    split_level = 2 if levels.count(1) <= 1 else 1
    for line in text.split("\n"):
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
        m = None if fence else _HEADING.match(line)
        if m and len(m.group(1)) == 1 and not title and split_level == 2:
            title = m.group(2).strip()
            continue
        if m and len(m.group(1)) <= split_level:
            if current is None:
                intro = buf
            else:
                current.text = "\n".join(buf).strip()
                sections.append(current)
            heading = m.group(2).strip()
            current = Section(heading=heading, text="", key=heading)
            buf = []
            continue
        buf.append(line)
    if current is None:
        intro = buf
    else:
        current.text = "\n".join(buf).strip()
        sections.append(current)
    return title, sections, "\n".join(intro).strip()


def _markdown(path: Path, doc: Document) -> None:
    text = read_text_file(path)
    title, sections, intro = split_markdown(text)
    if title:
        doc.metadata["doc_title"] = title
    doc.summary = intro
    doc.sections = list(sections) or [Section(heading=doc.title, text=intro, key="text")]
    if not sections:
        doc.summary = ""
    _md_links(text, doc)


_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")


def _md_links(text: str, doc: Document) -> None:
    for url in _MD_LINK.findall(text):
        if url not in doc.links:
            doc.links.append(url)


def _text(path: Path, doc: Document) -> None:
    text = read_text_file(path)
    doc.sections = _paragraph_sections(text, doc.title)


def _paragraph_sections(text: str, title: str) -> List[Section]:
    """Plain text: one section, split later into notes by size."""
    return [Section(heading=title, text=text.strip(), key="text")] if text.strip() else []


def _code(path: Path, doc: Document) -> None:
    text = read_text_file(path)
    lang = CODE_LANG.get(path.suffix.lower()) or SPECIAL_NAMES.get(path.name.lower(), "")
    doc.kind = "code"
    doc.facts.append(("language", lang or "text"))
    doc.sections = [Section(heading=path.name, text=f"```{lang}\n{text.rstrip()}\n```", key=path.name)]


def _html(path: Path, doc: Document) -> None:
    from .html_text import html_to_document

    html = read_text_file(path)
    html_to_document(html, doc, base_url="")


# ── tables ───────────────────────────────────────────────────────────────────
def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M").replace(" 00:00", "")
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s.replace("|", "\\|")[:200]


def markdown_table(rows: List[List[Any]], total_rows: Optional[int] = None) -> str:
    rows = [[_cell(c) for c in r[:MAX_TABLE_COLS]] for r in rows if any(c not in (None, "") for c in r)]
    if not rows:
        return "(empty)"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:MAX_TABLE_ROWS + 1]
    lines = ["| " + " | ".join(h or f"col{i + 1}" for i, h in enumerate(header)) + " |",
             "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    shown = len(body)
    total = total_rows if total_rows is not None else len(rows) - 1
    if total > shown:
        lines.append(f"\n({shown} of {total} rows shown)")
    return "\n".join(lines)


def _table_section(name: str, rows: List[List[Any]], total_rows: int) -> Section:
    header = [_cell(c) for c in (rows[0] if rows else [])][:MAX_TABLE_COLS]
    facts: List[Tuple[str, str]] = []
    if any(header):
        facts.append(("columns", f"{name}: " + ", ".join(h for h in header if h)))
    facts.append(("rows", f"{name}: {total_rows} rows"))
    return Section(heading=name, text=markdown_table(rows, total_rows), key=name, facts=facts)


def _csv(path: Path, doc: Document) -> None:
    text = read_text_file(path)
    delim = "\t" if path.suffix.lower() == ".tsv" else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows: List[List[Any]] = []
    total = -1
    for row in reader:
        total += 1
        if len(rows) <= MAX_TABLE_ROWS:
            rows.append(row)
    doc.sections = [_table_section(doc.title, rows, max(0, total))]


def _json(path: Path, doc: Document) -> None:
    text = read_text_file(path)
    if path.suffix.lower() == ".jsonl":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        doc.facts.append(("records", f"{len(lines)} lines"))
        doc.sections = [Section(heading=doc.title, key="data",
                                text="```json\n" + "\n".join(lines[:300]) + "\n```")]
        return
    try:
        data = json.loads(text)
    except ValueError as e:
        raise IngestError(f"'{path.name}' is not valid JSON ({e}).") from e
    if isinstance(data, dict):
        keys = list(data)[:40]
        doc.facts.append(("keys", ", ".join(str(k) for k in keys)))
    elif isinstance(data, list):
        doc.facts.append(("records", f"{len(data)} items"))
    pretty = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if len(pretty) > 60_000:
        pretty = pretty[:60_000] + "\n… (cut)"
    doc.sections = [Section(heading=doc.title, key="data", text=f"```json\n{pretty}\n```")]


# ── optional readers ─────────────────────────────────────────────────────────
def _pdf(path: Path, doc: Document, budget: Optional[Budget] = None) -> None:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise MissingDependency("PDF", "pypdf>=4.0") from e
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise IngestError(f"'{path.name}' is password-protected; save an unprotected copy "
                                  "and import that.") from e
        info = reader.metadata or {}
        title = str(getattr(info, "title", "") or "").strip()
        if title and len(title) > 3:
            doc.metadata["doc_title"] = title
        author = str(getattr(info, "author", "") or "").strip()
        if author:
            doc.facts.append(("author", author))
        pages = len(reader.pages)
        doc.facts.append(("pages", str(pages)))
        sections: List[Section] = []
        buf: List[str] = []
        start = 1
        total = 0
        for i, page in enumerate(reader.pages, 1):
            if i > MAX_PDF_PAGES or (budget is not None and budget.out_of_time(margin=10.0)):
                doc.skipped.append(f"pages {i}–{pages} not read ({'page limit' if i > MAX_PDF_PAGES else 'time limit'})")
                doc.stopped = "" if i > MAX_PDF_PAGES else (budget.stop_reason() if budget else "")
                if sum(len(b) for b in buf):
                    sections.append(Section(heading=f"Pages {start}–{i - 1}",
                                            text="\n\n".join(b for b in buf if b), key=f"pages-{start}"))
                break
            try:
                text = page.extract_text() or ""
            except Exception as e:  # one broken page must not stop the others
                doc.errors.append(f"page {i}: {e}")
                text = ""
            text = re.sub(r"[ \t]+\n", "\n", text).strip()
            buf.append(text)
            total += len(text)
            if sum(len(b) for b in buf) >= PAGE_GROUP_CHARS or i == pages:
                label = f"Pages {start}–{i}" if i > start else f"Page {i}"
                body = "\n\n".join(b for b in buf if b)
                if body:
                    sections.append(Section(heading=label, text=body, key=f"pages-{start}"))
                buf, start = [], i + 1
            if total >= MAX_TEXT_CHARS:
                doc.skipped.append(f"pages after {i} (text limit)")
                break
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"Could not read the PDF '{path.name}': {e}.") from e
    if not sections:
        doc.errors.append("no text found — a scanned PDF needs OCR, which is not supported")
    doc.sections = sections


def _docx(path: Path, doc: Document, budget: Optional[Budget] = None) -> None:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise MissingDependency("Word (.docx)", "python-docx>=1.1") from e
    check_ooxml(path)
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise IngestError(f"Could not read the Word file '{path.name}': {e}.") from e
    props = d.core_properties
    if (props.title or "").strip():
        doc.metadata["doc_title"] = props.title.strip()
    if (props.author or "").strip():
        doc.facts.append(("author", props.author.strip()))
    sections: List[Section] = []
    intro: List[str] = []
    current: Optional[Section] = None
    buf: List[str] = []
    for block in _docx_blocks(d):
        if isinstance(block, tuple):  # (level, heading)
            level, heading = block
            if level == 0 and "doc_title" not in doc.metadata:
                doc.metadata["doc_title"] = heading
                continue
            if level <= 2:
                if current is None:
                    intro = buf
                else:
                    current.text = "\n\n".join(buf).strip()
                    sections.append(current)
                current = Section(heading=heading, text="", key=heading)
                buf = []
                continue
            buf.append("#" * min(level + 1, 6) + " " + heading)
            continue
        buf.append(block)
    if current is None:
        intro = buf
    else:
        current.text = "\n\n".join(buf).strip()
        sections.append(current)
    if sections:
        doc.summary = "\n\n".join(intro).strip()
        doc.sections = sections
    else:
        doc.sections = [Section(heading=doc.title, text="\n\n".join(intro).strip(), key="text")]


def _docx_blocks(d: Any):
    """Paragraphs (text or (level, heading)) and tables (markdown) in order."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"^Heading (\d)", style)
            if style == "Title":
                yield (0, text)
            elif m:
                yield (int(m.group(1)), text)
            elif style.startswith("List"):
                yield f"* {text}"
            else:
                yield text
        elif tag == "tbl":
            t = Table(child, d)
            rows = [[c.text for c in r.cells] for r in t.rows[:MAX_TABLE_ROWS + 1]]
            yield markdown_table(rows, len(t.rows) - 1)


def _xlsx(path: Path, doc: Document, budget: Optional[Budget] = None) -> None:
    try:
        import openpyxl
    except ImportError as e:
        raise MissingDependency("Excel (.xlsx)", "openpyxl>=3.1") from e
    check_ooxml(path)
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:
        raise IngestError(f"Could not read the Excel file '{path.name}': {e}.") from e
    try:
        sections: List[Section] = []
        names = list(wb.sheetnames)
        doc.facts.append(("sheets", ", ".join(names[:30])))
        for name in names[:30]:
            ws = wb[name]
            rows: List[List[Any]] = []
            total = -1
            more = False
            for n, row in enumerate(ws.iter_rows(values_only=True)):
                if n % 5000 == 0 and budget is not None and budget.out_of_time(margin=10.0):
                    more = True
                    break
                if not any(c not in (None, "") for c in row):
                    continue
                total += 1
                if len(rows) <= MAX_TABLE_ROWS:
                    rows.append(list(row[:MAX_TABLE_COLS]))
                if total >= MAX_XLSX_ROWS_COUNTED:
                    more = True
                    break
            if more:
                doc.skipped.append(f"sheet {name}: rows after {total} not counted (row/time limit)")
            if rows:
                sections.append(_table_section(name, rows, max(0, total)))
        doc.sections = sections
        if len(names) > 30:
            doc.skipped.append(f"{len(names) - 30} more sheets")
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _image(path: Path, doc: Document) -> None:
    doc.kind = "image"
    info: Dict[str, str] = {"format": path.suffix.lstrip(".").upper()}
    try:
        from PIL import Image  # optional
    except ImportError:
        Image = None  # type: ignore[assignment]
    if Image is not None and path.suffix.lower() != ".svg":
        try:
            with Image.open(path) as im:
                info["dimensions"] = f"{im.width} × {im.height} px"
                info["mode"] = str(im.mode)
                if im.format:
                    info["format"] = str(im.format)
        except Exception as e:
            doc.errors.append(f"could not read the image details: {e}")
    elif path.suffix.lower() == ".svg":
        head = read_text_file(path, 4000)
        m = re.search(r'<svg[^>]*\bwidth="([\d.]+)[^"]*"[^>]*\bheight="([\d.]+)', head)
        if m:
            info["dimensions"] = f"{m.group(1)} × {m.group(2)}"
    for k, v in info.items():
        doc.facts.append((k, v))
    doc.summary = (f"Image file `{path.name}`. Only the file's details are stored (no text "
                   "recognition).")
    doc.sections = []
