import pytest
import sqlite3
import os
from pathlib import Path
from myclaw.memory import Memory

@pytest.fixture
def memory(tmp_path):
    # Override HOME temporarily or just patch Memory db_path?
    # Memory uses Path.home() / ".myclaw" / f"memory_{user_id}.db"
    # We can mock Path.home()
    import unittest.mock
    with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
        mem = Memory(user_id="test_user")
        yield mem
        mem.close()

def test_memory_initialization(memory):
    assert memory.conn is not None
    assert memory.auto_cleanup_days == 30

def test_add_and_get_history(memory):
    memory.add("user", "Hello World")
    memory.add("assistant", "Hi there!")

    history = memory.get_history()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello World"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Hi there!"

def test_cleanup_old_messages(memory):
    # Insert an old message manually
    old_time = "2000-01-01T00:00:00.000000"
    memory.conn.execute(
        "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
        ("user", "Old message", old_time)
    )
    memory.conn.commit()

    # Insert a new message manually
    new_time = "2099-01-01T00:00:00.000000"
    memory.conn.execute(
        "INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
        ("user", "New message", new_time)
    )
    memory.conn.commit()

    # Cleanup older than 30 days
    deleted = memory.cleanup(30)
    assert deleted >= 1

    # Check what remains
    history = memory.get_history()
    assert len(history) == 1
    assert history[0]["content"] == "New message"
