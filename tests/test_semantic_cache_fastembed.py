"""2026-10-31: the semantic cache uses the knowledge base's fastembed model.

The Doctor showed 'sentence-transformers not installed. Semantic cache will use
hash-based matching only' (a WARNING on every start). sentence-transformers
needs PyTorch (several GB); fastembed (ONNX) is used instead, shared with the
knowledge base. Without either, only exact matches are cached and an INFO line
says so once.
"""

from __future__ import annotations

import importlib.util
import logging

import numpy as np
import pytest


@pytest.fixture
def fake_embedder(monkeypatch):
    from myclaw.knowledge import semantic

    semantic.set_embedder_factory(lambda model: semantic.HashEmbedder(model=model))
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "sentence_transformers" else real_find(name, *a))
    yield semantic
    semantic.set_embedder_factory(None)


def _cache(tmp_path):
    from myclaw.semantic_cache import SemanticCache

    return SemanticCache(cache_dir=tmp_path, similarity_threshold=0.8)


def _msgs(q):
    return [{"role": "user", "content": q}]


def _embedding(cache, text, tries=50):
    import time

    for _ in range(tries):  # the model loads in the background
        v = cache._get_embedding(text)
        if v is not None:
            return v
        time.sleep(0.05)
    return None


def test_similar_questions_hit_without_sentence_transformers(tmp_path, fake_embedder, caplog):
    cache = _cache(tmp_path)
    with caplog.at_level(logging.INFO):
        assert _embedding(cache, "warm up") is not None
    assert cache._backend == "fastembed"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    cache.set(_msgs("What is the weather in Bucharest today?"), "m", "Sunny")
    assert cache.get(_msgs("What is the weather in Bucharest today?"), "m")[0] == "Sunny"  # exact
    hit = cache.get(_msgs("what is the weather in bucharest today"), "m")                  # similar
    assert hit and hit[0] == "Sunny"
    assert cache.get(_msgs("Install the printer driver"), "m") is None


def test_no_embedding_library_is_info_once_and_exact_matches_work(tmp_path, monkeypatch, caplog):
    import myclaw.semantic_cache as sc
    from myclaw.knowledge import semantic

    monkeypatch.setattr(semantic, "library_installed", lambda: False)
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "sentence_transformers" else real_find(name, *a))
    monkeypatch.setattr(sc, "_NO_MODEL_LOGGED", False)
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            c = _cache(tmp_path)
            c.set(_msgs("hello"), "m", "hi")
            assert c.get(_msgs("hello"), "m")[0] == "hi"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len([r for r in caplog.records if "no embedding library" in r.getMessage()]) == 1


def test_entries_from_another_model_size_are_skipped(tmp_path, fake_embedder):
    from myclaw.semantic_cache import CacheEntry

    cache = _cache(tmp_path)
    assert _embedding(cache, "warm up") is not None
    cache._cache["old"] = CacheEntry(query_embedding=np.ones(384, dtype=np.float32), response="old",
                                     tool_calls=None, timestamp=9e18, model="m")
    cache.set(_msgs("tell me about routers"), "m", "new")
    assert cache.get(_msgs("tell me about routers please"), "m")[0] == "new"  # no shape error
