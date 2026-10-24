"""Semantic search for the knowledge base (KB phase 3, 2026-10-24).

The Markdown files stay the source of truth; vectors are derived data kept in
the base's index database and can always be rebuilt (``backfill_embeddings``).

* **Embedder** — :class:`FastEmbedEmbedder` (fastembed / ONNX, local, CPU,
  ``threads=1``, model cache in ``~/.myclaw/models``) or the deterministic
  :class:`HashEmbedder` used by the tests. fastembed is optional (extra
  ``semantic``); without it everything else works and the status says how to
  install it. Inference never runs on the event loop.
* **Store** — ``note_vectors``: one row per (entity_id, chunk_no) with model,
  dimension, the chunk text's checksum and an L2-normalised float32 BLOB;
  ``note_embed_state``: per note, which file version and model it was
  embedded from. Both are deleted with the note (FK cascade + trigger); a
  deleted note's vectors are kept for a while in ``note_vectors_carry`` (keyed
  by model + chunk checksum) so a full re-index (``sync --force``, which
  re-creates every entity) or restoring a note from ``.trash`` reuses them
  instead of running the model again. Schema init never drops anything.
* **Embed on write** — ``KnowledgeDB.sync_entity_from_note`` (the single
  place every write, edit and sync goes through) calls :func:`note_indexed`
  after the commit; a background thread embeds queued notes in small
  batches, one batch at a time, only chunks whose text or model changed.
* **Search** — cosine in numpy over the current model's vectors (capped),
  best chunk per note, fused with the FTS ranking by Reciprocal Rank Fusion
  (k = 60). ``mode="auto"`` uses it only when it is switched on and ready;
  boolean queries always use FTS only.

Lessons from memopad (its sqlite-vec/content-hash work, deleted by release
5c3b6c4): composite key, checksum dedup per chunk, threads capped, inference
off the event loop, a model change re-embeds (rows of another model are never
compared), the query embedding is cached, failures are logged once.
sqlite-vec is not used: Python's sqlite3 often cannot load extensions,
memopad's vec0 path silently never loaded for months and needed per-dimension
tables; a numpy scan of a personal knowledge base (<= 60 000 chunks) takes
milliseconds.
"""
from __future__ import annotations

import atexit
import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Models ───────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
INSTALL_SPEC = "fastembed>=0.5,<1.0"
INSTALL_HINT = (f'python -m pip install "{INSTALL_SPEC}" in ZenSynora\'s virtual environment '
                "(or: pip install 'zensynora[semantic]'), or the Install button on the Knowledge page")


@dataclass(frozen=True)
class ModelInfo:
    dim: int
    size_mb: int
    licence: str
    max_tokens: int
    chunk_chars: int      # chunk size that fits the model's input window
    min_score: float      # cosine below this: not a semantic match (estimate, see docs)
    languages: str


# The two models offered in Settings. bge-small-en-v1.5 is the default: 67 MB,
# MIT, 512-token window (a ~1000-character chunk fits), the best English
# retrieval quality at its size. The multilingual MiniLM understands Romanian
# (and ~50 languages, cross-lingual) but is 3x larger and reads only 128
# tokens, so chunks are smaller (more chunks, more inference).
MODELS: Dict[str, ModelInfo] = {
    "BAAI/bge-small-en-v1.5": ModelInfo(384, 67, "MIT", 512, 1000, 0.5, "English"),
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": ModelInfo(
        384, 220, "Apache-2.0", 128, 450, 0.4, "about 50 languages, Romanian included"),
}
_UNKNOWN_MODEL = ModelInfo(0, 0, "see the model card", 512, 1000, 0.3, "see the model card")

RRF_K = 60
CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_NOTE = 48          # ~48 000 characters of one note are embedded
BATCH_CHUNKS = 16                 # chunks per model call (bounded memory / latency)
BATCH_NOTES = 32                  # notes read and written per transaction
MAX_SCAN_CHUNKS = 60_000          # vectors scanned per query (~90 MB at 384 dims)
QUEUE_CAP = 20_000                # queued note ids per base; more → "all stale"
AGENT_TIMEOUT = 1.5               # automatic context: query embedding budget (s)
SEARCH_TIMEOUT = 15.0             # tool / Web UI search: wait for the model (s)
LOAD_RETRY_SECONDS = 300          # a failed model load is retried after this
CARRY_DAYS = 30                   # deleted notes' vectors kept for re-use
CARRY_MAX_ROWS = 200_000
DEBOUNCE_SECONDS = 0.3            # coalesce bursts of writes
MODES = ("auto", "fts", "semantic", "hybrid")

# ── Options (config: knowledge.semantic_search / embedding_model / …) ───────

_OPTIONS: Dict[str, Any] = {
    "semantic_search": False,
    "embedding_model": DEFAULT_MODEL,
    "kb_enabled": True,
    "min_score": 0.0,             # 0 = the model's default
}


def set_options(semantic_search: Optional[bool] = None, embedding_model: Optional[str] = None,
                kb_enabled: Optional[bool] = None, min_score: Optional[float] = None) -> None:
    """Apply the settings. A new model drops the loaded one (its vectors become stale)."""
    if semantic_search is not None:
        _OPTIONS["semantic_search"] = bool(semantic_search)
    if kb_enabled is not None:
        _OPTIONS["kb_enabled"] = bool(kb_enabled)
    if min_score is not None:
        try:
            _OPTIONS["min_score"] = max(0.0, min(1.0, float(min_score)))
        except (TypeError, ValueError):
            pass
    if embedding_model is not None:
        name = str(embedding_model).strip() or DEFAULT_MODEL
        if name != _OPTIONS["embedding_model"]:
            _OPTIONS["embedding_model"] = name
            _reset_caches()


def enabled() -> bool:
    return bool(_OPTIONS["semantic_search"])


