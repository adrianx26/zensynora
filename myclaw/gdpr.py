"""GDPR compliance helpers for ZenSynora.

Provides user data deletion (right to erasure) and export capabilities.
All operations are opt-in via config — by default, GDPR features are disabled.

Usage:
    from myclaw.gdpr import delete_user_data, export_user_data

    # Delete all data for a user
    delete_user_data("user_123")

    # Export all data for a user
    export_user_data("user_123", "/path/to/export.zip")
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_user_memory_db(user_id: str) -> Optional[Path]:
    """Get the memory database path for a user."""
    db_path = Path.home() / ".myclaw" / f"memory_{user_id}.db"
    return db_path if db_path.exists() else None


def _get_user_knowledge_dir(user_id: str) -> Path:
    """Get the knowledge directory for a user."""
    return Path.home() / ".myclaw" / "knowledge" / user_id


def _get_audit_log_path() -> Path:
    """Get the audit log path."""
    return Path.home() / ".myclaw" / "audit" / "audit.log.jsonl"


def delete_user_data(user_id: str, dry_run: bool = False) -> Dict[str, any]:
    """Delete all data for a user (GDPR Right to Erasure).

    Deletes:
        - Memory database (SQLite)
        - Knowledge base notes (Markdown files + SQLite FTS)
        - Audit log entries for this user

    Args:
        user_id: The user ID to delete
        dry_run: If True, only report what would be deleted

    Returns:
        Dict with deletion summary
    """
    deleted: Dict[str, any] = {"user_id": user_id, "dry_run": dry_run, "items": []}

    # 1. Delete memory database
    mem_db = _get_user_memory_db(user_id)
    if mem_db and mem_db.exists():
        if not dry_run:
            mem_db.unlink()
        deleted["items"].append({"type": "memory_db", "path": str(mem_db)})
        logger.info(f"{'Would delete' if dry_run else 'Deleted'} memory DB: {mem_db}")

    # 2. Delete knowledge directory
    kb_dir = _get_user_knowledge_dir(user_id)
    if kb_dir.exists():
        files = list(kb_dir.rglob("*"))
        if not dry_run:
            shutil.rmtree(kb_dir)
        deleted["items"].append({
            "type": "knowledge_dir",
            "path": str(kb_dir),
            "file_count": len([f for f in files if f.is_file()]),
        })
        logger.info(f"{'Would delete' if dry_run else 'Deleted'} knowledge dir: {kb_dir}")

    # 3. Delete knowledge graph DB entries for this user
    from .knowledge.db import KnowledgeDB
    kg_db_path = Path.home() / ".myclaw" / f"knowledge_{user_id}.db"
    if kg_db_path.exists():
        if not dry_run:
            kg_db_path.unlink()
        deleted["items"].append({"type": "knowledge_graph_db", "path": str(kg_db_path)})
        logger.info(f"{'Would delete' if dry_run else 'Deleted'} knowledge graph DB: {kg_db_path}")

    # 4. Filter audit log entries for this user
    audit_path = _get_audit_log_path()
    if audit_path.exists():
        removed_count = 0
        kept_lines: List[str] = []
        with audit_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    details = entry.get("details", {})
                    # Check various places where user_id might appear
                    entry_user = details.get("user_id") or details.get("user")
                    if entry_user == user_id:
                        removed_count += 1
                        continue
                except Exception:
                    pass
                kept_lines.append(line)

        if removed_count > 0:
            if not dry_run:
                audit_path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
            deleted["items"].append({
                "type": "audit_log_entries",
                "count": removed_count,
                "path": str(audit_path),
            })
            logger.info(
                f"{'Would remove' if dry_run else 'Removed'} {removed_count} "
                f"audit log entries for user {user_id}"
            )

    # 5. Delete knowledge gap log entries for this user
    gap_file = Path.home() / ".myclaw" / "knowledge_gaps.jsonl"
    if gap_file.exists():
        removed_count = 0
        kept_lines: List[str] = []
        with gap_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("user_id") == user_id:
                        removed_count += 1
                        continue
                except Exception:
                    pass
                kept_lines.append(line)

        if removed_count > 0:
            if not dry_run:
                gap_file.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
            deleted["items"].append({
                "type": "knowledge_gaps",
                "count": removed_count,
                "path": str(gap_file),
            })
            logger.info(
                f"{'Would remove' if dry_run else 'Removed'} {removed_count} "
                f"gap entries for user {user_id}"
            )

    deleted["timestamp"] = datetime.utcnow().isoformat()
    deleted["total_items"] = len(deleted["items"])
    return deleted


def export_user_data(user_id: str, export_path: Optional[str] = None) -> str:
    """Export all user data as a ZIP archive (GDPR Right to Data Portability).

    Args:
        user_id: The user ID to export
        export_path: Output ZIP file path (default: ~/.myclaw/exports/{user_id}_{timestamp}.zip)

    Returns:
        Path to the exported ZIP file
    """
    if export_path is None:
        exports_dir = Path.home() / ".myclaw" / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        export_path = str(exports_dir / f"{user_id}_{ts}.zip")

    target = Path(export_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        # Memory DB
        mem_db = _get_user_memory_db(user_id)
        if mem_db and mem_db.exists():
            zf.write(mem_db, arcname=f"memory/{mem_db.name}")

        # Knowledge files
        kb_dir = _get_user_knowledge_dir(user_id)
        if kb_dir.exists():
            for file_path in kb_dir.rglob("*"):
                if file_path.is_file():
                    arcname = f"knowledge/{file_path.relative_to(kb_dir)}"
                    zf.write(file_path, arcname=arcname)

        # Knowledge graph DB
        kg_db = Path.home() / ".myclaw" / f"knowledge_{user_id}.db"
        if kg_db.exists():
            zf.write(kg_db, arcname=f"knowledge_graph/{kg_db.name}")

        # Audit log entries for this user
        audit_path = _get_audit_log_path()
        if audit_path.exists():
            user_entries = []
            with audit_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        details = entry.get("details", {})
                        entry_user = details.get("user_id") or details.get("user")
                        if entry_user == user_id:
                            user_entries.append(entry)
                    except Exception:
                        pass
            if user_entries:
                zf.writestr(
                    "audit_log.jsonl",
                    "\n".join(json.dumps(e, ensure_ascii=False) for e in user_entries) + "\n",
                )

        # Manifest
        manifest = {
            "user_id": user_id,
            "exported_at": datetime.utcnow().isoformat(),
            "version": "1.0",
            "notice": "This is your personal data export from ZenSynora.",
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    logger.info(f"User data exported: {target}")
    return str(target)
