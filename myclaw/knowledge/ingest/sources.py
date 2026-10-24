"""Sources for ``assimilate``: web page, GitHub repository, local file/folder
(KB phase 5, 2026-10-24).

* **URL** — fetched with ``httpx`` (the proxy settings of the environment are
  used); every redirect target is checked like the ``browse`` tool does
  (``myclaw.tools.web._is_safe_url``) and, in addition, the host name's
  addresses are resolved so a name pointing at a private or local address is
  refused too. HTML → main text, headings and links; a PDF/Word/Excel/text
  URL is read as that document.
* **GitHub** — ``https://github.com/owner/repo[/tree/branch/path]`` via the
  REST API (no git needed): repository info, the file tree, and the key files
  from ``raw.githubusercontent.com``. ``GITHUB_TOKEN`` (environment) is used
  when set (private repositories, higher rate limit).
* **Local** — a file or folder inside the allowed roots (see
  :func:`safety.check_local_path`); folders honour ``.gitignore`` and never
  read hidden folders or secret files.

``http_get`` is the only function that touches the network (tests replace it).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

from .documents import kind_of, read_document, read_text_file
from .model import Budget, Document, IngestError
from .repo import TreeEntry, analyze_repo
from .safety import (
    GitIgnore, check_local_path, clean_url, ip_is_public, is_secret_file, skip_dir,
)

logger = logging.getLogger(__name__)

USER_AGENT = "ZenSynora-knowledge-import/1.0 (+https://github.com/adrianx26/zensynora)"
MAX_PAGE_BYTES = 10_000_000
MAX_WALK_ENTRIES = 20_000
HTTP_TIMEOUT = 20.0


# ── HTTP ─────────────────────────────────────────────────────────────────────
@dataclass
class HttpResponse:
    status: int
    url: str
    content: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        ctype = self.headers.get("content-type", "")
        m = re.search(r"charset=([\w-]+)", ctype)
        enc = m.group(1) if m else "utf-8"
        try:
            return self.content.decode(enc, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content.decode("utf-8", errors="replace"))


def _proxy_for(url: str) -> Optional[str]:
    """The HTTP(S) proxy the environment sets for ``url`` (None = direct)."""
    import urllib.request

    parsed = urlparse(url)
    proxies = urllib.request.getproxies()
    proxy = proxies.get(parsed.scheme) or proxies.get("all")
    if not proxy:
        return None
    try:
        if urllib.request.proxy_bypass(parsed.hostname or ""):
            return None
    except Exception:  # a broken no_proxy entry: use the proxy
        pass
    return proxy


def check_url(url: str) -> List[str]:
    """Refuse URLs that are not public web addresses; returns the checked IPs.

    2026-10-24 (KB phase 5, review): every address of the host must be
    globally routable (``ipaddress.is_global``, also the IPv4 inside mapped /
    NAT64 / 6to4 IPv6 addresses), and a name that cannot be resolved is
    refused — except behind an HTTP(S) proxy, which resolves names itself
    (then names that resolve here must still be public; what the proxy
    resolves cannot be checked from here).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise IngestError(f"'{clean_url(url)}' is not a web address (it must start with http:// or "
                          "https://).")
    try:
        from myclaw.tools.web import _is_safe_url  # the browse tool's check

        ok, reason = _is_safe_url(url)
    except Exception:  # pragma: no cover - the tools package cannot be imported
        ok, reason = True, ""
    if not ok:
        raise IngestError(f"{reason} Only public web pages can be imported.")
    host = parsed.hostname.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise IngestError("Access to localhost is not allowed; only public web pages can be imported.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as e:
        raise IngestError(f"'{clean_url(url)}' has an invalid port.") from e
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as e:
        if _proxy_for(url):
            return []  # the proxy resolves the name
        raise IngestError(f"'{host}' could not be found (DNS). Check the address.") from e
    ips: List[str] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            continue
        if not ip_is_public(ip):
            raise IngestError(f"'{host}' points to a local or private address ({ip}); only public "
                              "web pages can be imported.")
        if str(ip) not in ips:
            ips.append(str(ip))
    if not ips and not _proxy_for(url):
        raise IngestError(f"'{host}' could not be found (DNS). Check the address.")
    return ips


def _deadline_error(url: str) -> IngestError:
    return IngestError(f"Stopped downloading '{clean_url(url)}': the time limit of this run was "
                       "reached.")


def http_get(url: str, headers: Optional[Dict[str, str]] = None, max_bytes: int = MAX_PAGE_BYTES,
             timeout: float = HTTP_TIMEOUT, budget: Optional[Budget] = None) -> HttpResponse:
    """GET with address checks on every hop, a size cap and a time limit.

    2026-10-24 (KB phase 5, review): without a proxy the connection goes to
    exactly the address that was checked (the URL is rewritten to the IP; the
    Host header and the TLS server name stay the host name, so certificates
    are still verified), so a DNS answer that changes between the check and
    the connection (DNS rebinding) cannot reach a local address. The budget's
    deadline and cancel flag stop the download between chunks; the whole
    request never takes longer than ``timeout``.
    """
    import time as _time

    import httpx

    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    current = url
    started = _time.monotonic()

    def left() -> float:
        rest = timeout - (_time.monotonic() - started)
        if budget is not None:
            rest = min(rest, budget.time_left())
        return rest

    for _ in range(6):
        if left() <= 0 or (budget is not None and budget.cancel.is_set()):
            raise _deadline_error(current)
        ips = check_url(current)
        proxy = _proxy_for(current)
        parsed = urlparse(current)
        target = current
        extensions: Dict[str, object] = {}
        req_headers = dict(hdrs)
        if not proxy:
            ip = ips[0]
            ip_host = f"[{ip}]" if ":" in ip else ip
            netloc = ip_host + (f":{parsed.port}" if parsed.port else "")
            target = parsed._replace(netloc=netloc).geturl()
            req_headers["Host"] = parsed.netloc.rsplit("@", 1)[-1]
            if parsed.scheme == "https":
                extensions["sni_hostname"] = parsed.hostname
        try:
            client = httpx.Client(timeout=httpx.Timeout(max(0.5, min(timeout, left()))),
                                  follow_redirects=False, trust_env=bool(proxy))
            with client, client.stream("GET", target, headers=req_headers,
                                       extensions=extensions) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    nxt = urljoin(current, r.headers["location"])
                    if urlparse(nxt).hostname != urlparse(url).hostname:
                        hdrs.pop("Authorization", None)  # never send a token to another host
                    current = nxt
                    continue
                chunks: List[bytes] = []
                size = 0
                for chunk in r.iter_bytes():
                    if left() <= 0 or (budget is not None and budget.cancel.is_set()):
                        raise _deadline_error(current)
                    size += len(chunk)
                    if size > max_bytes:
                        raise IngestError(f"'{clean_url(current)}' is larger than "
                                          f"{max_bytes // 1_000_000} MB; it is not imported.")
                    chunks.append(chunk)
                return HttpResponse(r.status_code, current, b"".join(chunks),
                                    {k.lower(): v for k, v in r.headers.items()})
        except httpx.TimeoutException as e:
            if budget is not None and budget.out_of_time():
                raise _deadline_error(current) from e
            raise IngestError(f"'{clean_url(current)}' did not answer in time; try again later.") from e
        except httpx.HTTPError as e:
            raise IngestError(f"Could not fetch '{clean_url(current)}': {type(e).__name__}. Check the "
                              "address and the internet connection.") from e
    raise IngestError(f"'{clean_url(url)}' redirects too often.")


# ── web page ─────────────────────────────────────────────────────────────────
_DOC_TYPES = {"application/pdf": ".pdf",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
              "text/csv": ".csv", "application/json": ".json", "text/markdown": ".md",
              "text/plain": ".txt"}


def fetch_url(url: str, budget: Budget) -> Document:
    """A web page (or a document behind a URL) as a :class:`Document`."""
    from .html_text import html_to_document

    shown = clean_url(url)  # never store / show credentials of the URL
    r = http_get(url, budget=budget)
    if r.status == 404:
        raise IngestError(f"'{shown}' was not found (404). Check the address.")
    if r.status in (401, 403):
        raise IngestError(f"'{shown}' needs a login or blocks automated access ({r.status}); "
                          "download the page and import the file instead.")
    if r.status >= 400:
        raise IngestError(f"'{shown}' answered with HTTP {r.status}; try again later.")
    budget.take_bytes(len(r.content))
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    parsed = urlparse(r.url)
    ext = Path(parsed.path).suffix.lower()
    if ctype in _DOC_TYPES or (ctype in ("application/octet-stream", "") and kind_of(Path(parsed.path))
                               not in ("", "html", "code")):
        suffix = _DOC_TYPES.get(ctype) or ext or ".txt"
        if suffix == ".txt" and ext in (".md", ".markdown", ".rst"):
            suffix = ext
        with tempfile.TemporaryDirectory(prefix="zensynora-ingest-") as tmp:
            name = re.sub(r"[^\w.-]", "_", Path(parsed.path).name or "download")[:80]
            if not name.lower().endswith(suffix):
                name += suffix
            path = Path(tmp) / name
            path.write_bytes(r.content)
            doc = read_document(path, budget, source=shown)
        doc.permalink_hint = f"web-{parsed.hostname}-{Path(parsed.path).stem}"
        doc.date = ""
        return doc
    if not (ctype.startswith("text/html") or ctype in ("application/xhtml+xml", "") or "html" in ctype):
        raise IngestError(f"'{shown}' is {ctype or 'an unknown type'}, not a web page or a supported "
                          "document.")
    doc = Document(title=parsed.hostname or shown, source=shown, kind="web", size=len(r.content),
                   permalink_hint=f"web-{parsed.hostname}-{parsed.path.strip('/').replace('/', '-')[:40]}")
    html_to_document(r.text, doc, base_url=r.url)
    if doc.metadata.get("doc_title"):
        doc.title = str(doc.metadata.pop("doc_title"))
    doc.metadata["site"] = parsed.hostname or ""
    doc.links = list(dict.fromkeys(clean_url(u) for u in doc.links))
    if not doc.sections and not doc.summary:
        raise IngestError(f"No readable text on '{shown}' (the page may need JavaScript). Try "
                          "another address, or save the page and import the file.")
    return doc


# ── GitHub ───────────────────────────────────────────────────────────────────
_GH = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?"
                 r"(?:/(?:tree|blob)/([^/]+)(?:/(.*?))?)?/?(?:[?#].*)?$")


