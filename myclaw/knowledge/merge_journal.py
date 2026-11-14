"""Durable snapshots and rollback for source-note merges."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import difflib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional


_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _write_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    _write_bytes_atomic(path, data)


def _operation_dir(base_dir: Path, operation_id: str) -> Path:
    if not _OPERATION_ID.fullmatch(str(operation_id or "")):
        raise ValueError("Invalid merge operation id.")
    root = base_dir / ".merge-journal"
    operation = root / operation_id
    try:
        operation.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("Merge operation is outside the journal.") from None
    return operation


@contextmanager
def _pair_lock(base_dir: Path, keep: str, drop: str):
    lock_root = base_dir / ".merge-journal" / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    pair = "\x00".join(sorted((keep.casefold(), drop.casefold())))
    lock_path = lock_root / f"{hashlib.sha256(pair.encode('utf-8')).hexdigest()}.lock"
    stream = open(lock_path, "a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("A merge or rollback for these notes is already in progress.") from None
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("A merge or rollback for these notes is already in progress.") from None
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _safe_note_path(base_dir: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        raise ValueError(f"Merge source is outside the knowledge base: {path}") from None
    if not resolved.is_file():
        raise LookupError(f"Merge source file no longer exists: {path}")
    return resolved


def _safe_restore_path(base_dir: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Journal source path is outside the knowledge base.")
    path = base_dir
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Journal source path traverses a symbolic link.")
    resolved = path.resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        raise ValueError("Journal source path is outside the knowledge base.") from None
    return resolved


def _restore_files(operation: Path, manifest: Dict[str, Any], base_dir: Path) -> None:
    for source in manifest["sources"]:
        original = _safe_restore_path(base_dir, str(source["relative_path"]))
        snapshot = (operation / source["snapshot"]).resolve()
        try:
            snapshot.relative_to(operation.resolve())
        except ValueError:
            raise ValueError("Journal snapshot path is outside the operation folder.") from None
        data = snapshot.read_bytes()
        if _sha(data) != source["sha256"]:
            raise ValueError(f"Merge snapshot checksum mismatch: {source['permalink']}")
        _write_bytes_atomic(original, data)


def _reindex(user_id: str, db_path: Any) -> None:
    from .sync import sync_knowledge

    sync_knowledge(user_id, force=True, db_path=db_path)


def apply_merge(
    user_id: str,
    keep: Any,
    drop: Any,
    apply_changes: Callable[[], Dict[str, Any]],
    operation_context: Optional[Dict[str, Any]] = None,
    db_path: Any = None,
) -> Dict[str, Any]:
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    with _pair_lock(base_dir, keep.permalink, drop.permalink):
        return _apply_merge_locked(user_id, keep, drop, apply_changes, operation_context, db_path)


def _apply_merge_locked(
    user_id: str,
    keep: Any,
    drop: Any,
    apply_changes: Callable[[], Dict[str, Any]],
    operation_context: Optional[Dict[str, Any]] = None,
    db_path: Any = None,
) -> Dict[str, Any]:
    """Snapshot, apply, and verify one merge; restore originals on failure."""
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    keep_path = _safe_note_path(base_dir, keep.file_path)
    drop_path = _safe_note_path(base_dir, drop.file_path)
    if keep_path == drop_path:
        raise ValueError("A note cannot be merged into itself.")
    keep_data, drop_data = keep_path.read_bytes(), drop_path.read_bytes()
    expected = (operation_context or {}).get("source_checksums") or {}
    for note, data in ((keep, keep_data), (drop, drop_data)):
        if expected and expected.get(note.permalink) != _sha(data):
            raise ValueError(f"Source changed after review: {note.permalink}. Regenerate the proposal.")

    operation_id = uuid.uuid4().hex
    operation = _operation_dir(base_dir, operation_id)
    operation.mkdir(parents=True, exist_ok=False)
    manifest_path = operation / "manifest.json"
    manifest: Dict[str, Any] = {
        "operation_id": operation_id,
        "status": "preparing",
        "created_at": _now(),
        "updated_at": _now(),
        "user_id": user_id,
        "keep": keep.permalink,
        "drop": drop.permalink,
        "rationale": (operation_context or {}).get("rationale", "Explicit merge operation"),
        "proposal_reasons": (operation_context or {}).get("proposal_reasons", []),
        "review": (operation_context or {}).get("review"),
        "sources": [
            {"permalink": note.permalink,
             "relative_path": path.relative_to(base_dir).as_posix(),
             "snapshot": snapshot,
             "sha256": _sha(data)}
            for note, path, snapshot, data in (
                (keep, keep_path, "keep.md", keep_data),
                (drop, drop_path, "drop.md", drop_data),
            )
        ],
    }
    _write_manifest(manifest_path, manifest)
    try:
        for source, data in zip(manifest["sources"], (keep_data, drop_data)):
            _write_bytes_atomic(operation / source["snapshot"], data)
        manifest["status"] = "prepared"
        manifest["updated_at"] = _now()
        _write_manifest(manifest_path, manifest)
    except Exception as exc:
        manifest["status"] = "preparation_failed"
        manifest["error"] = str(exc)
        manifest["updated_at"] = _now()
        _write_manifest(manifest_path, manifest)
        raise RuntimeError(f"Could not prepare merge snapshots: {exc}") from exc

    manifest["status"] = "applying"
    manifest["updated_at"] = _now()
    _write_manifest(manifest_path, manifest)
    try:
        result = apply_changes()
        _reindex(user_id, db_path)
        from .storage import read_note

        merged = read_note(keep.permalink, user_id, db_path=db_path)
        if merged is None or not merged.file_path or not merged.file_path.is_file():
            raise RuntimeError("Merge did not produce the kept note.")
        if read_note(drop.permalink, user_id, db_path=db_path) is not None:
            raise RuntimeError("Merge did not remove the dropped note from the active knowledge base.")
        merged_data = merged.file_path.read_bytes()
        _write_bytes_atomic(operation / "merged-keep.md", merged_data)
        manifest["merged_keep"] = {
            "sha256": _sha(merged_data),
            "snapshot": "merged-keep.md",
            "relative_path": merged.file_path.resolve().relative_to(base_dir).as_posix(),
        }
        manifest["changes"] = result.get("added", {})
        manifest["status"] = "applied"
        manifest["applied_at"] = _now()
        manifest["updated_at"] = manifest["applied_at"]
        _write_manifest(manifest_path, manifest)
        result["operation_id"] = operation_id
        return result
    except Exception as exc:
        manifest["status"] = "recovery_required"
        manifest["error"] = str(exc)
        manifest["updated_at"] = _now()
        manifest_error = None
        try:
            _write_manifest(manifest_path, manifest)
        except Exception as journal_error:
            manifest_error = journal_error
        try:
            _restore_files(operation, manifest, base_dir)
            _reindex(user_id, db_path)
            manifest["status"] = "rolled_back"
            manifest["rolled_back_at"] = _now()
            manifest["updated_at"] = manifest["rolled_back_at"]
            _write_manifest(manifest_path, manifest)
        except Exception as rollback_error:
            manifest["status"] = "recovery_required"
            manifest["recovery_error"] = str(rollback_error)
            if manifest_error:
                manifest["journal_error"] = str(manifest_error)
            manifest["updated_at"] = _now()
            final_manifest_error = None
            try:
                _write_manifest(manifest_path, manifest)
            except Exception as write_error:
                final_manifest_error = write_error
            journal_detail = (f" Journal update failed: {final_manifest_error}."
                              if final_manifest_error else "")
            raise RuntimeError(
                f"Merge failed ({exc}); automatic recovery also failed ({rollback_error}). "
                f"Operation {operation_id} needs Doctor review.{journal_detail}") from exc
        if manifest_error:
            raise RuntimeError(
                f"Merge failed and both source notes were restored, but the journal could not be "
                f"updated ({manifest_error}). Operation {operation_id} needs Doctor review.") from exc
        raise RuntimeError(
            f"Merge failed and both source notes were restored. Operation {operation_id}: {exc}") from exc


def list_operations(user_id: str = "default") -> list:
    from .storage import get_knowledge_dir

    root = get_knowledge_dir(user_id) / ".merge-journal"
    if not root.exists():
        return []
    operations = []
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            operations.append({"operation_id": manifest_path.parent.name,
                              "status": "manifest_unreadable"})
            continue
        operations.append({
            "operation_id": manifest.get("operation_id", manifest_path.parent.name),
            "status": manifest.get("status", "unknown"),
            "created_at": manifest.get("created_at"),
            "applied_at": manifest.get("applied_at"),
            "rolled_back_at": manifest.get("rolled_back_at"),
            "recovered_at": manifest.get("recovered_at"),
            "keep": manifest.get("keep"),
            "drop": manifest.get("drop"),
            "rationale": manifest.get("rationale"),
            "error": manifest.get("error") or manifest.get("recovery_error"),
        })
    return operations


def audit_operations(user_id: str = "default") -> Dict[str, Any]:
    """Read-only check of journal integrity and active merge state."""
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    root = base_dir / ".merge-journal"
    findings = []
    if not root.is_dir():
        return {"operations": 0, "findings": findings}
    operation_dirs = sorted(path for path in root.iterdir()
                            if path.is_dir() and path.name != ".locks")
    for operation in operation_dirs:
        manifest_path = operation / "manifest.json"
        if not manifest_path.is_file():
            findings.append({"operation_id": operation.name, "status": "manifest_missing",
                             "message": "Merge operation directory has no manifest."})
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            findings.append({"operation_id": operation.name, "status": "manifest_invalid",
                             "message": f"Merge manifest cannot be read: {exc}"})
            continue
        operation_id = str(manifest.get("operation_id") or operation.name)
        status = str(manifest.get("status") or "unknown")
        if manifest.get("user_id") != user_id or operation_id != operation.name:
            findings.append({"operation_id": operation_id, "status": "manifest_invalid",
                             "message": "Merge manifest identity does not match its knowledge base folder."})
            continue
        issues = []
        sources = manifest.get("sources") or []
        if len(sources) != 2:
            issues.append("expected two source snapshots")
        for source in sources:
            snapshot = (operation / str(source.get("snapshot") or "")).resolve()
            try:
                snapshot.relative_to(operation.resolve())
                data = snapshot.read_bytes()
            except (OSError, ValueError) as exc:
                issues.append(f"{source.get('permalink')}: snapshot unavailable ({exc})")
                continue
            if _sha(data) != source.get("sha256"):
                issues.append(f"{source.get('permalink')}: snapshot checksum mismatch")
        if status in ("preparing", "prepared", "applying", "recovery_required",
                      "recovering",
                      "rolling_back", "rollback_verification_failed", "preparation_failed"):
            issues.append(f"operation stopped in state {status}")
        if status == "applied":
            merged = manifest.get("merged_keep") or {}
            path = (base_dir / str(merged.get("relative_path") or "")).resolve()
            try:
                path.relative_to(base_dir)
                data = path.read_bytes()
                if _sha(data) != merged.get("sha256"):
                    issues.append("merged note checksum changed; automatic rollback is no longer safe")
            except (OSError, ValueError) as exc:
                issues.append(f"merged note unavailable ({exc})")
            if len(sources) == 2:
                dropped = (base_dir / str(sources[1].get("relative_path") or "")).resolve()
                try:
                    dropped.relative_to(base_dir)
                    if dropped.exists():
                        issues.append("dropped note is still active after the merge")
                except ValueError:
                    issues.append("dropped note path is outside the knowledge base")
            try:
                from .quality import find_conflicts
                from .storage import read_note

                merged_note = read_note(str(manifest.get("keep") or ""), user_id)
                active = [o for o in (merged_note.observations or [])
                          if getattr(o, "status", "active") == "active"] if merged_note else []
                if active and find_conflicts(active):
                    issues.append("merged note contains unresolved active fact conflicts")
            except (OSError, ValueError, LookupError) as exc:
                issues.append(f"merged note conflict check failed ({exc})")
        if status in ("rolled_back", "recovered"):
            for source in sources:
                try:
                    note_path = _safe_restore_path(base_dir, str(source.get("relative_path") or ""))
                    data = note_path.read_bytes()
                    if _sha(data) != source.get("sha256"):
                        issues.append(f"{source.get('permalink')}: restored source checksum mismatch")
                except (OSError, ValueError) as exc:
                    issues.append(f"{source.get('permalink')}: restored source unavailable ({exc})")
        findings.extend(
            {"operation_id": operation_id, "status": status, "message": issue,
             "rationale": manifest.get("rationale"),
             "proposal_reasons": manifest.get("proposal_reasons", []),
             "review": manifest.get("review")}
            for issue in issues
        )
    return {"operations": len(operation_dirs), "findings": findings}


def recover_operation(user_id: str, operation_id: str, db_path: Any = None) -> Dict[str, Any]:
    """Restore originals from a verified interrupted merge, preserving partial files first."""
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    operation = _operation_dir(base_dir, operation_id)
    try:
        manifest = json.loads((operation / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(f"Merge operation journal is unavailable: {exc}") from exc
    if manifest.get("operation_id") != operation_id or manifest.get("user_id") != user_id:
        raise ValueError("Merge journal does not belong to this operation and knowledge base.")
    with _pair_lock(base_dir, str(manifest.get("keep", "")), str(manifest.get("drop", ""))):
        return _recover_operation_locked(user_id, operation_id, db_path)


def _recover_operation_locked(user_id: str, operation_id: str, db_path: Any = None) -> Dict[str, Any]:
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    operation = _operation_dir(base_dir, operation_id)
    manifest_path = operation / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(f"Merge operation journal is unavailable: {exc}") from exc
    if manifest.get("operation_id") != operation_id or manifest.get("user_id") != user_id:
        raise ValueError("Merge journal does not belong to this operation and knowledge base.")
    recoverable = {
        "preparing", "prepared", "applying", "recovery_required", "recovering",
        "rolling_back", "rollback_verification_failed", "preparation_failed",
    }
    if manifest.get("status") not in recoverable:
        raise ValueError(
            f"Only an interrupted merge can be recovered (status: {manifest.get('status')})."
        )
    sources = manifest.get("sources") or []
    if len(sources) != 2:
        raise ValueError("Merge journal must contain exactly two source snapshots.")
    source_ids = [str(source.get("permalink") or "") for source in sources]
    source_paths = [str(source.get("relative_path") or "") for source in sources]
    if len(set(source_ids)) != 2 or set(source_ids) != {
            str(manifest.get("keep") or ""), str(manifest.get("drop") or "")}:
        raise ValueError("Merge journal source identities do not match its keep/drop notes.")
    if len(set(source_paths)) != 2:
        raise ValueError("Merge journal source paths are not distinct.")
    for source in sources:
        snapshot = (operation / str(source.get("snapshot") or "")).resolve()
        try:
            snapshot.relative_to(operation.resolve())
            data = snapshot.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Cannot recover: snapshot unavailable for {source.get('permalink')}: {exc}"
            ) from exc
        if _sha(data) != source.get("sha256"):
            raise ValueError(f"Cannot recover: snapshot checksum mismatch for {source.get('permalink')}.")
        _safe_restore_path(base_dir, str(source.get("relative_path") or ""))

    attempt_id = uuid.uuid4().hex
    attempt = operation / f"recovery-{attempt_id}"
    attempt.mkdir(parents=False, exist_ok=False)
    preserved = []
    for index, source in enumerate(sources):
        current = _safe_restore_path(base_dir, str(source["relative_path"]))
        item = {"relative_path": source["relative_path"], "existed": current.exists()}
        if current.exists():
            if not current.is_file():
                raise ValueError(f"Cannot recover: a non-file occupies {source['relative_path']}.")
            current_data = current.read_bytes()
            name = f"current-{index}.bin"
            _write_bytes_atomic(attempt / name, current_data)
            if _sha((attempt / name).read_bytes()) != _sha(current_data):
                raise RuntimeError("Could not verify the preserved partial merge state.")
            item.update({"snapshot": f"{attempt.name}/{name}", "sha256": _sha(current_data)})
        preserved.append(item)

    recovery = {"attempt_id": attempt_id, "status": "preserved", "started_at": _now(),
                "preserved_state": preserved}
    manifest["recovery_attempt"] = recovery
    manifest["status"] = "recovering"
    manifest["updated_at"] = _now()
    _write_manifest(manifest_path, manifest)
    try:
        _restore_files(operation, manifest, base_dir)
        _reindex(user_id, db_path)
        for source in sources:
            restored = _safe_restore_path(base_dir, str(source["relative_path"]))
            if _sha(restored.read_bytes()) != source["sha256"]:
                raise RuntimeError(f"Restored checksum mismatch: {source['permalink']}")
        recovery["status"] = "complete"
        recovery["finished_at"] = _now()
        manifest["status"] = "recovered"
        manifest["recovered_at"] = recovery["finished_at"]
        manifest["updated_at"] = recovery["finished_at"]
        _write_manifest(manifest_path, manifest)
    except Exception as exc:
        recovery["status"] = "failed"
        recovery["error"] = str(exc)
        recovery["finished_at"] = _now()
        manifest["status"] = "recovery_required"
        manifest["recovery_error"] = str(exc)
        manifest["updated_at"] = recovery["finished_at"]
        _write_manifest(manifest_path, manifest)
        raise RuntimeError(
            f"Merge recovery did not complete ({exc}). The partial state is preserved in "
            f"journal operation {operation_id}; retry after resolving the error."
        ) from exc
    return {"ok": True, "operation_id": operation_id, "status": "recovered",
            "checksums_verified": True, "partial_state_preserved": True}


def diagnose_operation(user_id: str, operation_id: str) -> Dict[str, Any]:
    """Provide Medic with the original rationale, evidence, and exact merge diff."""
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    operation = _operation_dir(base_dir, operation_id)
    try:
        manifest = json.loads((operation / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(f"Merge operation journal is unavailable: {exc}") from exc
    if manifest.get("operation_id") != operation_id or manifest.get("user_id") != user_id:
        raise ValueError("Merge journal does not belong to this operation and knowledge base.")
    if len(manifest.get("sources") or []) != 2:
        raise ValueError("Merge journal does not contain two source snapshots.")

    sources = {}
    for source in manifest["sources"]:
        snapshot = (operation / str(source.get("snapshot") or "")).resolve()
        try:
            snapshot.relative_to(operation.resolve())
            raw = snapshot.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Source snapshot unavailable: {source.get('permalink')}: {exc}") from exc
        sources[source["permalink"]] = {
            "sha256": _sha(raw),
            "expected_sha256": source.get("sha256"),
            "checksum_valid": _sha(raw) == source.get("sha256"),
            "text": raw.decode("utf-8", errors="replace"),
        }

    merged_snapshot = (operation / str((manifest.get("merged_keep") or {}).get("snapshot") or "")).resolve()
    try:
        merged_snapshot.relative_to(operation.resolve())
        merged_raw = merged_snapshot.read_bytes()
        merged_text = merged_raw.decode("utf-8", errors="replace")
        merged_checksum_valid = _sha(merged_raw) == (manifest.get("merged_keep") or {}).get("sha256")
    except (OSError, ValueError):
        merged_text = ""
        merged_checksum_valid = False
    keep_id = str(manifest.get("keep") or "")
    before_text = sources.get(keep_id, {}).get("text", "")
    diff = list(difflib.unified_diff(
        before_text.splitlines(keepends=True),
        merged_text.splitlines(keepends=True),
        fromfile=f"{keep_id} before merge",
        tofile=f"{keep_id} after merge",
    ))
    return {
        "operation_id": operation_id,
        "status": manifest.get("status"),
        "keep": keep_id,
        "drop": manifest.get("drop"),
        "rationale": manifest.get("rationale"),
        "proposal_reasons": manifest.get("proposal_reasons", []),
        "review": manifest.get("review"),
        "source_checksums_valid": all(item["checksum_valid"] for item in sources.values()),
        "merged_snapshot_checksum_valid": merged_checksum_valid,
        "actual_merge_diff": "".join(diff),
        "error": manifest.get("error") or manifest.get("recovery_error"),
    }


def rollback_merge(user_id: str, operation_id: str, db_path: Any = None) -> Dict[str, Any]:
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    operation = _operation_dir(base_dir, operation_id)
    try:
        manifest = json.loads((operation / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(f"Merge operation journal is unavailable: {exc}") from exc
    with _pair_lock(base_dir, str(manifest.get("keep", "")), str(manifest.get("drop", ""))):
        return _rollback_merge_locked(user_id, operation_id, db_path)


def _rollback_merge_locked(user_id: str, operation_id: str, db_path: Any = None) -> Dict[str, Any]:
    """Restore both exact source snapshots if the applied result is unchanged."""
    from .storage import get_knowledge_dir

    base_dir = get_knowledge_dir(user_id).resolve()
    operation = _operation_dir(base_dir, operation_id)
    manifest_path = operation / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(f"Merge operation journal is unavailable: {exc}") from exc
    if manifest.get("operation_id") != operation_id or manifest.get("user_id") != user_id:
        raise ValueError("Merge journal does not belong to this operation and knowledge base.")
    if len(manifest.get("sources") or []) != 2:
        raise ValueError("Merge journal must contain exactly two source snapshots.")
    if manifest.get("status") != "applied":
        raise ValueError(f"Only an applied merge can be rolled back (status: {manifest.get('status')}).")

    merged = manifest.get("merged_keep") or {}
    current_keep = (base_dir / merged.get("relative_path", "")).resolve()
    try:
        current_keep.relative_to(base_dir)
    except ValueError:
        raise ValueError("Merged note path is outside the knowledge base.") from None
    if not current_keep.is_file() or _sha(current_keep.read_bytes()) != merged.get("sha256"):
        raise ValueError("The merged note changed after this operation; refusing to overwrite newer edits.")
    drop_path = (base_dir / manifest["sources"][1]["relative_path"]).resolve()
    try:
        drop_path.relative_to(base_dir)
    except ValueError:
        raise ValueError("Dropped note path is outside the knowledge base.") from None
    if drop_path.exists():
        raise ValueError("The dropped note path is occupied; refusing to overwrite it.")

    manifest["status"] = "rolling_back"
    manifest["updated_at"] = _now()
    _write_manifest(manifest_path, manifest)
    try:
        _restore_files(operation, manifest, base_dir)
        _reindex(user_id, db_path)
        for source in manifest["sources"]:
            restored = base_dir / source["relative_path"]
            if _sha(restored.read_bytes()) != source["sha256"]:
                raise RuntimeError(f"Restored checksum mismatch: {source['permalink']}")
        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = _now()
        manifest["updated_at"] = manifest["rolled_back_at"]
        _write_manifest(manifest_path, manifest)
    except Exception as exc:
        manifest["status"] = "rollback_verification_failed"
        manifest["recovery_error"] = str(exc)
        manifest["updated_at"] = _now()
        _write_manifest(manifest_path, manifest)
        raise RuntimeError(f"Rollback could not be fully verified: {exc}") from exc
    return {"ok": True, "operation_id": operation_id, "status": "rolled_back",
            "checksums_verified": True}
