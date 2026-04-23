import pytest
import asyncio
import os
from pathlib import Path
from myclaw.memory import Memory, AsyncSQLitePool


@pytest.fixture
def memory(tmp_path):
    import unittest.mock

    with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
        mem = Memory(user_id="test_user")
        yield mem
        asyncio.get_event_loop().run_until_complete(mem.close())


# ── AsyncSQLitePool Tests ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def reset_pool():
    """Reset the pool singleton state before each test."""
    await AsyncSQLitePool.close_all()
    AsyncSQLitePool._pools.clear()
    AsyncSQLitePool._checked_out.clear()
    AsyncSQLitePool._semaphores.clear()
    AsyncSQLitePool._locks.clear()


@pytest.mark.asyncio
async def test_pool_checkout_release(tmp_path):
    """Pool should checkout and release connections correctly."""
    db_path = tmp_path / "pool.db"
    conn = await AsyncSQLitePool.get_connection(db_path)
    assert conn is not None
    cursor = await conn.execute("SELECT 42")
    row = await cursor.fetchone()
    assert row[0] == 42
    await AsyncSQLitePool.release_connection(db_path, conn)
    await AsyncSQLitePool.close_all()


@pytest.mark.asyncio
async def test_pool_concurrent_access(tmp_path):
    """Multiple concurrent workers should share pool safely."""
    db_path = tmp_path / "pool_concurrent.db"

    async def worker(n):
        conn = await AsyncSQLitePool.get_connection(db_path)
        await asyncio.sleep(0.01)
        await conn.execute(f"CREATE TABLE IF NOT EXISTS t{n} (id INTEGER)")
        await AsyncSQLitePool.release_connection(db_path, conn)
        return n

    results = await asyncio.gather(*[worker(i) for i in range(8)])
    assert sorted(results) == list(range(8))
    await AsyncSQLitePool.close_all()


@pytest.mark.asyncio
async def test_pool_exhaustion_and_reuse(tmp_path):
    """Pool should reuse connections when all are checked out."""
    db_path = tmp_path / "pool_exhaust.db"
    AsyncSQLitePool._pool_size = 2

    # Acquire 2 connections (max pool size)
    c1 = await AsyncSQLitePool.get_connection(db_path)
    c2 = await AsyncSQLitePool.get_connection(db_path)

    # Release one and acquire again — should reuse the same connection
    await AsyncSQLitePool.release_connection(db_path, c1)
    c3 = await AsyncSQLitePool.get_connection(db_path)

    # c3 should reuse c1's connection object
    assert id(c3) == id(c1)

    await AsyncSQLitePool.release_connection(db_path, c2)
    await AsyncSQLitePool.release_connection(db_path, c3)
    await AsyncSQLitePool.close_all()


@pytest.mark.asyncio
async def test_memory_initialization(memory):
    await memory.initialize()
    assert memory.conn is not None
    assert memory.auto_cleanup_days == 30


@pytest.mark.asyncio
async def test_add_and_get_history(memory):
    await memory.initialize()
    await memory.add("user", "Hello World")
    await memory.add("assistant", "Hi there!")

    history = await memory.get_history()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello World"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_cleanup_old_messages(memory):
    await memory.initialize()

    old_time = "2000-01-01T00:00:00.000000"
    await memory.conn.execute(
        "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
        ("user", "Old message", old_time),
    )
    await memory.conn.commit()

    new_time = "2099-01-01T00:00:00.000000"
    await memory.conn.execute(
        "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
        ("user", "New message", new_time),
    )
    await memory.conn.commit()

    deleted = await memory.cleanup(30)
    assert deleted >= 1

    history = await memory.get_history()
    assert len(history) == 1
    assert history[0]["content"] == "New message"