def parse_github(url: str) -> Optional[Tuple[str, str, Optional[str], str]]:
    """``(owner, repo, ref, path)`` of a GitHub repository URL, else None."""
    m = _GH.match((url or "").strip())
    if not m:
        return None
    owner, repo, ref, path = m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip("/")
    if owner.lower() in ("orgs", "settings", "topics", "marketplace", "features", "about"):
        return None
    return owner, repo, ref, path


def _gh_headers() -> Dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _gh_api(path: str, budget: Optional[Budget] = None) -> HttpResponse:
    r = http_get(f"https://api.github.com{path}", headers=_gh_headers(), budget=budget)
    if r.status == 404:
        hint = ("" if os.environ.get("GITHUB_TOKEN")
                else " If it is private, set GITHUB_TOKEN in ZenSynora's environment.")
        raise IngestError(f"GitHub repository not found.{hint} Check the owner/name in the URL.")
    if r.status in (403, 429):
        reset = r.headers.get("x-ratelimit-reset", "")
        when = ""
        if reset.isdigit():
            import time as _t

            when = f" (try again after {_t.strftime('%H:%M', _t.localtime(int(reset)))})"
        hint = "" if os.environ.get("GITHUB_TOKEN") else " Setting GITHUB_TOKEN raises the limit."
        raise IngestError(f"GitHub refused the request (rate limit or access){when}.{hint}")
    if r.status >= 400:
        raise IngestError(f"GitHub answered with HTTP {r.status}; try again later.")
    return r


