"""Ownership, conflict handling and recoverable file operations for integration files.

The manifest is an ownership record, never an authority to delete any path it
names.  Every path is re-derived from a currently allowed host root, every
relative name is on a closed list, and every installed file must still match the
hash the tool wrote before it may be updated or removed.

Writes use a lock, a transaction record and atomic replacement.  A normal
exception restores the files written by this transaction, but never overwrites a
concurrent user edit.  A crash may leave a transaction record; later writes then
refuse until the user inspects it, and ``--force`` does not bypass that refusal.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .render import BINDING_FIELDS, decode_binding, encode_binding

SCHEMA_VERSION = 1
INSTALLER_VERSION = "0.1.0"
SCHEMA_KEYS = frozenset({"schema_version", "installer_version", "roots"})
ALLOWED_FILES = frozenset({
    "SKILL.md",
    "scripts/reproagent.ps1",
    "scripts/reproagent.sh",
    "agents/openai.yaml",
})
BACKUP_MARKER = ".reproagent-backup-"
LOCK_NAME = "integration.lock"
TRANSACTION_NAME = "integration-transaction.json"
_FILE_HEX = frozenset("0123456789abcdef")
_UNCHECKED = object()


class ManifestError(ValueError):
    """The ownership record is corrupt or names a path outside its authority."""

    def __init__(self, code: str, message: str = "invalid integration manifest"):
        super().__init__(message)
        self.code = code


class PlanConflict(ValueError):
    def __init__(self, conflicts):
        super().__init__("integration files conflict; inspect and use --force to back up")
        self.conflicts = conflicts


class InstallBusy(RuntimeError):
    def __init__(self, message="another integration install is in progress"):
        super().__init__(message)


class IncompleteTransaction(RuntimeError):
    def __init__(self, path):
        super().__init__("an incomplete integration transaction exists; inspect it before retrying")
        self.path = str(path)


class InstallFailed(RuntimeError):
    def __init__(self, code="install_failed", message="integration install failed and was rolled back"):
        super().__init__(message)
        self.code = code


class RollbackIncomplete(InstallFailed):
    def __init__(self, path):
        super().__init__("rollback_incomplete",
                         "integration rollback could not restore every file; inspect the transaction record")
        self.path = str(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _inside(path, root) -> bool:
    path_norm, root_norm = _norm(path), _norm(root)
    if path_norm == root_norm:
        return False
    try:
        return os.path.commonpath((path_norm, root_norm)) == root_norm
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse)


def _validate_path_components(path: Path) -> None:
    """Reject links in existing ancestors as well as the target itself."""
    path = Path(os.path.abspath(path))
    for component in reversed((path, *path.parents)):
        if _is_link_or_reparse(component):
            raise ManifestError("path_uses_link")


def _empty_manifest() -> dict:
    return {"schema_version": SCHEMA_VERSION, "installer_version": INSTALLER_VERSION,
            "roots": []}


def _validate_hex(value, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _FILE_HEX:
        raise ManifestError(f"manifest_{name}_invalid")
    return value


def _validate_relative(relative) -> str:
    if not isinstance(relative, str) or not relative or relative not in ALLOWED_FILES:
        raise ManifestError("manifest_relative_path_invalid")
    parts = Path(relative).parts
    if any(part in ("", ".", "..") for part in parts) or Path(relative).is_absolute():
        raise ManifestError("manifest_relative_path_invalid")
    return relative


def _validate_root(root, host: str, allowed_roots: Mapping[str, tuple[Path, ...]]) -> Path:
    if not isinstance(root, str) or not root:
        raise ManifestError("manifest_root_invalid")
    candidate = Path(os.path.abspath(root))
    if not candidate.is_absolute():
        raise ManifestError("manifest_root_invalid")
    permitted = {_norm(path) for path in allowed_roots.get(host, ())}
    if _norm(candidate) not in permitted:
        raise ManifestError("manifest_root_outside_allowed")
    _validate_path_components(candidate)
    return candidate


def read_manifest(path: Path, *, allowed_roots: Mapping[str, tuple[Path, ...]]) -> dict:
    """Read and validate an ownership record; a missing file is an empty record."""
    path = Path(path)
    _validate_path_components(path)
    if not path.exists():
        if _is_link_or_reparse(path):
            raise ManifestError("manifest_not_a_regular_file")
        return _empty_manifest()
    if _is_link_or_reparse(path) or not path.is_file():
        raise ManifestError("manifest_not_a_regular_file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest_unreadable") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("manifest_schema_invalid")
    roots = data.get("roots")
    if not isinstance(roots, list):
        raise ManifestError("manifest_roots_invalid")
    validated = []
    seen_roots = set()
    for entry in roots:
        if not isinstance(entry, dict):
            raise ManifestError("manifest_root_invalid")
        host, layout, root = entry.get("host"), entry.get("layout"), entry.get("root")
        if host not in allowed_roots or not isinstance(layout, str):
            raise ManifestError("manifest_root_invalid")
        root_path = _validate_root(root, host, allowed_roots)
        key = (host, _norm(root_path))
        if key in seen_roots:
            raise ManifestError("manifest_root_duplicate")
        seen_roots.add(key)
        files = entry.get("files", [])
        if not isinstance(files, list):
            raise ManifestError("manifest_files_invalid")
        validated_files = []
        seen_files = set()
        for record in files:
            if not isinstance(record, dict):
                raise ManifestError("manifest_file_invalid")
            relative = _validate_relative(record.get("path"))
            digest = _validate_hex(record.get("sha256"), "sha256")
            absolute = record.get("absolute")
            if not isinstance(absolute, str) or _norm(absolute) != _norm(root_path / relative):
                raise ManifestError("manifest_file_outside_root")
            if relative in seen_files:
                raise ManifestError("manifest_file_duplicate")
            seen_files.add(relative)
            if _is_link_or_reparse(Path(absolute)):
                raise ManifestError("manifest_file_not_a_regular_file")
            validated_files.append({"path": relative, "sha256": digest, "absolute": absolute})
        backups = entry.get("backups", [])
        if not isinstance(backups, list):
            raise ManifestError("manifest_backups_invalid")
        validated_backups = []
        seen_backups = set()
        for record in backups:
            if not isinstance(record, dict):
                raise ManifestError("manifest_backup_invalid")
            backup_path = record.get("path")
            digest = _validate_hex(record.get("sha256"), "backup_sha256")
            if not isinstance(backup_path, str) or not _inside(backup_path, root_path):
                raise ManifestError("manifest_backup_outside_root")
            if BACKUP_MARKER not in Path(backup_path).name:
                raise ManifestError("manifest_backup_invalid")
            if _norm(backup_path) in seen_backups:
                raise ManifestError("manifest_backup_duplicate")
            seen_backups.add(_norm(backup_path))
            validated_backups.append({"path": backup_path, "sha256": digest})
        validated_entry = {"host": host, "layout": layout, "root": str(root_path),
                           "files": validated_files, "backups": validated_backups}
        if "binding" in entry:
            try:
                binding = entry["binding"]
                if not isinstance(binding, dict) or set(binding) != set(BINDING_FIELDS):
                    raise ValueError("invalid binding fields")
                validated_entry["binding"] = decode_binding(encode_binding(binding))
            except (ValueError, TypeError) as exc:
                raise ManifestError("manifest_binding_invalid") from exc
        validated.append(validated_entry)
    return {"schema_version": SCHEMA_VERSION,
            "installer_version": data.get("installer_version", INSTALLER_VERSION),
            "roots": validated}


def classify_file(path: Path, desired: bytes | None, record: dict | None) -> str:
    """Classify one target without trusting a manifest path as authority."""
    path = Path(path)
    if _is_link_or_reparse(path):
        return "conflict"
    try:
        if not path.exists():
            return "missing"
        if not path.is_file():
            return "conflict"
        actual = _sha256(path.read_bytes())
    except OSError:
        return "conflict"
    if record is not None:
        return "owned_matching" if actual == record.get("sha256") else "conflict"
    if desired is not None and actual == _sha256(desired):
        return "unmanaged_matching"
    return "conflict"


def _validate_operation(operation: dict, allowed_roots: Mapping[str, tuple[Path, ...]]) -> None:
    if not isinstance(operation, dict):
        raise ManifestError("plan_operation_invalid")
    host = operation.get("host")
    if host not in allowed_roots:
        raise ManifestError("plan_host_invalid")
    root = _validate_root(operation.get("root"), host, allowed_roots)
    relative = _validate_relative(operation.get("relative_path"))
    path = operation.get("path")
    if not isinstance(path, str) or _norm(path) != _norm(root / relative):
        raise ManifestError("plan_path_outside_root")
    _validate_path_components(Path(path))
    if _is_link_or_reparse(root) or (root.exists() and not root.is_dir()):
        raise ManifestError("plan_root_not_a_directory")
    current = root
    for part in Path(relative).parent.parts:
        current = current / part
        if _is_link_or_reparse(current):
            raise ManifestError("plan_path_uses_link")
    if _is_link_or_reparse(Path(path)):
        raise ManifestError("plan_target_is_link")


def _record_map(manifest: dict) -> dict:
    records = {}
    for root_entry in manifest["roots"]:
        for record in root_entry["files"]:
            records[(_norm(root_entry["root"]), record["path"])] = record
    return records


def _decisions(plan: dict, manifest: dict, allowed_roots) -> list[dict]:
    action = plan.get("action")
    if action not in ("install", "uninstall"):
        raise ManifestError("plan_action_invalid")
    force = bool(plan.get("force"))
    operations = plan.get("operations")
    if not isinstance(operations, list):
        raise ManifestError("plan_operations_invalid")
    records = _record_map(manifest)
    decisions = []
    for operation in operations:
        _validate_operation(operation, allowed_roots)
        content = operation.get("content")
        if action == "install":
            if not isinstance(content, (bytes, bytearray)):
                raise ManifestError("plan_content_invalid")
            desired = bytes(content)
            digest = _sha256(desired)
            expected = operation.get("desired_sha256")
            if expected is not None and expected != digest:
                raise ManifestError("plan_digest_mismatch")
        else:
            desired = None
            digest = None
        record = records.get((_norm(operation["root"]), operation["relative_path"]))
        state = classify_file(Path(operation["path"]), desired, record)
        if action == "install":
            if state == "missing":
                decision = "create"
            elif state == "owned_matching":
                decision = "keep" if record["sha256"] == digest else "update"
            elif state == "unmanaged_matching":
                decision = "adopt" if force else "keep"
            elif force:
                decision = "update"
            else:
                decision = "conflict"
        else:
            decision = "remove" if state == "owned_matching" else "keep"
        target = Path(operation["path"])
        if state == "owned_matching":
            observed = record["sha256"]
        elif state == "unmanaged_matching":
            observed = digest
        else:
            observed = _sha256(target.read_bytes()) if target.is_file() else None
        decisions.append({**operation, "decision": decision, "state": state,
                          "record": record, "desired_sha256": digest,
                          "_observed_sha256": observed})
    return decisions


def _check_unchanged(decision: dict) -> None:
    """User editors do not take our lock; recheck before each mutation."""
    _check_target_unchanged(Path(decision["path"]), decision["_observed_sha256"])


def _check_target_unchanged(path: Path, expected: str | None) -> None:
    try:
        unchanged = (not path.exists() if expected is None else
                     path.is_file() and _sha256(path.read_bytes()) == expected)
    except OSError:
        unchanged = False
    if not unchanged:
        raise InstallFailed("concurrent_file_change",
                            "integration file changed during installation; user changes preserved")


def _lock_path(manifest_path: Path) -> Path:
    return Path(manifest_path).with_name(LOCK_NAME)


def _transaction_path(manifest_path: Path) -> Path:
    return Path(manifest_path).with_name(TRANSACTION_NAME)


def preflight(plan: dict, *, manifest_path: Path, allowed_roots) -> list[dict]:
    """Return per-operation decisions; no directory, lock or manifest is created."""
    manifest_path = Path(manifest_path)
    if _lock_path(manifest_path).exists():
        raise InstallBusy()
    if _transaction_path(manifest_path).exists():
        raise IncompleteTransaction(_transaction_path(manifest_path))
    manifest = read_manifest(manifest_path, allowed_roots=allowed_roots)
    return _decisions(plan, manifest, allowed_roots)


def _atomic_write_bytes(path, data: bytes, mode: int | None = None, *,
                        expected_sha256=_UNCHECKED) -> None:
    path = Path(path)
    _validate_path_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = 0o755 if path.suffix == ".sh" else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=".reproagent-tmp-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        _validate_path_components(path)
        if expected_sha256 is not _UNCHECKED:
            _check_target_unchanged(path, expected_sha256)
        if expected_sha256 is None:
            # Windows rename rejects an existing destination; POSIX rename would
            # replace it, so use a same-directory exclusive hard link there.
            try:
                if os.name == "nt":
                    os.rename(temporary, path)
                else:
                    os.link(temporary, path)
            except FileExistsError as exc:
                raise InstallFailed("concurrent_file_change") from exc
            if os.name != "nt":
                os.unlink(temporary)
        else:
            os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _write_manifest(path: Path, manifest: dict) -> None:
    path = Path(path)
    _validate_path_components(path)
    if not manifest["roots"]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    _atomic_write_bytes(path, _manifest_bytes(manifest), mode=0o600)


def _write_transaction(path: Path, transaction: dict) -> None:
    _atomic_write_bytes(path, json.dumps(transaction, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                        mode=0o600)


class _Lock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.descriptor = None

    def acquire(self) -> None:
        _validate_path_components(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise InstallBusy() from exc
        try:
            os.write(self.descriptor, json.dumps({"pid": os.getpid(),
                "time": datetime.now(timezone.utc).isoformat()}).encode("utf-8"))
        except OSError:
            self.release()
            raise

    def release(self) -> None:
        if self.descriptor is not None:
            try:
                os.close(self.descriptor)
            finally:
                self.descriptor = None
        try:
            _validate_path_components(self.path)
        except ManifestError:
            # A redirected control directory is no longer ours to unlink through.
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _backup(path: Path) -> tuple[Path, str]:
    _validate_path_components(path)
    data = path.read_bytes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(path.name + BACKUP_MARKER + stamp + "-" + secrets.token_hex(4))
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return backup, _sha256(data)


def _build_manifest(old: dict, decisions: list[dict], backups: list[tuple[tuple, dict]],
                    binding: dict | None = None) -> dict:
    entries = {}
    for root_entry in old["roots"]:
        key = (root_entry["host"], _norm(root_entry["root"]))
        entries[key] = {
            "host": root_entry["host"], "layout": root_entry["layout"],
            "root": root_entry["root"],
            "files": {record["path"]: dict(record) for record in root_entry["files"]},
            "backups": [dict(record) for record in root_entry.get("backups", [])],
        }
        if "binding" in root_entry:
            entries[key]["binding"] = dict(root_entry["binding"])
    for decision in decisions:
        key = (decision["host"], _norm(decision["root"]))
        entry = entries.setdefault(key, {
            "host": decision["host"], "layout": decision["layout"],
            "root": decision["root"], "files": {}, "backups": [],
        })
        if decision["decision"] in ("create", "update", "adopt"):
            if binding is not None:
                entry["binding"] = decode_binding(encode_binding(binding))
            entry["files"][decision["relative_path"]] = {
                "path": decision["relative_path"],
                "sha256": decision["desired_sha256"],
                "absolute": decision["path"],
            }
        elif decision["decision"] == "remove":
            entry["files"].pop(decision["relative_path"], None)
    for key, record in backups:
        entry = entries.setdefault(key, {
            "host": key[0], "layout": "", "root": decisions[0]["root"] if decisions else "",
            "files": {}, "backups": [],
        })
        entry["backups"].append(record)
    roots = []
    for entry in entries.values():
        entry["files"] = sorted(entry["files"].values(), key=lambda item: item["path"])
        entry["backups"] = sorted(entry["backups"], key=lambda item: item["path"])
        if entry["files"] or entry["backups"]:
            roots.append(entry)
    roots.sort(key=lambda item: (item["host"], item["root"]))
    return {"schema_version": SCHEMA_VERSION, "installer_version": INSTALLER_VERSION,
            "roots": roots}


def _restore(path: Path, data: bytes, mode: int | None) -> None:
    _atomic_write_bytes(path, data, mode=mode)


def _rollback(completed: list[dict], old_manifest: bytes | None, new_manifest: bytes | None,
              manifest_path: Path, transaction_path: Path, staging: Path,
              transaction: dict) -> list[dict]:
    problems = []
    try:
        _validate_path_components(manifest_path)
        _validate_path_components(transaction_path)
        _validate_path_components(staging)
    except ManifestError:
        return [{"path": str(manifest_path), "reason": "control_path_uses_link"}]
    for item in reversed(completed):
        path = Path(item["path"])
        kind = item["kind"]
        try:
            _validate_path_components(path)
            if kind == "create":
                if path.exists():
                    if _sha256(path.read_bytes()) == item["desired_sha256"]:
                        path.unlink()
                    else:
                        problems.append({"path": str(path), "reason": "concurrent_edit"})
            elif kind in ("update", "adopt"):
                if not path.exists():
                    problems.append({"path": str(path), "reason": "concurrent_delete"})
                elif _sha256(path.read_bytes()) != item["desired_sha256"]:
                    problems.append({"path": str(path), "reason": "concurrent_edit"})
                elif not item.get("staged") or not Path(item["staged"]).exists():
                    problems.append({"path": str(path), "reason": "staged_copy_missing"})
                else:
                    _restore(path, Path(item["staged"]).read_bytes(), item.get("mode"))
            elif kind == "remove":
                if path.exists():
                    problems.append({"path": str(path), "reason": "concurrent_create"})
                elif not item.get("staged") or not Path(item["staged"]).exists():
                    problems.append({"path": str(path), "reason": "staged_copy_missing"})
                else:
                    _restore(path, Path(item["staged"]).read_bytes(), item.get("mode"))
        except (OSError, ManifestError):
            problems.append({"path": str(path), "reason": "restore_failed"})
    try:
        _validate_path_components(manifest_path)
        _validate_path_components(transaction_path)
        _validate_path_components(staging)
    except ManifestError:
        # Do not read, restore, unlink or clean up through a redirected control dir.
        problems.append({"path": str(manifest_path), "reason": "control_path_uses_link"})
        return problems
    try:
        current = manifest_path.read_bytes() if manifest_path.exists() else None
    except OSError:
        current = None
    if new_manifest is None:
        # The planned commit removed the manifest (for example, the last file was
        # uninstalled). Restore the old record if that removal already happened.
        if current is None and old_manifest is not None:
            _restore(manifest_path, old_manifest, 0o600)
        elif current is not None and (old_manifest is None or current != old_manifest):
            problems.append({"path": str(manifest_path), "reason": "manifest_changed"})
    elif current == new_manifest:
        if old_manifest is None:
            try:
                manifest_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _restore(manifest_path, old_manifest, 0o600)
    elif current is not None and current != (old_manifest or b""):
        problems.append({"path": str(manifest_path), "reason": "manifest_changed"})
    if problems:
        transaction["phase"] = "rollback_incomplete"
        transaction["problems"] = problems
        try:
            _write_transaction(transaction_path, transaction)
        except OSError:
            pass
        return problems
    try:
        transaction_path.unlink()
    except FileNotFoundError:
        pass
    shutil.rmtree(staging, ignore_errors=True)
    return []


def _summary(plan: dict, decisions: list[dict], backups: list[dict], changed: bool) -> dict:
    groups = {"create": "created", "update": "updated", "adopt": "adopted",
              "remove": "removed", "keep": "kept", "conflict": "conflicts"}
    result = {"action": plan.get("action"), "changed": changed,
              "preserved": [], "conflicts": [], "backups": list(backups)}
    for key in ("created", "updated", "adopted", "removed", "kept", "conflicts"):
        result[key] = []
    for decision in decisions:
        bucket = groups[decision["decision"]]
        result[bucket].append(decision["path"])
        if decision["decision"] in ("keep", "conflict"):
            result["preserved"].append(decision["path"])
    return result


def apply_plan(plan: dict, *, manifest_path: Path, allowed_roots) -> dict:
    """Apply an install or uninstall plan after preflight, lock and transaction."""
    manifest_path = Path(manifest_path)
    old_manifest = read_manifest(manifest_path, allowed_roots=allowed_roots)
    decisions = _decisions(plan, old_manifest, allowed_roots)
    conflicts = [decision for decision in decisions if decision["decision"] == "conflict"]
    if conflicts and not plan.get("force"):
        raise PlanConflict([{"path": item["path"], "state": item["state"]} for item in conflicts])
    changes = [decision for decision in decisions
               if decision["decision"] in ("create", "update", "adopt", "remove")]
    if not changes:
        return _summary(plan, decisions, [], False)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _Lock(_lock_path(manifest_path))
    lock.acquire()
    backup_records = []
    try:
        transaction_path = _transaction_path(manifest_path)
        if transaction_path.exists():
            raise IncompleteTransaction(transaction_path)
        old_manifest = read_manifest(manifest_path, allowed_roots=allowed_roots)
        decisions = _decisions(plan, old_manifest, allowed_roots)
        conflicts = [decision for decision in decisions if decision["decision"] == "conflict"]
        if conflicts and not plan.get("force"):
            raise PlanConflict([{"path": item["path"], "state": item["state"]} for item in conflicts])
        changes = [decision for decision in decisions
                   if decision["decision"] in ("create", "update", "adopt", "remove")]
        if not changes:
            return _summary(plan, decisions, [], False)
        old_manifest_bytes = manifest_path.read_bytes() if manifest_path.exists() else None
        transaction_id = secrets.token_hex(8)
        staging = manifest_path.parent / (".transaction-" + transaction_id)
        staging.mkdir()
        transaction = {
            "transaction_id": transaction_id, "action": plan.get("action"),
            "phase": "writing",
            "old_manifest_b64": (base64.b64encode(old_manifest_bytes).decode("ascii")
                                 if old_manifest_bytes is not None else None),
            "new_manifest_sha256": None, "completed": [],
        }
        _write_transaction(transaction_path, transaction)
        completed = []
        try:
            for index, decision in enumerate(changes):
                path = Path(decision["path"])
                _validate_operation(decision, allowed_roots)
                _validate_path_components(manifest_path)
                _check_unchanged(decision)
                kind = decision["decision"]
                item = {"path": str(path), "kind": kind,
                        "desired_sha256": decision["desired_sha256"],
                        "staged": None, "mode": None}
                if kind in ("update", "adopt", "remove"):
                    old_data = path.read_bytes()
                    staged = staging / str(index)
                    _atomic_write_bytes(staged, old_data, mode=0o600)
                    item["staged"] = str(staged)
                    item["mode"] = stat.S_IMODE(path.stat().st_mode)
                if kind in ("adopt",) or (kind == "update" and decision["state"] == "conflict"):
                    backup, digest = _backup(path)
                    root_key = (decision["host"], _norm(decision["root"]))
                    backup_records.append((root_key, {"path": str(backup), "sha256": digest}))
                # Staging and backup are I/O: edits during either must survive too.
                _validate_operation(decision, allowed_roots)
                _check_unchanged(decision)
                if kind == "remove":
                    path.unlink()
                else:
                    _atomic_write_bytes(path, decision["content"],
                                        expected_sha256=decision["_observed_sha256"])
                completed.append(item)
                transaction["completed"] = completed
                _write_transaction(transaction_path, transaction)
            new_manifest = _build_manifest(
                old_manifest, decisions,
                [(key, record) for key, record in backup_records], binding=plan.get("_binding"))
            new_manifest_bytes = _manifest_bytes(new_manifest) if new_manifest["roots"] else None
            transaction["new_manifest_sha256"] = (_sha256(new_manifest_bytes)
                                                  if new_manifest_bytes is not None else None)
            _write_manifest(manifest_path, new_manifest)
            transaction["phase"] = "committed"
            try:
                _write_transaction(transaction_path, transaction)
            except OSError:
                pass
            try:
                transaction_path.unlink()
            except FileNotFoundError:
                pass
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            old_bytes = (base64.b64decode(transaction["old_manifest_b64"])
                         if transaction.get("old_manifest_b64") is not None else None)
            planned = _build_manifest(old_manifest, decisions,
                                      [(key, record) for key, record in backup_records],
                                      binding=plan.get("_binding"))
            new_bytes = _manifest_bytes(planned) if planned["roots"] else None
            problems = _rollback(completed, old_bytes, new_bytes, manifest_path,
                                 transaction_path, staging, transaction)
            if problems:
                raise RollbackIncomplete(transaction_path) from exc
            if isinstance(exc, InstallFailed):
                raise
            raise InstallFailed("install_failed") from exc
        backups = [record for _, record in backup_records]
        return _summary(plan, decisions, backups, True)
    finally:
        lock.release()


__all__ = ["ALLOWED_FILES", "INSTALLER_VERSION", "IncompleteTransaction", "InstallBusy",
           "InstallFailed", "ManifestError", "PlanConflict", "RollbackIncomplete",
           "apply_plan", "classify_file", "preflight", "read_manifest"]
