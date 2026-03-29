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


@dataclass
class CacheEntry:
    """A cached LLM response with its embedding."""
    query_embedding: np.ndarray
    response: str
    tool_calls: Optional[List[Dict]]
    timestamp: float
    access_count: int = 0
    query_hash: str = ""


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
        cache_dir: Optional[Path] = None
    ):
        self.max_size = max_size
        self.ttl = ttl
        self.similarity_threshold = similarity_threshold
        self.embedding_model = embedding_model
        
        # Cache storage
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._embedding_model = None
        self._model_loaded = False
        
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
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for key, entry_data in data.items():
                entry = CacheEntry(
                    query_embedding=np.array(entry_data['embedding']),
                    response=entry_data['response'],
                    tool_calls=entry_data.get('tool_calls'),
                    timestamp=entry_data['timestamp'],
                    access_count=entry_data.get('access_count', 0),
                    query_hash=entry_data.get('query_hash', '')
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
                    'embedding': entry.query_embedding.tolist(),
                    'response': entry.response,
                    'tool_calls': entry.tool_calls,
                    'timestamp': entry.timestamp,
                    'access_count': entry.access_count,
                    'query_hash': entry.query_hash
                }
            
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to save semantic cache: {e}")
    
    def _load_embedding_model(self):
        """Lazy load the embedding model."""
        if self._model_loaded:
            return
        
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.embedding_model}")
            self._embedding_model = SentenceTransformer(self.embedding_model)
            self._model_loaded = True
            logger.info("Embedding model loaded successfully")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. "
                "Semantic cache will use hash-based matching only. "
                "Install with: pip install sentence-transformers"
            )
            self._model_loaded = True  # Mark as loaded to prevent re-attempts
    
    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding for text using the model."""
        self._load_embedding_model()
        
        if self._embedding_model is None:
            return None
        
        try:
            embedding = self._embedding_model.encode(text, convert_to_numpy=True)
            return embedding
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return None
    
    def _compute_hash(self, messages: List[Dict]) -> str:
        """Compute a hash of the messages for exact matching."""
        # Normalize messages by removing timestamps and extra whitespace
        normalized = []
        for m in messages:
            if m.get('role') == 'system':
                continue  # Skip system prompts for hashing
            normalized.append({
                'role': m.get('role', ''),
                'content': ' '.join(m.get('content', '').split())
            })
        
        content = json.dumps(normalized, sort_keys=True)
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
        query_hash = self._compute_hash(messages)
        
        # First, try exact hash match
        if query_hash in self._cache:
            entry = self._cache[query_hash]
            if not self._is_expired(entry):
                self.hits += 1
                entry.access_count += 1
                self._cache.move_to_end(query_hash)
                logger.debug(f"Semantic cache hit (exact): {query_hash[:8]}...")
                return entry.response, entry.tool_calls
        
        # Try semantic similarity match
        if self._embedding_model is not None:
            # Construct query text from messages
            query_text = self._extract_query_text(messages)
            query_embedding = self._get_embedding(query_text)
            
            if query_embedding is not None:
                best_match = None
                best_similarity = 0.0
                
                # Scan all non-expired entries
                for key, entry in list(self._cache.items()):
                    if self._is_expired(entry):
                        continue
                    
                    similarity = self._compute_similarity(query_embedding, entry.query_embedding)
                    
                    if similarity > best_similarity and similarity >= self.similarity_threshold:
                        best_similarity = similarity
                        best_match = key
                
                if best_match:
                    entry = self._cache[best_match]
                    self.hits += 1
                    entry.access_count += 1
                    self._cache.move_to_end(best_match)
                    logger.debug(f"Semantic cache hit (similarity={best_similarity:.3f}): {best_match[:8]}...")
                    return entry.response, entry.tool_calls
        
        self.misses += 1
        return None
    
    def _extract_query_text(self, messages: List[Dict]) -> str:
        """Extract the user query text from messages."""
        parts = []
        for m in messages:
            if m.get('role') == 'user':
                parts.append(m.get('content', ''))
            elif m.get('role') == 'assistant' and not m.get('content', '').startswith('Tool'):
                # Include last assistant response for context
                parts.append(m.get('content', ''))
        
        # Return the last user message as the primary query
        user_msgs = [m for m in messages if m.get('role') == 'user']
        if user_msgs:
            return user_msgs[-1].get('content', '')
        
        return ' '.join(parts[:2]) if parts else ''
    
    def set(
        self,
        messages: List[Dict],
        model: str,
        response: str,
        tool_calls: Optional[List[Dict]] = None
    ):
        """
        Cache a response for the given messages.
        
        Args:
            messages: List of message dicts
            model: Model name
            response: The LLM response text
            tool_calls: Optional list of tool calls
        """
        query_hash = self._compute_hash(messages)
        query_text = self._extract_query_text(messages)
        
        # Get embedding if model available
        embedding = self._get_embedding(query_text) if query_text else None
        
        entry = CacheEntry(
            query_embedding=embedding if embedding is not None else np.zeros(384),
            response=response,
            tool_calls=tool_calls,
            timestamp=time.time(),
            query_hash=query_hash
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
            "similarity_threshold": self.similarity_threshold
        }
    
    def save(self):
        """Persist cache to disk."""
        self._save_cache()


# Global semantic cache instance
_semantic_cache: Optional[SemanticCache] = None


def get_semantic_cache(
    max_size: int = 256,
    ttl: int = 3600,
    similarity_threshold: float = 0.92
) -> SemanticCache:
    """Get or create the global semantic cache instance."""
    global _semantic_cache
    
    if _semantic_cache is None:
        _semantic_cache = SemanticCache(
            max_size=max_size,
            ttl=ttl,
            similarity_threshold=similarity_threshold
        )
    
    return _semantic_cache


def clear_semantic_cache():
    """Clear the global semantic cache."""
    global _semantic_cache
    
    if _semantic_cache is not None:
        _semantic_cache.clear()
        _semantic_cache = None