def model_name() -> str:
    return str(_OPTIONS["embedding_model"] or DEFAULT_MODEL)


def model_info(name: Optional[str] = None) -> ModelInfo:
    return MODELS.get(name or model_name(), _UNKNOWN_MODEL)


def min_score() -> float:
    v = float(_OPTIONS.get("min_score") or 0.0)
    return v if v > 0 else model_info().min_score


def models_dir() -> Path:
    """Model cache (survives updates; ZenSynora's own folder, not ~/.cache)."""
    return Path.home() / ".myclaw" / "models"


def install_command() -> List[str]:
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", INSTALL_SPEC]


# ── Log once ────────────────────────────────────────────────────────────────

_LOGGED: set = set()
_LOG_LOCK = threading.Lock()


def _log_once(key: str, level: int, msg: str, *args: Any) -> None:
    with _LOG_LOCK:
        if key in _LOGGED:
            return
        _LOGGED.add(key)
        if len(_LOGGED) > 1000:
            _LOGGED.clear()
    logger.log(level, msg, *args)


# ── Embedders ───────────────────────────────────────────────────────────────

class Embedder(Protocol):
    """What the store needs from an embedding model.

    Both methods return float32 vectors with unit length (rows of a matrix for
    passages). They are blocking and CPU-heavy: call them from a thread.
    """

    model: str
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat[None, :]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    return (mat / np.clip(norms, 1e-12, None)).astype(np.float32)