def _earlier(base: Optional[str], source: str) -> set:
    """Files an unfinished earlier run of this source already wrote (continuation)."""
    if not base:
        return set()
    from .notes import earlier_parts
    from .safety import clean_source

    return set(earlier_parts(base, clean_source(source)))


def fetch_github(url: str, budget: Budget, max_files: int, base: Optional[str] = None) -> Document:
    """Architecture overview of a GitHub repository (REST API, no git)."""
    parsed = parse_github(url)
    if not parsed:
        raise IngestError(f"'{url}' is not a GitHub repository address "
                          "(https://github.com/owner/repo).")
    owner, repo, ref, sub = parsed
    info = _gh_api(f"/repos/{owner}/{repo}", budget).json()
    ref = ref or str(info.get("default_branch") or "main")
    budget.report("reading the file list")
    tree = _gh_api(f"/repos/{owner}/{repo}/git/trees/{quote(ref, safe='')}?recursive=1", budget).json()
    entries: List[TreeEntry] = []
    prefix = f"{sub}/" if sub else ""   # what the entry paths are relative to
    for item in tree.get("tree") or []:
        if item.get("type") != "blob":
            continue
        p = str(item.get("path") or "")
        size = int(item.get("size") or 0)
        if not sub:
            entries.append(TreeEntry(p, size))
        elif p == sub:  # a single file (…/blob/branch/path/file.py)
            prefix = sub[: len(sub) - len(p.rsplit("/", 1)[-1])]
            entries = [TreeEntry(p.rsplit("/", 1)[-1], size)]
            break
        elif p.startswith(prefix):
            entries.append(TreeEntry(p[len(prefix):], size))
    if sub and not entries:
        raise IngestError(f"'{sub}' was not found in {owner}/{repo} (branch {ref}).")
    facts: List[Tuple[str, str]] = []
    for key, cat in (("language", "main_language"), ("license", "license"),
                     ("stargazers_count", "stars"), ("homepage", "homepage")):
        val = info.get(key)
        if isinstance(val, dict):
            val = val.get("spdx_id") or val.get("name")
        if val not in (None, "", "NOASSERTION"):
            facts.append((cat, str(val)))
    if info.get("topics"):
        facts.append(("topics", ", ".join(info["topics"][:15])))
    facts.append(("branch", ref))
    if tree.get("truncated"):
        facts.append(("note", "the file list was cut by GitHub (very large repository)"))

    def read(path: str) -> Optional[str]:
        raw = (f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(ref, safe='')}/"
               f"{quote(prefix + path)}")
        r = http_get(raw, headers=_gh_headers(), max_bytes=1_000_000, budget=budget)
        if r.status >= 400:
            raise IngestError(f"HTTP {r.status}")
        if b"\x00" in r.content[:4096]:
            return None
        return r.content.decode("utf-8", errors="replace")

    name = f"{owner}/{repo}" + (f"/{sub}" if sub else "")
    done = _earlier(base, f"https://github.com/{name}")
    doc = analyze_repo(
        title=f"{name} (GitHub)", source=f"https://github.com/{name}", kind="github",
        permalink_hint=f"github-{owner}-{repo}-{sub}" if sub else f"github-{owner}-{repo}",
        entries=entries, read=read, budget=budget, max_files=max_files,
        description=str(info.get("description") or ""),
        link_base=f"https://github.com/{owner}/{repo}/blob/{ref}/{prefix}", extra_facts=facts,
        done=done)
    doc.metadata.update({"repository": f"{owner}/{repo}", "branch": ref})
    return doc


