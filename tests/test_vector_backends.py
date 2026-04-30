"""Tests for vector backends (memory + sqlite + factory).

Qdrant is exercised by import only; full integration would require a
running server, which we don't spin up in unit tests.
"""

from pathlib import Path

import pytest

from myclaw.vector import (
    InMemoryBackend,
    SQLiteBackend,
    VectorRecord,
    cosine_similarity,
    make_backend,
)


# Two near-orthogonal vectors and one near-duplicate of the first, so we
# can predict ranking without floating-point fuss.
VEC_A = [1.0, 0.0, 0.0]
VEC_A_NEAR = [0.99, 0.01, 0.0]
VEC_B = [0.0, 1.0, 0.0]


# ── cosine_similarity sanity ──────────────────────────────────────────────


def test_cosine_identical_is_one():
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)


def test_cosine_handles_zero_vector():
    assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0


def test_cosine_mismatched_length_returns_zero():
    assert cosine_similarity([1, 0], [1, 0, 0]) == 0.0


# ── Shared backend test suite ─────────────────────────────────────────────
#
# Parametrize over both backends to ensure their behavior is identical.


@pytest.fixture(params=["memory", "sqlite"])
async def backend(request, tmp_path: Path):
    if request.param == "memory":
        be = InMemoryBackend()
    else:
        be = SQLiteBackend(db_path=tmp_path / "vec.db")
    yield be
    await be.clear()
    await be.close()


@pytest.mark.asyncio
async def test_upsert_then_count(backend):
    n = await backend.upsert([
        VectorRecord(id="a", vector=VEC_A),
        VectorRecord(id="b", vector=VEC_B),
    ])
    assert n == 2
    assert await backend.count() == 2


@pytest.mark.asyncio
async def test_search_ranks_by_similarity(backend):
    await backend.upsert([
        VectorRecord(id="a", vector=VEC_A),
        VectorRecord(id="b", vector=VEC_B),
        VectorRecord(id="a_near", vector=VEC_A_NEAR),
    ])
    hits = await backend.search(VEC_A, limit=3)
    assert [h.id for h in hits[:2]] == ["a", "a_near"]
    assert hits[2].id == "b"
    # Scores should descend.
    assert hits[0].score >= hits[1].score >= hits[2].score


@pytest.mark.asyncio
async def test_search_respects_limit(backend):
    await backend.upsert([
        VectorRecord(id=f"r{i}", vector=[1.0, float(i), 0.0]) for i in range(5)
    ])
    assert len(await backend.search(VEC_A, limit=2)) == 2


@pytest.mark.asyncio
async def test_metadata_filter(backend):
    await backend.upsert([
        VectorRecord(id="a", vector=VEC_A, metadata={"category": "x"}),
        VectorRecord(id="b", vector=VEC_A_NEAR, metadata={"category": "y"}),
    ])
    hits = await backend.search(VEC_A, limit=10, filter_metadata={"category": "y"})
    assert [h.id for h in hits] == ["b"]


@pytest.mark.asyncio
async def test_upsert_overwrites_existing(backend):
    await backend.upsert([VectorRecord(id="a", vector=VEC_A, metadata={"v": 1})])
    await backend.upsert([VectorRecord(id="a", vector=VEC_B, metadata={"v": 2})])
    hits = await backend.search(VEC_B, limit=1)
    assert hits[0].id == "a"
    assert hits[0].metadata["v"] == 2


@pytest.mark.asyncio
async def test_delete(backend):
    await backend.upsert([
        VectorRecord(id="a", vector=VEC_A),
        VectorRecord(id="b", vector=VEC_B),
    ])
    deleted = await backend.delete(["a"])
    assert deleted == 1
    assert await backend.count() == 1


@pytest.mark.asyncio
async def test_clear(backend):
    await backend.upsert([VectorRecord(id="a", vector=VEC_A)])
    await backend.clear()
    assert await backend.count() == 0


# ── SQLite-specific: persistence across reopens ───────────────────────────


@pytest.mark.asyncio
async def test_sqlite_persists_across_instances(tmp_path: Path):
    path = tmp_path / "persist.db"
    a = SQLiteBackend(db_path=path)
    await a.upsert([VectorRecord(id="x", vector=VEC_A, metadata={"k": "v"})])
    await a.close()

    b = SQLiteBackend(db_path=path)
    assert await b.count() == 1
    hits = await b.search(VEC_A, limit=1)
    assert hits[0].metadata["k"] == "v"
    await b.close()


@pytest.mark.asyncio
async def test_sqlite_rejects_invalid_table(tmp_path: Path):
    with pytest.raises(ValueError):
        SQLiteBackend(db_path=tmp_path / "x.db", table="bad; table")


# ── Factory ───────────────────────────────────────────────────────────────


def test_factory_unknown_name():
    with pytest.raises(ValueError):
        make_backend("nonexistent")


def test_factory_memory():
    be = make_backend("memory")
    assert be.name == "memory"


def test_factory_sqlite(tmp_path: Path):
    be = make_backend("sqlite", {"db_path": tmp_path / "f.db"})
    assert be.name == "sqlite"


def test_factory_qdrant_falls_back_when_unavailable(tmp_path: Path, monkeypatch):
    """When qdrant-client isn't installed, the factory must fall back rather
    than raise — config-time errors at startup are easier to debug than
    runtime crashes mid-request."""
    import myclaw.vector.qdrant_backend as qb
    monkeypatch.setattr(qb, "_QDRANT_AVAILABLE", False)

    be = make_backend("qdrant", {"fallback_db_path": tmp_path / "q.db"})
    # Should be a SQLite fallback, not a crash.
    assert be.name == "sqlite"