class FastEmbedEmbedder:
    """fastembed (ONNX Runtime, CPU). Imported lazily; one intra-op thread.

    The first load downloads the model from Hugging Face into
    ``~/.myclaw/models`` (bge-small-en-v1.5: 67 MB); later loads are offline.
    """

    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: Optional[Path] = None,
                 threads: int = 1) -> None:
        # The Rust tokenizer would otherwise use one thread per CPU.
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from fastembed import TextEmbedding  # optional dependency (extra "semantic")

        try:  # fastembed logs every download retry as ERROR (loguru); we log one WARNING
            from loguru import logger as _loguru

            _loguru.disable("fastembed")
        except Exception:
            pass

        cache = Path(cache_dir) if cache_dir else models_dir()
        cache.mkdir(parents=True, exist_ok=True)
        self.model = model
        self._m = TextEmbedding(model_name=model, cache_dir=str(cache), threads=threads)
        self.dim = int(self.embed_passages(["dimension probe"]).shape[1])

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        fn = getattr(self._m, "passage_embed", None) or self._m.embed
        return _normalise(np.asarray(list(fn(list(texts))), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        fn = getattr(self._m, "query_embed", None)
        vecs = list(fn(text)) if fn is not None else list(self._m.embed([text]))
        return _normalise(np.asarray(vecs[0], dtype=np.float32))[0]


class HashEmbedder:
    """Deterministic stand-in for tests (no download, no fastembed).

    Bag of hashed words (weight 1) and word trigrams (weight 0.3), so similar
    spellings are close; ``synonyms`` maps words onto each other to simulate
    meaning. ``delay`` makes queries slow (timeout tests). ``calls`` counts
    passage texts embedded.
    """

    def __init__(self, model: str = "test/hash-embedder", dim: int = 64,
                 synonyms: Optional[Dict[str, str]] = None, delay: float = 0.0) -> None:
        self.model = model
        self.dim = dim
        self.synonyms = {k.lower(): v.lower() for k, v in (synonyms or {}).items()}
        self.delay = delay
        self.calls = 0
        self.query_calls = 0

    def _bucket(self, feature: str) -> Tuple[int, float]:
        h = hashlib.md5(feature.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "little") % self.dim, (1.0 if h[4] & 1 else -1.0)

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for word in re.findall(r"\w+", (text or "").lower()):
            word = self.synonyms.get(word, word)
            i, s = self._bucket("w:" + word)
            v[i] += s
            padded = f"#{word}#"
            for j in range(len(padded) - 2):
                i, s = self._bucket("t:" + padded[j:j + 3])
                v[i] += 0.3 * s
        if not v.any():
            v[0] = 1.0
        return _normalise(v)[0]

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        self.calls += len(texts)
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        self.query_calls += 1
        if self.delay:
            time.sleep(self.delay)
        return self._vec(text)


_factory: Optional[Callable[[str], Embedder]] = None


def set_embedder_factory(factory: Optional[Callable[[str], Embedder]]) -> None:
    """Tests: build embedders with ``factory(model)`` instead of fastembed (None = fastembed)."""
    global _factory
    _factory = factory
    _reset_caches()


def library_installed() -> bool:
    if _factory is not None:
        return True
    try:
        return importlib.util.find_spec("fastembed") is not None
    except (ImportError, ValueError):
        return False


def _create_embedder(model: str) -> Embedder:
    if _factory is not None:
        return _factory(model)
    return FastEmbedEmbedder(model)


class _Holder:
    """The loaded model (one per process) and its loading state."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.gen = 0
        self.model: Optional[str] = None
        self.obj: Optional[Embedder] = None
        self.state = "idle"            # idle | loading | ready | error
        self.error: Optional[str] = None
        self.error_at = 0.0
        self.ready = threading.Event()


_H = _Holder()


def _load(model: str, gen: int, ready: threading.Event) -> None:
    obj: Optional[Embedder] = None
    err: Optional[str] = None
    t0 = time.time()
    try:
        obj = _create_embedder(model)
    except ImportError:
        err = f"fastembed is not installed — install with: {INSTALL_HINT}"
    except Exception as e:  # download failure, unknown model, broken onnxruntime…
        err = f"could not load the embedding model {model}: {type(e).__name__}: {e}"
        cached = models_dir().exists() and any(models_dir().iterdir())
        if not cached or "source" in str(e).lower() or "download" in str(e).lower():
            size = model_info(model).size_mb
            err += (f" — the first use downloads it from huggingface.co"
                    f"{f' ({size} MB)' if size else ''} into ~/.myclaw/models: check the internet "
                    "connection (or proxy) and press Backfill again")
    with _H.lock:
        if gen == _H.gen:
            if obj is not None:
                _H.obj, _H.state, _H.error = obj, "ready", None
            else:
                _H.obj, _H.state, _H.error, _H.error_at = None, "error", err, time.time()
        ready.set()
    if obj is not None:
        logger.info("Knowledge: embedding model %s loaded in %.1f s", model, time.time() - t0)
    else:
        _log_once(f"load:{model}:{err}", logging.WARNING, "Knowledge: semantic search: %s", err)


def get_embedder(wait: float = 0.0, retry: bool = False) -> Optional[Embedder]:
    """The model for the current settings, or None while it loads / when it cannot.

    Starts loading in a background thread if needed and waits at most ``wait``
    seconds. A failed load is retried after ``LOAD_RETRY_SECONDS`` or at once
    with ``retry=True``.
    """
    model = model_name()
    with _H.lock:
        if _H.model != model:
            _H.gen += 1
            _H.model, _H.obj, _H.state, _H.error = model, None, "idle", None
            _H.ready = threading.Event()
        if _H.state == "ready":
            return _H.obj
        if _H.state == "error" and (retry or time.time() - _H.error_at > LOAD_RETRY_SECONDS):
            _H.state, _H.error = "idle", None
            _H.ready = threading.Event()
        if _H.state == "idle":
            if not library_installed():
                _H.state, _H.error, _H.error_at = (
                    "error", f"fastembed is not installed — install with: {INSTALL_HINT}", time.time())
                _H.ready.set()
                return None
            _H.state = "loading"
            threading.Thread(target=_load, args=(model, _H.gen, _H.ready),
                             name="kb-embed-load", daemon=True).start()
        ready = _H.ready
    if wait > 0:
        ready.wait(wait)
    with _H.lock:
        return _H.obj if _H.state == "ready" and _H.model == model else None


def embedder_state() -> Tuple[str, Optional[str]]:
    with _H.lock:
        if _H.model != model_name():
            return "idle", None
        return _H.state, _H.error


# ── Query embeddings (bounded wait, LRU cache) ──────────────────────────────

_QCACHE: "OrderedDict[Tuple[str, str], np.ndarray]" = OrderedDict()
_QLOCK = threading.Lock()
_QPOOL: Optional[ThreadPoolExecutor] = None
_INFLIGHT: Dict[Tuple[str, str], Future] = {}
QCACHE_MAX = 256


def _query_pool() -> ThreadPoolExecutor:
    global _QPOOL
    with _QLOCK:
        if _QPOOL is None:
            _QPOOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-embed-query")
        return _QPOOL


def query_vector(text: str, timeout: float, wait_load: float = 0.0) -> Optional[np.ndarray]:
    """The query's vector within ``timeout`` seconds, else None (the embedding
    keeps running and is cached, so the next identical query is instant)."""
    emb = get_embedder(wait=wait_load)
    if emb is None:
        return None
    key = (emb.model, text)
    with _QLOCK:
        hit = _QCACHE.get(key)
        if hit is not None:
            _QCACHE.move_to_end(key)
            return hit
        fut = _INFLIGHT.get(key)
    if fut is None:
        def run() -> np.ndarray:
            vec = emb.embed_query(text)
            with _QLOCK:
                _QCACHE[key] = vec
                _QCACHE.move_to_end(key)
                while len(_QCACHE) > QCACHE_MAX:
                    _QCACHE.popitem(last=False)
                _INFLIGHT.pop(key, None)
            return vec

        fut = _query_pool().submit(run)
        with _QLOCK:
            _INFLIGHT[key] = fut
    try:
        return fut.result(timeout=max(0.01, timeout))
    except FutureTimeout:
        _log_once("slowquery", logging.INFO,
                  "Knowledge: a query embedding took longer than %.1f s; using word search "
                  "for this one", timeout)
        return None
    except Exception as e:
        with _QLOCK:
            _INFLIGHT.pop(key, None)
        _log_once(f"query:{type(e).__name__}", logging.WARNING,
                  "Knowledge: query embedding failed: %s", e)
        return None


# ── Chunking ────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,6}\s")


def _paragraphs(body: str) -> List[str]:
    """Blocks of text: split at blank lines and before headings."""
    out: List[str] = []
    cur: List[str] = []
    for line in (body or "").splitlines():
        if not line.strip() or (_HEADING_RE.match(line) and cur and not _HEADING_RE.match(cur[-1])):
            if cur:
                out.append("\n".join(cur).strip())
                cur = []
            if not line.strip():
                continue
        cur.append(line.rstrip())
    if cur:
        out.append("\n".join(cur).strip())
    return [p for p in out if p]


def _split_long(text: str, size: int, overlap: int) -> List[str]:
    """Pieces of at most ``size`` characters, cut at spaces, overlapping."""
    pieces = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            cut = text.rfind(" ", start + size // 2, end)
            if cut > start:
                end = cut
        pieces.append(text[start:end].strip())
        if end >= n:
            break
        start = max(start + 1, end - overlap)
        sp = text.find(" ", start, end)
        if sp != -1 and sp < end - 1:
            start = sp + 1
    return [p for p in pieces if p]


def _tail(text: str, overlap: int) -> str:
    if len(text) <= overlap:
        return text
    t = text[-overlap:]
    sp = t.find(" ")
    return t[sp + 1:] if 0 <= sp < len(t) - 1 else t


def _pack_lines(label: str, lines: List[str], budget: int) -> List[str]:
    """``label`` + lines, in blocks of at most ``budget`` characters (a line is never cut
    unless it alone is longer)."""
    blocks: List[str] = []
    cur = label
    for line in lines:
        if cur != label and len(cur) + 1 + len(line) > budget:
            blocks.append(cur)
            cur = label
        cur += "\n" + line
    if cur != label:
        blocks.append(cur)
    return blocks


def chunk_note(title: str, body: str = "", tags: Iterable[str] = (),
               facts: Iterable[Tuple[str, str]] = (), links: Iterable[Tuple[str, str]] = (),
               max_chars: int = 1000, overlap: int = CHUNK_OVERLAP,
               max_chunks: int = MAX_CHUNKS_PER_NOTE) -> List[str]:
    """The texts embedded for one note.

    Every chunk starts with the title (and the tags), so a chunk from the
    middle of a long note still says what it is about. The text is split at
    headings and paragraphs and packed into chunks of about ``max_chars``
    characters; consecutive chunks overlap by ~``overlap`` characters. Facts
    (``category: text``) and links (``relation target``) follow the text.
    """
    title = " ".join(str(title or "").split()) or "Untitled"
    tags = [str(t) for t in tags if str(t).strip()]
    header = title + (f"\nTags: {', '.join(tags)}" if tags else "")
    budget = max(200, max_chars - len(header) - 2)
    paras = _paragraphs(body)
    fact_lines = [f"- {c}: {t}" if c else f"- {t}" for c, t in facts if str(t or "").strip()]
    link_lines = [f"- {r.replace('_', ' ')} {t}" for r, t in links if str(t or "").strip()]
    paras += _pack_lines("Facts:", fact_lines, budget)
    paras += _pack_lines("Links:", link_lines, budget)
    pieces: List[str] = []
    for p in paras:
        pieces.extend(_split_long(p, budget, overlap) if len(p) > budget else [p])
    chunks: List[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 2 + len(piece) > budget:
            chunks.append(cur)
            if len(chunks) >= max_chunks:
                cur = ""
                break
            tail = _tail(cur, overlap)
            cur = tail + "\n\n" + piece if len(tail) + 2 + len(piece) <= budget else piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur and len(chunks) < max_chunks:
        chunks.append(cur)
    if not chunks:
        return [header]
    return [f"{header}\n\n{c}" for c in chunks]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Schema ──────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Vector tables (additive, never dropped; safe on any older index database)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_vectors (
            entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            chunk_no INTEGER NOT NULL,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            vector BLOB NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (entity_id, chunk_no)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_note_vectors_model ON note_vectors(model, dim)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_note_vectors_checksum "
                 "ON note_vectors(model, checksum)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_embed_state (
            entity_id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
            file_checksum TEXT,
            text_checksum TEXT NOT NULL,
            model TEXT NOT NULL,
            chunks INTEGER NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS note_vectors_carry (
            model TEXT NOT NULL,
            checksum TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL,
            added_at INTEGER NOT NULL,
            PRIMARY KEY (model, checksum)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_note_vectors_carry_added "
                 "ON note_vectors_carry(added_at)")
    # Before a note's entry is deleted, keep its vectors by checksum (a full
    # re-index deletes and re-creates every entry). Works without
    # PRAGMA foreign_keys too (the AFTER trigger removes the rows).
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS entities_vec_bd BEFORE DELETE ON entities BEGIN
            INSERT OR REPLACE INTO note_vectors_carry (model, checksum, dim, vector, added_at)
            SELECT model, checksum, dim, vector, CAST(strftime('%s', 'now') AS INTEGER)
            FROM note_vectors WHERE entity_id = old.id;
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS entities_vec_ad AFTER DELETE ON entities BEGIN
            DELETE FROM note_vectors WHERE entity_id = old.id;
            DELETE FROM note_embed_state WHERE entity_id = old.id;
        END
    """)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _has_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='note_vectors'").fetchone() is not None


