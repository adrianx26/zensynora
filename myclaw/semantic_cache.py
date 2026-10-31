"""
Semantic LLM Response Cache - Optimization #2

Provides semantic similarity-based caching for LLM responses.
Uses embeddings to find similar queries and cache their responses,
significantly reducing API costs for repeated or similar queries.
"""

import hashlib
import time
import logging
import json
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass
from collections import OrderedDict
import numpy as np

logger = logging.getLogger(__name__)


FASTEMBED_TIMEOUT = 2.0  # seconds a cache lookup may wait for an embedding
_NO_MODEL_LOGGED = False


@dataclass
class CacheEntry:
    """A cached LLM response with its embedding."""

    query_embedding: np.ndarray
    response: str
    tool_calls: Optional[List[Dict]]
    timestamp: float
    access_count: int = 0
    query_hash: str = ""
    model: str = ""  # v0.7.3: similarity matches only within the same model


class SemanticCache:
    """
    Semantic similarity-based cache for LLM responses.

    Uses sentence embeddings to find similar queries even when
    the exact wording differs. Perfect for:
    - Repeated questions with different phrasing
    - Code snippets with minor variations
    - FAQ-style queries

    Features:
    - Configurable similarity threshold
    - TTL support for cache entries
    - LRU eviction when max size reached
    - Embedding model lazy loading
    """

    def __init__(
        self,
        max_size: int = 256,
        ttl: int = 3600,
        similarity_threshold: float = 0.92,
        embedding_model: str = "all-MiniLM-L6-v2",
        cache_dir: Optional[Path] = None,
        max_scan_entries: int = 256,
    ):
        self.max_size = max_size
        self.ttl = ttl
        self.similarity_threshold = similarity_threshold
        self.embedding_model = embedding_model
        # PERF FIX (2026-04-23): Limit linear scan to avoid latency spikes
        # with large caches. We scan most-recent entries first (LRU tail).
        self.max_scan_entries = max_scan_entries

        # Cache storage
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._embedding_model = None
        self._model_loaded = False
        # 2026-10-31: "sentence-transformers" (PyTorch, several GB) or "fastembed"
        # (ONNX, the knowledge base's model, shared) or None (exact matches only)
        self._backend: Optional[str] = None

        # Stats
        self.hits = 0
        self.misses = 0
        self.saves = 0

        # Optional persistent cache
        self.cache_dir = cache_dir or (Path.home() / ".myclaw" / "semantic_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "cache.json"

        # Load from disk if exists
        self._load_cache()

    def _load_cache(self):
        """Load cache from disk."""
        if not self.cache_file.exists():
            return

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, entry_data in data.items():
                entry = CacheEntry(
                    query_embedding=np.array(entry_data["embedding"]),
                    response=entry_data["response"],
                    tool_calls=entry_data.get("tool_calls"),
                    timestamp=entry_data["timestamp"],
                    access_count=entry_data.get("access_count", 0),
                    query_hash=entry_data.get("query_hash", ""),
                    model=entry_data.get("model", ""),
                )
                self._cache[key] = entry

            logger.info(f"Loaded {len(self._cache)} entries from semantic cache")
        except Exception as e:
            logger.warning(f"Failed to load semantic cache: {e}")

    def _save_cache(self):
        """Persist cache to disk."""
        try:
            data = {}
            for key, entry in self._cache.items():
                data[key] = {
                    "embedding": entry.query_embedding.tolist(),
                    "response": entry.response,
                    "tool_calls": entry.tool_calls,
                    "timestamp": entry.timestamp,
                    "access_count": entry.access_count,
                    "query_hash": entry.query_hash,
                    "model": entry.model,
                }

            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to save semantic cache: {e}")

    def _load_embedding_model(self):
        """Lazy load the embedding model with memory optimization."""
        if self._model_loaded:
            return

        global _NO_MODEL_LOGGED
        import importlib.util

        if importlib.util.find_spec("sentence_transformers") is None:
            # 2026-10-31: sentence-transformers pulls in PyTorch (several GB), so it
            # is not a dependency. The knowledge base's fastembed model (ONNX, ~67 MB,
            # one per process, run on the shared inference thread) is used instead.
            from .knowledge import semantic as kb_semantic

            self._model_loaded = True
            if kb_semantic.library_installed():
                self._backend = "fastembed"
                logger.info("Semantic cache: using the knowledge base's embedding model (fastembed)")
            elif not _NO_MODEL_LOGGED:
                _NO_MODEL_LOGGED = True
                logger.info("Semantic cache: no embedding library installed — only identical questions are "
                            "answered from the cache. Install fastembed (Settings → Knowledge → semantic "
                            "search, or pip install fastembed) to also match similar questions.")
            return

        try:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading embedding model: {self.embedding_model}")

            # Use CPU only and limit threads to reduce memory usage
            try:
                import torch

                torch.set_num_threads(4)
                device = "cpu"
            except ImportError:
                device = "cpu"

            self._embedding_model = SentenceTransformer(self.embedding_model, device=device)
            self._backend = "sentence-transformers"
            self._model_loaded = True
            logger.info("Embedding model loaded successfully")
        except Exception as e:  # broken torch install etc.: exact matches still work
            logger.info("Semantic cache: sentence-transformers could not be loaded (%s); "
                        "only identical questions are answered from the cache", e)
            self._model_loaded = True  # Mark as loaded to prevent re-attempts

    def _cleanup_embedding_model(self):
        """Clean up embedding model to free memory."""
        if self._embedding_model is not None:
            try:
                import gc

                del self._embedding_model
                gc.collect()

                # Try to clear torch cache if available
                try:
                    import torch

                    if hasattr(torch, "cuda"):
                        torch.cuda.empty_cache()
                except ImportError:
                    pass

                self._embedding_model = None
                self._model_loaded = False
                logger.debug("Embedding model cleaned up")
            except Exception as e:
                logger.warning(f"Failed to cleanup embedding model: {e}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self._cleanup_embedding_model()
        return False

    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding for text using the model."""
        self._load_embedding_model()

        if self._backend == "fastembed":
            from .knowledge import semantic as kb_semantic

            try:
                # None while the model is still loading: this lookup is exact-match only
                vec = kb_semantic.query_vector(text, timeout=FASTEMBED_TIMEOUT)
            except Exception as e:
                logger.info("Semantic cache: embedding failed: %s", e)
                return None
            return None if vec is None else np.asarray(vec, dtype=np.float32)

        if self._embedding_model is None:
            return None

        try:
            embedding = self._embedding_model.encode(text, convert_to_numpy=True)
            return embedding
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return None

    def _compute_hash(self, messages: List[Dict], model: str = "") -> str:
        """Compute a hash of the request for exact matching.

        Fixed 2026-10-14 (v0.7.3): the key used to ignore the model, the
        system prompt and tool calls/results. Different agents and models got
        each other's cached answers, and a job re-run with the same prompt got
        the previous run's reply (including failed ones) instead of trying
        again. The key now covers all of them.
        """
        normalized = []
        for m in messages:
            entry = {"role": m.get("role", ""),
                     "content": " ".join(str(m.get("content") or "").split())}
            if m.get("tool_calls"):
                entry["tool_calls"] = json.dumps(m["tool_calls"], sort_keys=True, default=str)
            if m.get("tool_call_id"):
                entry["tool_call_id"] = m["tool_call_id"]
            normalized.append(entry)

        content = json.dumps({"model": model or "", "messages": normalized}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def _compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Compute cosine similarity between two embeddings."""
        dot_product = np.dot(emb1, emb2)
        norm1 = np.linalg.norm(emb1)
        norm2 = np.linalg.norm(emb2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def _is_expired(self, entry: CacheEntry) -> bool:
        """Check if a cache entry has expired."""
        return time.time() - entry.timestamp > self.ttl

    def get(self, messages: List[Dict], model: str) -> Optional[Tuple[str, Optional[List[Dict]]]]:
        """
        Look up a cached response for the given messages.

        Args:
            messages: List of message dicts (role, content)
            model: Model name for additional cache key context

        Returns:
            Tuple of (response, tool_calls) if found, None otherwise
        """
        query_hash = self._compute_hash(messages, model)

        # First, try exact hash match
        if query_hash in self._cache:
            entry = self._cache[query_hash]
            if not self._is_expired(entry):
                self.hits += 1
                entry.access_count += 1
                self._cache.move_to_end(query_hash)
                logger.debug(f"Semantic cache hit (exact): {query_hash[:8]}...")
                # Record Prometheus metric
                try:
                    from .metrics import get_metrics

                    get_metrics().record_cache("semantic", hit=True)
                except Exception:
                    pass
                return entry.response, entry.tool_calls

        # Try semantic similarity match (2026-10-31: also with the fastembed backend)
        self._load_embedding_model()
        if self._embedding_model is not None or self._backend == "fastembed":
            # Construct query text from messages
            query_text = self._extract_query_text(messages)
            query_embedding = self._get_embedding(query_text)

            if query_embedding is not None:
                best_match = None
                best_similarity = 0.0

                # PERF (2026-04-30): vectorized similarity scan.
                #
                # Original: per-entry Python loop calling np.dot/np.linalg.norm
                # twice per comparison. With max_scan_entries raised to 256
                # this would be ~1.5ms/scan; batched matrix multiply is ~50µs
                # for the same shape on modern CPUs.
                #
                # We still walk the LRU tail (newest first) for the eviction
                # of expired entries; only the similarity computation itself
                # is batched.
                entries_to_remove = []
                scan_keys = []
                scan_vectors = []
                # OrderedDict: oldest first, newest last. Reverse to check newest first.
                for key, entry in reversed(list(self._cache.items())):
                    if len(scan_keys) >= self.max_scan_entries:
                        break
                    if self._is_expired(entry):
                        entries_to_remove.append(key)
                        continue
                    if entry.model != (model or ""):
                        continue
                    # 2026-10-31: entries made with another embedding model have
                    # another size — skip them instead of failing the whole scan
                    if getattr(entry.query_embedding, "shape", None) != query_embedding.shape:
                        continue
                    scan_keys.append(key)
                    scan_vectors.append(entry.query_embedding)

                if scan_vectors:
                    # Build a (N, D) matrix and compute cosine similarity in one shot.
                    matrix = np.asarray(scan_vectors)
                    q_norm = np.linalg.norm(query_embedding)
                    row_norms = np.linalg.norm(matrix, axis=1)
                    # Avoid divide-by-zero — entries with degenerate embeddings
                    # contribute zero similarity.
                    denom = q_norm * row_norms
                    safe_denom = np.where(denom == 0.0, 1.0, denom)
                    sims = matrix.dot(query_embedding) / safe_denom
                    sims = np.where(denom == 0.0, 0.0, sims)

                    # argmax wins — but we still need the threshold filter.
                    best_idx = int(np.argmax(sims))
                    best_sim_value = float(sims[best_idx])
                    if best_sim_value >= self.similarity_threshold:
                        best_match = scan_keys[best_idx]
                        best_similarity = best_sim_value

                for key in entries_to_remove:
                    self._cache.pop(key, None)

                if best_match:
                    entry = self._cache[best_match]
                    self.hits += 1
                    entry.access_count += 1
                    self._cache.move_to_end(best_match)
                    logger.debug(
                        f"Semantic cache hit (similarity={best_similarity:.3f}): {best_match[:8]}..."
                    )
                    try:
                        from .metrics import get_metrics

                        get_metrics().record_cache("semantic", hit=True)
                    except Exception:
                        pass
                    return entry.response, entry.tool_calls

        self.misses += 1
        try:
            from .metrics import get_metrics

            get_metrics().record_cache("semantic", hit=False)
        except Exception:
            pass
        return None

    def _extract_query_text(self, messages: List[Dict]) -> str:
        """Extract the user query text from messages."""
        parts = []
        for m in messages:
            if m.get("role") == "user":
                parts.append(m.get("content", ""))
            elif m.get("role") == "assistant" and not m.get("content", "").startswith("Tool"):
                # Include last assistant response for context
                parts.append(m.get("content", ""))

        # Return the last user message as the primary query
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if user_msgs:
            return user_msgs[-1].get("content", "")

        return " ".join(parts[:2]) if parts else ""

    def set(
        self,
        messages: List[Dict],
        model: str,
        response: str,
        tool_calls: Optional[List[Dict]] = None,
    ):
        """
        Cache a response for the given messages.

        Args:
            messages: List of message dicts
            model: Model name
            response: The LLM response text
            tool_calls: Optional list of tool calls
        """
        # v0.7.3: never cache an empty answer (it would be served again).
        if not (response or "").strip() and not tool_calls:
            return
        query_hash = self._compute_hash(messages, model)
        query_text = self._extract_query_text(messages)

        # Get embedding if model available
        embedding = self._get_embedding(query_text) if query_text else None

        entry = CacheEntry(
            query_embedding=embedding if embedding is not None else np.zeros(384),
            response=response,
            tool_calls=tool_calls,
            timestamp=time.time(),
            query_hash=query_hash,
            model=model or "",
        )

        # Evict oldest if at capacity
        if len(self._cache) >= self.max_size and query_hash not in self._cache:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            logger.debug(f"Evicted oldest cache entry: {oldest_key[:8]}...")

        self._cache[query_hash] = entry
        self._cache.move_to_end(query_hash)
        self.saves += 1

        # Periodically persist to disk
        if self.saves % 10 == 0:
            self._save_cache()

        logger.debug(f"Cached response for: {query_hash[:8]}...")

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()
        self.hits = 0
        self.misses = 0
        self.saves = 0
        if self.cache_file.exists():
            self.cache_file.unlink()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            "entries": len(self._cache),
            "hits": self.hits,
            "misses": self.misses,
            "saves": self.saves,
            "hit_rate": f"{hit_rate:.1f}%",
            "model": self.embedding_model if self._model_loaded else "not loaded",
            "ttl": self.ttl,
            "similarity_threshold": self.similarity_threshold,
        }

    def save(self):
        """Persist cache to disk."""
        self._save_cache()


# Global semantic cache instance
_semantic_cache: Optional[SemanticCache] = None


def get_semantic_cache(
    max_size: int = 256, ttl: int = 3600, similarity_threshold: float = 0.92
) -> SemanticCache:
    """Get or create the global semantic cache instance."""
    global _semantic_cache

    if _semantic_cache is None:
        _semantic_cache = SemanticCache(
            max_size=max_size, ttl=ttl, similarity_threshold=similarity_threshold
        )

    return _semantic_cache


def clear_semantic_cache():
    """Clear the global semantic cache."""
    global _semantic_cache

    if _semantic_cache is not None:
        _semantic_cache.clear()
        _semantic_cache = None


# ── Public API surface ───────────────────────────────────────────────
# Listing __all__ explicitly so `from this_module import *` doesn't leak
# internal helpers (e.g. _profile_cache, _LAST_ACTIVE_TIME). Names that
# aren't here are still importable by direct attribute access — they
# just don't participate in star imports.
__all__ = ['SemanticCache', 'CacheEntry', 'get_semantic_cache']
