import pytest
import asyncio
import os
from pathlib import Path
from myclaw.memory import Memory

@pytest.fixture
def memory(tmp_path):
    import unittest.mock
    with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
        mem = Memory(user_id="test_user")
        yield mem
        asyncio.get_event_loop().run_until_complete(mem.close())

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
        ("user", "Old message", old_time)
    )
    await memory.conn.commit()

    new_time = "2099-01-01T00:00:00.000000"
    await memory.conn.execute(
        "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
        ("user", "New message", new_time)
    )
    await memory.conn.commit()

    deleted = await memory.cleanup(30)
    assert deleted >= 1

    history = await memory.get_history()
    assert len(history) == 1
    assert history[0]["content"] == "New message"