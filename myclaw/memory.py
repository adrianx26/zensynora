import sqlite3
import json
from datetime import datetime
from pathlib import Path

class Memory:
    def __init__(self):
        self.db = Path.home() / ".myclaw" / "memory.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )""")
        self.conn.commit()

    def add(self, role: str, content: str):
        self.conn.execute("INSERT INTO messages (role, content, timestamp) VALUES (?, ?, ?)",
                         (role, content, datetime.now().isoformat()))
        self.conn.commit()

    def get_history(self, limit=20):
        cur = self.conn.execute("SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return [{"role": r, "content": c} for r, c in cur.fetchall()][::-1]