# ── Reading notes from the index ────────────────────────────────────────────

def _ph(n: int) -> str:
    return ",".join("?" * n)


def _note_sources(conn: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    """Title, tags, text, facts and links of these notes, from the index."""
    from .parser import extract_body

    if not ids:
        return {}
    ids = list(ids)
    out: Dict[int, Dict[str, Any]] = {}
    for r in conn.execute(f"SELECT id, name, title, checksum, entity_metadata FROM entities "
                          f"WHERE id IN ({_ph(len(ids))})", ids):
        tags: List[str] = []
        try:
            meta = json.loads(r["entity_metadata"] or "{}")
            if isinstance(meta, dict) and isinstance(meta.get("tags"), list):
                tags = [str(t) for t in meta["tags"]]
        except ValueError:
            pass
        out[r["id"]] = {"title": r["title"] or r["name"], "tags": tags, "body": "",
                        "facts": [], "links": [], "checksum": r["checksum"]}
    if not out:
        return out
    present = list(out)
    try:
        for r in conn.execute(f"SELECT rowid, body FROM notes_text_fts "
                              f"WHERE rowid IN ({_ph(len(present))})", present):
            src = out.get(r["rowid"])
            if src is not None:
                src["body"] = extract_body(r["body"] or "", src["title"])
    except sqlite3.OperationalError:
        pass  # no FTS5: title, facts and links only
    for r in conn.execute(f"SELECT entity_id, category, content FROM observations "
                          f"WHERE entity_id IN ({_ph(len(present))}) ORDER BY id", present):
        out[r["entity_id"]]["facts"].append((r["category"] or "", r["content"] or ""))
    for r in conn.execute(
            f"SELECT r.from_entity_id AS f, r.relation_type AS t, "
            f"COALESCE(r.to_name, e.title, e.name) AS n FROM relations r "
            f"JOIN entities e ON e.id = r.to_entity_id "
            f"WHERE r.from_entity_id IN ({_ph(len(present))}) ORDER BY r.id", present):
        out[r["f"]]["links"].append((r["t"], r["n"]))
    for r in conn.execute(f"SELECT from_entity_id AS f, relation_type AS t, to_name AS n "
                          f"FROM pending_relations WHERE from_entity_id IN ({_ph(len(present))}) "
                          f"ORDER BY id", present):
        out[r["f"]]["links"].append((r["t"], r["n"]))
    return out


def _stale_ids(conn: sqlite3.Connection, model: str, all_notes: bool = False,
               limit: Optional[int] = None) -> List[int]:
    """Notes without vectors of ``model`` or embedded from an older file version."""
    if all_notes:
        sql = "SELECT id FROM entities ORDER BY updated_at DESC"
        args: List[Any] = []
    else:
        sql = ("SELECT e.id FROM entities e LEFT JOIN note_embed_state s ON s.entity_id = e.id "
               "WHERE s.entity_id IS NULL OR s.model != ? OR (e.checksum IS NOT NULL AND "
               "(s.file_checksum IS NULL OR s.file_checksum != e.checksum)) "
               "ORDER BY e.updated_at DESC")
        args = [model]
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    return [r[0] for r in conn.execute(sql, args)]


# ── Embedding notes ─────────────────────────────────────────────────────────

class _Stopped(Exception):
    pass


def _write_retry(conn: sqlite3.Connection, fn: Callable[[], None]) -> None:
    """One write transaction; retried only on "database is locked"/busy."""
    for attempt in range(4):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                fn()
            except BaseException:
                conn.rollback()
                raise
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            text = str(e).lower()
            if ("locked" not in text and "busy" not in text) or attempt == 3:
                raise
            time.sleep(0.5 * (attempt + 1))


def embed_notes(db_path: Path, ids: Sequence[int], emb: Embedder,
                progress: Optional[Callable[[int, int], None]] = None,
                stop: Optional[Callable[[], bool]] = None) -> Dict[str, int]:
    """Embed these notes (blocking; call from a thread). Only chunks whose text
    or model changed are run through the model; vectors of identical chunks
    (another note, or a deleted/re-created entry) are re-used."""
    stats = {"notes": 0, "chunks": 0, "embedded": 0, "reused": 0, "unchanged": 0}
    ids = list(dict.fromkeys(int(i) for i in ids))
    total = len(ids)
    model = emb.model
    info = model_info(model)
    conn = _connect(Path(db_path))
    try:
        if not _has_tables(conn):
            ensure_schema(conn)
            conn.commit()
        for start in range(0, total, BATCH_NOTES):
            if stop and stop():
                raise _Stopped()
            group = ids[start:start + BATCH_NOTES]
            conn.execute("BEGIN")  # one consistent snapshot of these notes
            try:
                srcs = _note_sources(conn, group)
                plans: Dict[int, Dict[str, Any]] = {}
                for eid, s in srcs.items():
                    chunks = chunk_note(s["title"], s["body"], s["tags"], s["facts"], s["links"],
                                        max_chars=info.chunk_chars)
                    sums = [_sha(c) for c in chunks]
                    plans[eid] = {"chunks": chunks, "sums": sums, "file": s["checksum"],
                                  "text": _sha("\n".join(sums))}
                existing: Dict[Tuple[int, int], Tuple[str, str, int]] = {}
                if plans:
                    for r in conn.execute(f"SELECT entity_id, chunk_no, checksum, model, dim FROM "
                                          f"note_vectors WHERE entity_id IN ({_ph(len(plans))})",
                                          list(plans)):
                        existing[(r[0], r[1])] = (r[2], r[3], r[4])
                need: Dict[str, str] = {}      # checksum -> text
                for eid, p in plans.items():
                    for no, (txt, cs) in enumerate(zip(p["chunks"], p["sums"])):
                        old = existing.get((eid, no))
                        if old is None or old[0] != cs or old[1] != model or old[2] != emb.dim:
                            need.setdefault(cs, txt)
                vecs: Dict[str, bytes] = {}
                if need:
                    keys = list(need)
                    for i in range(0, len(keys), 500):
                        part = keys[i:i + 500]
                        for r in conn.execute(
                                f"SELECT checksum, vector FROM note_vectors WHERE model = ? AND dim = ? "
                                f"AND checksum IN ({_ph(len(part))})", [model, emb.dim, *part]):
                            vecs.setdefault(r[0], r[1])
                        for r in conn.execute(
                                f"SELECT checksum, vector FROM note_vectors_carry WHERE model = ? "
                                f"AND dim = ? AND checksum IN ({_ph(len(part))})",
                                [model, emb.dim, *part]):
                            vecs.setdefault(r[0], r[1])
            finally:
                conn.commit()
            if need:
                keys = list(need)
                stats["reused"] += len(vecs)
                todo = [k for k in keys if k not in vecs]
                for i in range(0, len(todo), BATCH_CHUNKS):
                    if stop and stop():
                        raise _Stopped()
                    part = todo[i:i + BATCH_CHUNKS]
                    mat = emb.embed_passages([need[k] for k in part])
                    if mat.shape != (len(part), emb.dim):
                        raise ValueError(f"the model returned vectors of shape {mat.shape}")
                    for k, row in zip(part, _normalise(mat)):
                        vecs[k] = row.astype(np.float32).tobytes()
                    stats["embedded"] += len(part)

            done = {"notes": 0, "chunks": 0, "unchanged": 0}

            def write() -> None:
                done.update(notes=0, chunks=0, unchanged=0)
                now = time.time()
                for eid, p in plans.items():
                    if conn.execute("SELECT 1 FROM entities WHERE id = ?", (eid,)).fetchone() is None:
                        continue  # deleted meanwhile
                    n = len(p["chunks"])
                    conn.execute("DELETE FROM note_vectors WHERE entity_id = ? AND chunk_no >= ?",
                                 (eid, n))
                    for no, cs in enumerate(p["sums"]):
                        old = existing.get((eid, no))
                        if old is not None and old == (cs, model, emb.dim):
                            done["unchanged"] += 1
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO note_vectors (entity_id, chunk_no, model, dim, "
                            "checksum, vector, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (eid, no, model, emb.dim, cs, vecs[cs], now))
                    conn.execute(
                        "INSERT OR REPLACE INTO note_embed_state (entity_id, file_checksum, "
                        "text_checksum, model, chunks, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (eid, p["file"], p["text"], model, n, now))
                    done["notes"] += 1
                    done["chunks"] += n
                used = [k for k in vecs]
                for i in range(0, len(used), 500):
                    part = used[i:i + 500]
                    conn.execute(f"DELETE FROM note_vectors_carry WHERE model = ? "
                                 f"AND checksum IN ({_ph(len(part))})", [model, *part])

            _write_retry(conn, write)
            for k, v in done.items():
                stats[k] += v
            if progress:
                progress(min(start + len(group), total), total)
    finally:
        conn.close()
    return stats


def prune_carry(db_path: Path) -> None:
    """Drop re-use copies older than ``CARRY_DAYS`` (and beyond ``CARRY_MAX_ROWS``)."""
    try:
        conn = _connect(Path(db_path))
        try:
            if not _has_tables(conn):
                return
            cutoff = int(time.time()) - CARRY_DAYS * 86400

            def work() -> None:
                conn.execute("DELETE FROM note_vectors_carry WHERE added_at < ?", (cutoff,))
                conn.execute("DELETE FROM note_vectors_carry WHERE rowid IN (SELECT rowid FROM "
                             "note_vectors_carry ORDER BY added_at DESC LIMIT -1 OFFSET ?)",
                             (CARRY_MAX_ROWS,))

            _write_retry(conn, work)
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.debug("carry prune skipped: %s", e)


# ── Background worker (embed on write) ──────────────────────────────────────

_LAST: Dict[str, Dict[str, Any]] = {}   # db path → last run / last error


class _Worker:
    """One thread; embeds queued notes one batch at a time."""

    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.pending: "OrderedDict[str, Optional[set]]" = OrderedDict()   # None = all stale
        self.busy = False
        self.current: Optional[str] = None   # base being embedded now
        self.stopping = False
        self.thread: Optional[threading.Thread] = None

    def submit(self, db_path: str, ids: Optional[Iterable[int]]) -> None:
        with self.cv:
            if self.stopping:
                return
            cur = self.pending.get(db_path, set())
            if ids is None or cur is None:
                self.pending[db_path] = None
            else:
                cur.update(int(i) for i in ids)
                self.pending[db_path] = None if len(cur) > QUEUE_CAP else cur
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="kb-embed-worker",
                                               daemon=True)
                self.thread.start()
            self.cv.notify_all()

    def queued(self, db_path: str) -> int:
        """Queued notes of this base (-1: every note not embedded yet, or a run
        of unknown size in progress)."""
        with self.cv:
            v = self.pending.get(db_path, set())
            running = self.current == db_path
        if v is None or (running and not v):
            return -1
        return len(v)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        with self.cv:
            while self.pending or self.busy:
                left = end - time.time()
                if left <= 0:
                    return False
                self.cv.wait(left)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        with self.cv:
            self.stopping = True
            self.pending.clear()
            self.cv.notify_all()
            t = self.thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)

    def _run(self) -> None:
        while True:
            with self.cv:
                while not self.pending and not self.stopping:
                    self.cv.wait()
                if self.stopping:
                    return
                self.busy = True
            try:
                if DEBOUNCE_SECONDS:
                    time.sleep(DEBOUNCE_SECONDS)
                with self.cv:
                    if not self.pending:
                        continue
                    path, ids = self.pending.popitem(last=False)
                    self.current = path
                self._process(path, ids)
            except Exception as e:  # never let the thread die
                _log_once(f"worker:{type(e).__name__}:{e}", logging.WARNING,
                          "Knowledge: embedding notes failed: %s", e)
            finally:
                with self.cv:
                    self.busy = False
                    self.current = None
                    self.cv.notify_all()

    def _process(self, path: str, ids: Optional[set]) -> None:
        if not wants_updates():
            return
        emb = None
        end = time.time() + 1800   # first load may download the model
        while emb is None and time.time() < end and not self.stopping and wants_updates():
            emb = get_embedder(wait=1.0)
            state, err = embedder_state()
            if emb is None and state == "error":
                _LAST.setdefault(path, {})["last_error"] = err
                return  # logged once by the loader; the notes stay "not embedded"
        if emb is None:
            return
        try:
            if ids is None:
                conn = _connect(Path(path))
                try:
                    todo = _stale_ids(conn, emb.model) if _has_tables(conn) else []
                finally:
                    conn.close()
            else:
                todo = sorted(ids)
            stats = embed_notes(Path(path), todo, emb,
                                stop=lambda: self.stopping or not wants_updates())
            _LAST[path] = {"last_run": time.time(), "last_stats": stats, "last_error": None}
        except _Stopped:
            pass
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            _LAST.setdefault(path, {})["last_error"] = msg
            _log_once(f"embed:{path}:{msg}", logging.WARNING,
                      "Knowledge: embedding notes of %s failed: %s", path, msg)


_WORKER = _Worker()


def _stop_worker() -> None:
    try:
        _WORKER.stop(5.0)
    except Exception:
        pass


atexit.register(_stop_worker)


def wants_updates() -> bool:
    """Embed on write: switched on, knowledge base on, library present."""
    return enabled() and bool(_OPTIONS["kb_enabled"]) and library_installed()


def note_indexed(db_path: Any, entity_ids: Iterable[int]) -> None:
    """Called after notes were (re-)indexed and committed. Never raises, never blocks."""
    try:
        if not wants_updates():
            return
        ids = [int(i) for i in entity_ids if i]
        if ids:
            _WORKER.submit(str(db_path), ids)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("embedding queue: %s", e)


def queue_all(db_path: Any) -> None:
    """Queue every stale note of a base (e.g. after turning the feature on)."""
    if wants_updates():
        _WORKER.submit(str(db_path), None)


def wait_idle(timeout: float = 30.0) -> bool:
    """Tests / CLI: wait until queued notes are embedded. False on timeout."""
    return _WORKER.wait_idle(timeout)


# ── Backfill + status ───────────────────────────────────────────────────────

def _db_path(base: str, db_path: Optional[Path]) -> Path:
    from .db import KnowledgeDB

    with KnowledgeDB(base, db_path=db_path) as db:   # creates the tables if needed
        return Path(db.db_path)


def unavailable_reason(for_search: bool = True) -> Optional[str]:
    """Why semantic search cannot be used now (None = it can)."""
    if for_search and not enabled():
        return ("semantic search is off — turn on Settings → Features → "
                "\"Semantic search\" (knowledge.semantic_search)")
    if not library_installed():
        return f"fastembed is not installed — install with: {INSTALL_HINT}"
    state, err = embedder_state()
    if state == "error" and err:
        return err
    return None


def backfill_embeddings(base: str = "default", limit: Optional[int] = None,
                        all_notes: bool = False, db_path: Optional[Path] = None,
                        progress: Optional[Callable[[int, int], None]] = None,
                        stop: Optional[Callable[[], bool]] = None,
                        wait_load: float = 1800.0) -> Dict[str, Any]:
    """Embed the notes that have no (current) vectors; ``all_notes`` checks every
    note (unchanged chunks are still not re-embedded). Works with the switch
    off too (so the vectors are ready when it is turned on). Blocking."""
    if not library_installed():
        return {"ok": False, "message": f"fastembed is not installed — install with: {INSTALL_HINT}"}
    path = _db_path(base, db_path)
    if progress:
        progress(0, 0)
    emb = get_embedder(wait=wait_load, retry=True)
    if emb is None:
        state, err = embedder_state()
        msg = err or ("The embedding model is still loading (the first time it is downloaded, "
                      f"about {model_info().size_mb or '?'} MB) — try again in a minute.")
        _LAST.setdefault(str(path), {})["last_error"] = msg
        return {"ok": False, "message": msg}
    conn = _connect(path)
    try:
        ids = _stale_ids(conn, emb.model, all_notes=all_notes, limit=limit)
    finally:
        conn.close()
    try:
        stats = embed_notes(path, ids, emb, progress=progress, stop=stop)
    except _Stopped:
        return {"ok": False, "message": "Stopped.", "stopped": True}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        _LAST.setdefault(str(path), {})["last_error"] = msg
        logger.warning("Knowledge: backfill of %s failed: %s", base, msg)
        return {"ok": False, "message": f"Embedding failed: {msg}"}
    prune_carry(path)
    _LAST[str(path)] = {"last_run": time.time(), "last_stats": stats, "last_error": None}
    return {"ok": True, "checked": len(ids), **stats,
            "message": (f"Embedded {stats['notes']} note(s): {stats['embedded']} chunk(s) "
                        f"through the model, {stats['reused']} re-used.")}


def embedding_status(base: str = "default", db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Counts and state for the status card / CLI / doctor."""
    path = _db_path(base, db_path)
    model = model_name()
    info = model_info(model)
    out: Dict[str, Any] = {
        "base": base, "enabled": enabled(), "installed": library_installed(),
        "model": model, "model_info": {**asdict(info), "known": model in MODELS},
        "models": {k: asdict(v) for k, v in MODELS.items()},
        "install_command": f'python -m pip install "{INSTALL_SPEC}"',
        "notes": 0, "embedded": 0, "stale": 0, "chunks": 0, "other_model_chunks": 0,
        "queued": _WORKER.queued(str(path)),
        "embedder": embedder_state()[0],
        "last_error": None, "last_run": None, "ready": False, "reason": None,
    }
    conn = _connect(path)
    try:
        out["notes"] = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        if _has_tables(conn):
            out["stale"] = len(_stale_ids(conn, model))
            out["chunks"] = conn.execute("SELECT COUNT(*) FROM note_vectors WHERE model = ?",
                                         (model,)).fetchone()[0]
            out["other_model_chunks"] = conn.execute(
                "SELECT COUNT(*) FROM note_vectors WHERE model != ?", (model,)).fetchone()[0]
        else:
            out["stale"] = out["notes"]
    finally:
        conn.close()
    out["embedded"] = max(0, out["notes"] - out["stale"])
    last = _LAST.get(str(path), {})
    out["last_error"] = last.get("last_error") or embedder_state()[1]
    out["last_run"] = last.get("last_run")
    out["ready"] = bool(out["enabled"] and out["installed"] and out["chunks"] > 0)
    out["reason"] = unavailable_reason() or (None if out["chunks"] else
                                             "no note is embedded yet — run Backfill")
    return out


# ── Search ──────────────────────────────────────────────────────────────────

_MATS: "OrderedDict[Tuple[str, str, int], Tuple[Any, np.ndarray, np.ndarray]]" = OrderedDict()
_MLOCK = threading.Lock()


def _reset_caches() -> None:
    with _H.lock:
        _H.gen += 1
        _H.model, _H.obj, _H.state, _H.error = None, None, "idle", None
        _H.ready = threading.Event()
    with _QLOCK:
        _QCACHE.clear()
        _INFLIGHT.clear()
    with _MLOCK:
        _MATS.clear()


def _matrix(conn: sqlite3.Connection, path: str, model: str, dim: int
            ) -> Tuple[np.ndarray, np.ndarray]:
    """(entity ids, vectors) of ``model``; cached until the table changes."""
    sig = tuple(conn.execute("SELECT COUNT(*), MAX(updated_at), TOTAL(entity_id) FROM note_vectors "
                             "WHERE model = ? AND dim = ?", (model, dim)).fetchone())
    key = (path, model, dim)
    with _MLOCK:
        hit = _MATS.get(key)
        if hit is not None and hit[0] == sig:
            _MATS.move_to_end(key)
            return hit[1], hit[2]
    if sig[0] > MAX_SCAN_CHUNKS:
        _log_once(f"cap:{path}", logging.WARNING,
                  "Knowledge: %s has %d vectors; semantic search scans the newest %d",
                  path, sig[0], MAX_SCAN_CHUNKS)
    rows = conn.execute("SELECT entity_id, vector FROM note_vectors WHERE model = ? AND dim = ? "
                        "ORDER BY updated_at DESC LIMIT ?", (model, dim, MAX_SCAN_CHUNKS)).fetchall()
    ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
    mat = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), dim)
           if rows else np.zeros((0, dim), np.float32))
    with _MLOCK:
        _MATS[key] = (sig, ids, mat)
        _MATS.move_to_end(key)
        while len(_MATS) > 4:
            _MATS.popitem(last=False)
    return ids, mat


def vector_rank(conn: sqlite3.Connection, path: Any, query: str, limit: int,
                timeout: float = SEARCH_TIMEOUT, wait_load: float = 0.0
                ) -> Tuple[Optional[List[Tuple[int, float]]], Optional[str]]:
    """Notes by similarity to ``query``: ([(entity_id, score)], None) or (None, reason)."""
    reason = unavailable_reason()
    if reason:
        return None, reason
    if not _has_tables(conn):
        return None, "no note is embedded yet — run Backfill"
    model = model_name()
    if conn.execute("SELECT 1 FROM note_vectors WHERE model = ? LIMIT 1", (model,)).fetchone() is None:
        return None, "no note is embedded yet — run Backfill"
    q = query_vector(query, timeout=timeout, wait_load=wait_load)
    if q is None:
        state, err = embedder_state()
        if state == "error" and err:
            return None, err
        if state == "loading":
            return None, "the embedding model is loading — try again in a moment"
        return None, f"the query embedding took longer than {timeout:g} s"
    ids, mat = _matrix(conn, str(path), model, int(q.shape[0]))
    if not len(ids):
        return None, "no note is embedded yet — run Backfill"
    scores = mat @ q
    floor = min_score()
    out: List[Tuple[int, float]] = []
    seen: set = set()
    for i in np.argsort(-scores):
        s = float(scores[i])
        if s < floor:
            break
        eid = int(ids[i])
        if eid in seen:
            continue
        seen.add(eid)
        out.append((eid, s))
        if len(out) >= limit:
            break
    return out, None


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> List[Tuple[int, float]]:
    """Reciprocal Rank Fusion: score = Σ 1 / (k + rank); ties keep first-seen order."""
    scores: Dict[int, float] = {}
    order: Dict[int, int] = {}
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking)):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            order.setdefault(key, len(order))
    return sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))


def normalise_mode(mode: Any) -> Optional[str]:
    m = str(mode or "auto").strip().lower()
    aliases = {"words": "fts", "text": "fts", "keyword": "fts", "keywords": "fts", "lexical": "fts",
               "meaning": "semantic", "vector": "semantic", "vectors": "semantic",
               "both": "hybrid", "rrf": "hybrid"}
    m = aliases.get(m, m)
    return m if m in MODES else None


def _fts_only_syntax(query: str) -> bool:
    from .db import _has_boolean_syntax

    return _has_boolean_syntax(query or "") or bool(re.search(r"\w\*", query or ""))


def search_entities(db: Any, query: str, limit: int = 10, boolean: bool = False,
                    mode: str = "fts", timeout: Optional[float] = None,
                    wait_load: Optional[float] = None,
                    info: Optional[Dict[str, Any]] = None) -> List[Any]:
    """``db.search_fts`` plus vectors. ``info`` (optional) gets ``mode`` (what
    was used) and ``fallback`` (why semantic search was not used)."""
    info = info if info is not None else {}
    m = normalise_mode(mode) or "auto"
    info["requested"] = m
    if m == "fts" or (m == "auto" and not enabled()):
        info["mode"] = "fts"
        return db.search_fts(query, limit, boolean=boolean)
    if _fts_only_syntax(query):
        info.update(mode="fts", fallback="AND / OR / NOT, \"phrases\" and prefix* use word search")
        return db.search_fts(query, limit, boolean=boolean)
    t = SEARCH_TIMEOUT if timeout is None else float(timeout)
    wl = t if wait_load is None else float(wait_load)
    conn = db._get_connection()
    fetch = max(limit * 2, 20)
    vec, reason = vector_rank(conn, db.db_path, query, fetch, timeout=t, wait_load=wl)
    if vec is None:
        info.update(mode="fts", fallback=reason)
        return db.search_fts(query, limit, boolean=boolean)
    fts = db.search_fts(query, fetch, boolean=boolean) if m != "semantic" else []
    by_id = {e.id: e for e in fts}
    if m == "semantic":
        ranked = [eid for eid, _ in vec]
        info["mode"] = "semantic"
    else:
        ranked = [eid for eid, _ in rrf([[e.id for e in fts], [eid for eid, _ in vec]])]
        info["mode"] = "hybrid"
    info["semantic_hits"] = len(vec)
    ranked = ranked[:limit]
    missing = [i for i in ranked if i not in by_id]
    if missing:
        from .db import _row_to_entity

        for r in conn.execute(f"SELECT * FROM entities WHERE id IN ({_ph(len(missing))})", missing):
            by_id[r["id"]] = _row_to_entity(r)
    return [by_id[i] for i in ranked if i in by_id]


def context_search_kwargs(info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extra ``search_notes`` arguments for the automatic agent context: none
    when semantic search is off (the call stays exactly as before)."""
    if not enabled():
        return {}
    kw: Dict[str, Any] = {"mode": "auto", "semantic_timeout": AGENT_TIMEOUT}
    if info is not None:
        kw["info"] = info
    return kw
