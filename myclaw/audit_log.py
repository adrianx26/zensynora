"""Tamper-evident audit logging with hash-chain integrity and rotation."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TamperEvidentAuditLog:
    """Persistent JSONL audit log where each entry includes previous entry hash."""

    def __init__(
        self,
        log_path: Optional[Path] = None,
        max_size_mb: int = 10,
        max_age_days: int = 7,
        max_files: int = 10,
        compress: bool = True,
    ):
        self.log_path = Path(log_path or (Path.home() / ".myclaw" / "audit" / "audit.log.jsonl"))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max(1, max_size_mb) * 1024 * 1024
        self.max_age_days = max(1, max_age_days)
        self.max_files = max(1, max_files)
        self.compress = compress
        self._lock = threading.RLock()

    @staticmethod
    def _hash_entry(payload: Dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _last_hash(self) -> str:
        if not self.log_path.exists():
            return "GENESIS"
        last_line = ""
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return "GENESIS"
        try:
            return json.loads(last_line).get("entry_hash", "GENESIS")
        except Exception:
            return "GENESIS"

    def append(self, event_type: str, details: Dict[str, Any], severity: str = "INFO") -> Dict[str, Any]:
        with self._lock:
            self._rotate_if_needed()
            prev_hash = self._last_hash()
            payload = {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event_type,
                "severity": severity,
                "details": details,
                "prev_hash": prev_hash,
            }
            payload["entry_hash"] = self._hash_entry(payload)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._enforce_retention()
            return payload

    def read_entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        return entries[-limit:]

    def clear(self) -> None:
        with self._lock:
            if self.log_path.exists():
                self.log_path.unlink()

    def export(self, export_path: str) -> str:
        target = Path(export_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists():
            target.write_text(self.log_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            target.write_text("", encoding="utf-8")
        return str(target)

    def verify_integrity(self) -> Dict[str, Any]:
        entries = self.read_entries(limit=10_000_000)
        prev = "GENESIS"
        for index, entry in enumerate(entries):
            given_hash = entry.get("entry_hash", "")
            if entry.get("prev_hash") != prev:
                return {"valid": False, "index": index, "reason": "prev_hash_mismatch"}
            candidate = dict(entry)
            candidate.pop("entry_hash", None)
            expected = self._hash_entry(candidate)
            if given_hash != expected:
                return {"valid": False, "index": index, "reason": "entry_hash_mismatch"}
            prev = given_hash
        return {"valid": True, "entries": len(entries), "last_hash": prev}

    def rotate_now(self) -> Dict[str, Any]:
        with self._lock:
            return self._rotate()

    def _rotate_if_needed(self) -> None:
        if not self.log_path.exists():
            return
        size_trigger = self.log_path.stat().st_size >= self.max_size_bytes
        mtime = datetime.fromtimestamp(self.log_path.stat().st_mtime)
        age_trigger = datetime.utcnow() - mtime >= timedelta(days=self.max_age_days)
        if size_trigger or age_trigger:
            self._rotate()

    def _rotate(self) -> Dict[str, Any]:
        if not self.log_path.exists():
            return {"rotated": False, "reason": "missing"}
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        rotated = self.log_path.with_name(f"{self.log_path.stem}.{ts}.jsonl")
        self.log_path.replace(rotated)
        if self.compress:
            gz_path = rotated.with_suffix(rotated.suffix + ".gz")
            with rotated.open("rb") as src, gzip.open(gz_path, "wb") as dst:
                dst.write(src.read())
            rotated.unlink(missing_ok=True)
            rotated = gz_path
        self.log_path.touch()
        self._enforce_retention()
        return {"rotated": True, "path": str(rotated)}

    def _enforce_retention(self) -> None:
        files = sorted(
            [p for p in self.log_path.parent.glob(f"{self.log_path.stem}.*") if p.name != self.log_path.name],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        now = datetime.utcnow()
        for idx, path in enumerate(files):
            file_age_days = (now - datetime.fromtimestamp(path.stat().st_mtime)).days
            if idx >= self.max_files or file_age_days > self.max_age_days:
                path.unlink(missing_ok=True)
