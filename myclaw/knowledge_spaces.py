"""Collaborative knowledge spaces with role-based access control.

Provides multi-user shared knowledge bases with three roles:
    - viewer: can read and search
    - editor: can read, search, write, update
    - admin: full control including manage members

Usage:
    from myclaw.knowledge_spaces import (
        create_space, add_member, remove_member,
        list_spaces, get_space, check_permission
    )

    space_id = create_space("team-docs", owner="alice")
    add_member(space_id, "bob", "editor")
    add_member(space_id, "carol", "viewer")
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_SPACES_DB_PATH = Path.home() / ".myclaw" / "knowledge_spaces.db"

VALID_ROLES = {"viewer", "editor", "admin"}


def _get_db() -> sqlite3.Connection:
    """Get or create the collaborative spaces database."""
    _SPACES_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SPACES_DB_PATH))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS spaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            owner TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS space_members (
            space_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            added_at TEXT NOT NULL,
            added_by TEXT NOT NULL,
            PRIMARY KEY (space_id, user_id),
            FOREIGN KEY (space_id) REFERENCES spaces(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_members_user ON space_members(user_id)
    """)

    conn.commit()
    return conn


def create_space(
    name: str,
    owner: str,
    description: str = "",
    space_id: Optional[str] = None,
) -> str:
    """Create a new collaborative knowledge space.

    Args:
        name: Human-readable name
        owner: User ID of the owner (gets admin role)
        description: Optional description
        space_id: Optional explicit ID (generated if omitted)

    Returns:
        The space ID
    """
    sid = space_id or f"space_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow().isoformat()

    conn = _get_db()
    conn.execute(
        "INSERT INTO spaces (id, name, description, owner, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, name, description, owner, now, now),
    )
    conn.execute(
        "INSERT INTO space_members (space_id, user_id, role, added_at, added_by) VALUES (?, ?, ?, ?, ?)",
        (sid, owner, "admin", now, owner),
    )
    conn.commit()
    conn.close()

    logger.info(f"Knowledge space created: {sid} by {owner}")
    return sid


def delete_space(space_id: str, requesting_user: str) -> bool:
    """Delete a knowledge space. Only owner can delete.

    Returns True if deleted, False if not found or not authorized.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT owner FROM spaces WHERE id = ?", (space_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    if row["owner"] != requesting_user:
        conn.close()
        logger.warning(f"User {requesting_user} attempted to delete space {space_id} without permission")
        return False

    conn.execute("DELETE FROM spaces WHERE id = ?", (space_id,))
    conn.commit()
    conn.close()
    logger.info(f"Knowledge space deleted: {space_id}")
    return True


def add_member(
    space_id: str,
    user_id: str,
    role: str,
    added_by: str,
) -> bool:
    """Add a member to a space. Added_by must be admin.

    Returns True if added, False if not authorized or invalid role.
    """
    if role not in VALID_ROLES:
        logger.warning(f"Invalid role: {role}")
        return False

    conn = _get_db()

    # Check if added_by is admin
    admin_row = conn.execute(
        "SELECT role FROM space_members WHERE space_id = ? AND user_id = ?",
        (space_id, added_by),
    ).fetchone()

    if not admin_row or admin_row["role"] != "admin":
        conn.close()
        logger.warning(f"User {added_by} attempted to add member without admin rights")
        return False

    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """
            INSERT INTO space_members (space_id, user_id, role, added_at, added_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(space_id, user_id) DO UPDATE SET
                role = excluded.role,
                added_at = excluded.added_at,
                added_by = excluded.added_by
            """,
            (space_id, user_id, role, now, added_by),
        )
        conn.execute(
            "UPDATE spaces SET updated_at = ? WHERE id = ?",
            (now, space_id),
        )
        conn.commit()
        conn.close()
        logger.info(f"Added {user_id} as {role} to space {space_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to add member: {e}")
        conn.close()
        return False


def remove_member(
    space_id: str,
    user_id: str,
    removed_by: str,
) -> bool:
    """Remove a member from a space. Removed_by must be admin.

    Returns True if removed, False if not authorized.
    """
    conn = _get_db()

    admin_row = conn.execute(
        "SELECT role FROM space_members WHERE space_id = ? AND user_id = ?",
        (space_id, removed_by),
    ).fetchone()

    if not admin_row or admin_row["role"] != "admin":
        conn.close()
        return False

    # Cannot remove the owner
    space_row = conn.execute(
        "SELECT owner FROM spaces WHERE id = ?", (space_id,)
    ).fetchone()
    if space_row and space_row["owner"] == user_id:
        conn.close()
        logger.warning(f"Cannot remove owner from space {space_id}")
        return False

    conn.execute(
        "DELETE FROM space_members WHERE space_id = ? AND user_id = ?",
        (space_id, user_id),
    )
    conn.execute(
        "UPDATE spaces SET updated_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), space_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Removed {user_id} from space {space_id}")
    return True


def get_space(space_id: str) -> Optional[Dict[str, any]]:
    """Get space details including members."""
    conn = _get_db()
    space = conn.execute(
        "SELECT * FROM spaces WHERE id = ?", (space_id,)
    ).fetchone()

    if not space:
        conn.close()
        return None

    members = conn.execute(
        "SELECT user_id, role, added_at FROM space_members WHERE space_id = ?",
        (space_id,),
    ).fetchall()
    conn.close()

    return {
        "id": space["id"],
        "name": space["name"],
        "description": space["description"],
        "owner": space["owner"],
        "created_at": space["created_at"],
        "updated_at": space["updated_at"],
        "members": [{"user_id": m["user_id"], "role": m["role"], "added_at": m["added_at"]} for m in members],
    }


def list_spaces(user_id: str) -> List[Dict[str, any]]:
    """List all spaces where user_id is a member."""
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT s.*, m.role as user_role
        FROM spaces s
        JOIN space_members m ON s.id = m.space_id
        WHERE m.user_id = ?
        ORDER BY s.updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "owner": r["owner"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "user_role": r["user_role"],
        }
        for r in rows
    ]


def check_permission(space_id: str, user_id: str, required_role: str) -> bool:
    """Check if a user has at least the required role in a space.

    Role hierarchy: viewer < editor < admin
    """
    role_levels = {"viewer": 0, "editor": 1, "admin": 2}
    required_level = role_levels.get(required_role, 0)

    conn = _get_db()
    row = conn.execute(
        "SELECT role FROM space_members WHERE space_id = ? AND user_id = ?",
        (space_id, user_id),
    ).fetchone()
    conn.close()

    if not row:
        return False

    user_level = role_levels.get(row["role"], -1)
    return user_level >= required_level


def get_user_role(space_id: str, user_id: str) -> Optional[str]:
    """Get the role of a user in a space."""
    conn = _get_db()
    row = conn.execute(
        "SELECT role FROM space_members WHERE space_id = ? AND user_id = ?",
        (space_id, user_id),
    ).fetchone()
    conn.close()
    return row["role"] if row else None