# ── local ────────────────────────────────────────────────────────────────────
def walk_folder(root: Path, budget: Budget) -> Tuple[List[TreeEntry], List[str]]:
    """Files of a folder (relative paths), honouring .gitignore and skip rules."""
    ignore = GitIgnore(root)
    entries: List[TreeEntry] = []
    skipped: List[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if skip_dir(d) or ignore.ignored(rel, is_dir=True):
                continue
            keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            seen += 1
            if seen > MAX_WALK_ENTRIES:
                skipped.append(f"more than {MAX_WALK_ENTRIES} files: the rest is not listed")
                return entries, skipped
            rel = f"{rel_dir}/{f}" if rel_dir else f
            full = Path(dirpath) / f
            if ignore.ignored(rel):
                continue
            if f.startswith(".") and f.lower() not in (".cursorrules",):
                continue
            if is_secret_file(f):
                skipped.append(f"{rel} (may contain secrets)")
                continue
            try:
                if full.is_symlink():
                    try:
                        full.resolve().relative_to(root)
                    except ValueError:
                        skipped.append(f"{rel} (link outside the folder)")
                        continue
                size = full.stat().st_size
            except OSError:
                continue
            entries.append(TreeEntry(rel, size))
        if budget.out_of_time(margin=10.0):
            skipped.append("folder listing stopped: time limit")
            break
    return entries, skipped


def _flatten(doc: Document) -> str:
    parts = [doc.summary] if doc.summary else []
    for s in doc.norm_sections():
        parts.append(f"### {s.heading}\n\n{s.text}" if s.heading != doc.title else s.text)
    for c, t in doc.facts:
        parts.append(f"* {c}: {t}")
    return "\n\n".join(p for p in parts if p)


def local_source(raw: str, budget: Budget, max_files: int, trusted: bool = False,
                 base: Optional[str] = None) -> Document:
    """A local file or folder as a :class:`Document`.

    ``trusted`` (CLI, uploads): the path may be outside the allowed roots
    (hidden folders and secret files are still never read).
    """
    # 2026-10-24 (KB phase 5, review): the same hidden/secret rules for every caller
    path = check_local_path(raw, trusted=trusted)
    if path.is_file():
        return read_document(path, budget)
    if not path.is_dir():
        raise IngestError(f"'{raw}' is neither a file nor a folder.")
    budget.report("listing the folder")
    entries, skipped = walk_folder(path, budget)

    def read(rel: str) -> Optional[str]:
        full = path / rel
        kind = kind_of(full)
        if kind in ("pdf", "docx", "xlsx", "csv", "json", "html"):
            return _flatten(read_document(full, budget))
        if kind == "image":
            return None
        return read_text_file(full, 300_000)

    doc = analyze_repo(title=f"{path.name}/ (folder)", source=str(path), kind="folder",
                       permalink_hint=f"folder-{path.name}", entries=entries, read=read,
                       budget=budget, max_files=max_files, prefetch=True,
                       done=_earlier(base, str(path)))
    doc.skipped = skipped + doc.skipped
    return doc


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", (source or "").strip(), re.IGNORECASE))
