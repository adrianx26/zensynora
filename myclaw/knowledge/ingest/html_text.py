"""HTML → main text, headings and outgoing links (KB phase 5, 2026-10-24).

Standard library only (``html.parser``). When the page has a ``<main>`` or
``<article>``, only that part is read; navigation, headers, footers, scripts
and forms are dropped. The page is split into sections at ``<h1>``–``<h3>``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urldefrag, urlparse

from .model import Document, Section

_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form",
         "button", "template", "iframe", "select", "canvas", "dialog"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source",
         "track", "wbr", "param"}
_BLOCK = {"p", "div", "section", "li", "tr", "blockquote", "dd", "dt", "figcaption", "table",
          "ul", "ol", "dl", "main", "article", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "br"}
MAX_LINKS = 200


class _Extractor(HTMLParser):
    def __init__(self, base_url: str, only_main: bool):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.only_main = only_main
        self.main_depth = 0
        self.skip_depth = 0
        self.stack: List[str] = []
        self.title = ""
        self.in_title = False
        self.description = ""
        self.og_title = ""
        self.blocks: List[Tuple[str, str]] = []  # ("h1".."h6" | "p" | "pre" | "li", text)
        self.buf: List[str] = []
        self.cur_tag = "p"
        self.in_pre = 0
        self.links: List[str] = []
        self.cells: List[str] = []
        self.in_cell = False

    # helpers
    def _active(self) -> bool:
        return self.skip_depth == 0 and (not self.only_main or self.main_depth > 0)

    def _flush(self) -> None:
        text = "".join(self.buf)
        self.buf = []
        if self.cur_tag == "pre":
            text = text.strip("\n")
        else:
            text = re.sub(r"\s+", " ", text).strip()
        if text:
            self.blocks.append((self.cur_tag, text))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = (a.get("content") or "").strip()
            if name == "og:title":
                self.og_title = (a.get("content") or "").strip()
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in _VOID:
            if tag == "br" and self._active():
                self.buf.append("\n" if self.in_pre else " ")
            return
        self.stack.append(tag)
        if tag in ("main", "article"):
            self.main_depth += 1
        if tag in _SKIP:
            self.skip_depth += 1
            return
        if not self._active():
            return
        if tag == "a":
            href = (a.get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                url = urldefrag(urljoin(self.base_url, href))[0] if self.base_url else href
                if urlparse(url).scheme in ("http", "https") and url not in self.links \
                        and len(self.links) < MAX_LINKS:
                    self.links.append(url)
        if tag in ("td", "th"):
            self.in_cell = True
            self._flush_cell_start()
            return
        if tag in _BLOCK and not self.in_cell:
            self._flush()
            self.cur_tag = tag if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "pre", "li") else "p"
            if tag == "pre":
                self.in_pre += 1

    def _flush_cell_start(self) -> None:
        if self.buf and not self.in_cell:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag in _VOID:
            return
        # pop to the matching tag (tolerates unclosed tags)
        if tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            if t in ("main", "article"):
                self.main_depth -= 1
            if t in _SKIP:
                self.skip_depth -= 1
            if t == tag:
                break
        if tag in ("td", "th"):
            self.cells.append(re.sub(r"\s+", " ", "".join(self.buf)).strip())
            self.buf = []
            self.in_cell = False
            return
        if tag == "tr":
            if any(self.cells) and (self.skip_depth == 0):
                self.blocks.append(("tr", " | ".join(self.cells)))
            self.cells = []
            return
        if tag == "pre":
            self.in_pre = max(0, self.in_pre - 1)
        if tag in _BLOCK and not self.in_cell:
            self._flush()
            self.cur_tag = "p"

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if self._active():
            self.buf.append(data)


def html_to_document(html: str, doc: Document, base_url: str = "") -> None:
    """Fill ``doc`` (title, summary, sections, links) from an HTML page."""
    only_main = bool(re.search(r"<(main|article)[\s>]", html, re.IGNORECASE))
    p = _Extractor(base_url, only_main)
    try:
        p.feed(html)
        p.close()
    except Exception as e:  # broken HTML: keep what was read
        doc.errors.append(f"HTML could not be fully read: {e}")
    p._flush()
    if only_main and sum(len(t) for _, t in p.blocks) < 200:
        # the <main> was nearly empty (an app shell): read the whole page
        p2 = _Extractor(base_url, False)
        try:
            p2.feed(html)
            p2.close()
        except Exception:
            pass
        p2._flush()
        p2.title = p2.title or p.title
        p = p2
    title = re.sub(r"\s+", " ", p.og_title or p.title or "").strip()
    first_h1 = next((t for tag, t in p.blocks if tag == "h1"), "")
    if title:
        doc.metadata["doc_title"] = title
    elif first_h1:
        doc.metadata["doc_title"] = first_h1
    doc.summary = p.description
    doc.links = [u for u in p.links if u != base_url]
    sections: List[Section] = []
    current: Optional[Section] = None
    intro: List[str] = []
    buf: List[str] = []
    for tag, text in p.blocks:
        if tag in ("h1", "h2", "h3"):
            if tag == "h1" and text == first_h1 and current is None and not buf:
                continue
            if current is None:
                intro = buf
            else:
                current.text = "\n\n".join(buf).strip()
                sections.append(current)
            current = Section(heading=text[:120], text="", key=text[:80])
            buf = []
        elif tag in ("h4", "h5", "h6"):
            buf.append(f"#### {text}")
        elif tag == "pre":
            buf.append(f"```\n{text}\n```")
        elif tag == "li":
            buf.append(f"* {text}")
        else:
            buf.append(text)
    if current is None:
        intro = buf
    else:
        current.text = "\n\n".join(buf).strip()
        sections.append(current)
    intro_text = "\n\n".join(intro).strip()
    if sections:
        if intro_text:
            doc.summary = (doc.summary + "\n\n" + intro_text).strip() if doc.summary else intro_text
        doc.sections = sections
    else:
        doc.sections = [Section(heading=doc.metadata.get("doc_title") or doc.title,
                                text=intro_text, key="text")] if intro_text else []
