from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path

from .core.models import EvidenceRef, TaskEvent
from .core.serialization import REGISTRY, bytes_hash, canonical_bytes, decode_record, encode_record, parse_json
from .paths import is_within, relative_name, shared_path_form, workspace_path


def atomic_write(path: Path, content: bytes):
    path = workspace_path(path)
    # The temporary is longer than the file it replaces, and both names must use
    # one representation for the rename to work.
    path, temporary = shared_path_form(path, path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError(f"unsafe relative path: {relative!r}")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"unsafe relative path: {relative!r}")
    root = workspace_path(root)
    path = workspace_path(root.joinpath(*parts))
    if not is_within(path, root):
        raise ValueError(f"path escapes workspace: {relative}")
    return path


class TaskStore:
    def __init__(self, root: Path):
        self.root = workspace_path(Path(root).resolve())
        self._lock = threading.RLock()

    def record_path(self, kind: str, key: str) -> Path:
        if kind not in REGISTRY or kind in ("events", "manifest"):
            raise ValueError(f"unsupported collection: {kind}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", key) or ".." in key:
            raise ValueError(f"unsafe record key: {key}")
        if kind in ("task", "request"):
            relative = f"{kind}.json"
        elif kind == "candidates":
            relative = f"candidates/{key}/manifest.json"
        elif kind == "runs":
            relative = f"runs/{key}/execution.json"
        elif kind == "snapshots":
            relative = f"snapshots/{key}/snapshot.json"
        else:
            relative = f"{kind}/{key}.json"
        return safe_child(self.root, relative)

    def save_record(self, kind: str, key: str, obj) -> EvidenceRef:
        with self._lock:
            path = self.record_path(kind, key)
            if type(obj) is not REGISTRY[kind]:
                raise TypeError(f"wrong record type for {kind}")
            data = canonical_bytes(encode_record(obj)) + b"\n"
            if path.exists() and kind != "task":
                if path.read_bytes() != data:
                    raise ValueError(f"immutable {kind} record already exists: {key}")
            else:
                atomic_write(path, data)
            return EvidenceRef(relative_name(path, self.root), bytes_hash(data))

    def load_record(self, kind: str, key: str):
        path = self.record_path(kind, key)
        try:
            return decode_record(kind, parse_json(path.read_text(encoding="utf-8")))
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc

    def save_contract(self, contract):
        return self.save_record('contracts', f'{contract.contract_id}-v{contract.version}', contract)

    def load_contract(self, contract_id, version):
        key = f'{contract_id}-v{version}'
        if not self.record_path('contracts', key).exists():
            key = contract_id
        contract = self.load_record('contracts', key)
        if contract.version != version:
            raise ValueError('contract version mismatch')
        return contract

    def read_events(self):
        path = workspace_path(self.root / "events.jsonl")
        events, errors = [], []
        if not path.exists():
            return events, errors
        for index, line in enumerate(path.read_bytes().splitlines(keepends=True)):
            try:
                if not line.endswith(b"\n"):
                    raise ValueError("incomplete event tail")
                event = decode_record("events", parse_json(line.decode("utf-8")))
                if event.seq != index:
                    raise ValueError("non-monotonic event sequence")
                events.append(event)
            except (ValueError, UnicodeError) as exc:
                errors.append(f"events.jsonl:{index + 1}: {exc}")
                break
        return events, errors

    def append_event(self, kind: str, refs: tuple[str, ...], payload: dict) -> TaskEvent:
        with self._lock:
            events, errors = self.read_events()
            if errors:
                raise ValueError("cannot append to corrupted event stream")
            event = TaskEvent(len(events), kind, refs, payload, time.time())
            self.root.mkdir(parents=True, exist_ok=True)
            with workspace_path(self.root / "events.jsonl").open("ab") as handle:
                handle.write(canonical_bytes(encode_record(event)) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def inspect(self):
        events, errors = self.read_events()
        task = None
        if workspace_path(self.root / "task.json").exists():
            try:
                task = encode_record(self.load_record("task", "task"))
            except ValueError as exc:
                errors.append(str(exc))
        known = {ref for event in events for ref in event.refs}
        orphans = []
        for kind in ("contracts", "environments", "candidates", "runs", "snapshots", "verdicts"):
            directory = workspace_path(self.root / kind)
            if directory.exists():
                pattern = {'runs':'*/execution.json', 'snapshots':'*/snapshot.json', 'candidates':'*/manifest.json'}.get(kind, '*.json')
                for path in directory.glob(pattern):
                    if path.name.endswith(".tmp"):
                        continue
                    relative = f"{kind}/{path.relative_to(directory).as_posix()}"
                    try:
                        decode_record(kind, parse_json(path.read_text(encoding="utf-8")))
                    except (ValueError, UnicodeError) as exc:
                        errors.append(f"{relative}: {exc}")
                    if relative not in known:
                        orphans.append(relative)
        return {"task": task, "event_count": len(events), "incomplete_records": bool(errors), "errors": errors, "unreferenced_records": orphans}
