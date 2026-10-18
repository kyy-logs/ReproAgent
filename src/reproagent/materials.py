"""Bounded, redacted diagnostic material; IDs never accept filesystem paths."""
from __future__ import annotations

import re
from copy import deepcopy

from .core.serialization import bytes_hash, canonical_bytes, parse_json
from .paths import workspace_path
from .store import atomic_write, safe_child


class MaterialStore:
    item_limit = 256 * 1024
    total_limit = 4 * 1024 * 1024
    count_limit = 128

    def __init__(self, store, secrets=()):
        self.store = store
        self.secrets = tuple(s for s in secrets if s)
        self._entries = {}
        directory = safe_child(store.root, 'materials')
        if directory.is_dir():
            for path in list(directory.glob('m-*.json'))[:self.count_limit]:
                try:
                    if path.stat().st_size > 4096: continue
                    data = parse_json(safe_child(store.root, 'materials/' + path.name).read_text(encoding='utf-8'))
                    identifier = data['id']
                    if (not re.fullmatch(r'm-[a-f0-9]{64}', identifier)
                            or path.name != identifier + '.json'
                            or data['path'] != f'materials/{identifier}.txt'
                            or data['source_hash'] != identifier[2:]
                            or type(data['stored_bytes']) is not int
                            or not 0 <= data['stored_bytes'] <= self.item_limit):
                        continue
                    if sum(x['stored_bytes'] for x in self._entries.values()) + data['stored_bytes'] > self.total_limit:
                        continue
                    self._entries[identifier] = data
                except (OSError, ValueError, TypeError, KeyError):
                    continue

    def redact(self, text):
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        return text

    def catalog(self):
        with self.store._lock:
            return deepcopy(list(self._entries.values()))

    def capture(self, text: str, *, kind: str, description: str, upstream_complete=None) -> dict:
        with self.store._lock:
            try:
                raw = self.redact(text).encode('utf-8')
                remaining = max(0, self.total_limit - sum(e['stored_bytes'] for e in self._entries.values()))
                kept = raw[:min(self.item_limit, remaining)].decode('utf-8', errors='ignore').encode('utf-8')
                identifier = 'm-' + bytes_hash(kind.encode('utf-8') + b'\0' + raw)
                if identifier in self._entries:
                    # A duplicate prefix must not claim the larger input is complete.
                    result = dict(self._entries[identifier])
                    result.update(source_bytes=len(raw), storage_complete=len(raw) == len(kept),
                                  upstream_complete=upstream_complete)
                    return result
                if len(self._entries) >= self.count_limit or (raw and not kept):
                    return {'code': 'material_capacity'}
                meta = dict(id=identifier, path=f'materials/{identifier}.txt', hash=bytes_hash(kept), source_hash=identifier[2:],
                            kind=self.redact(kind)[:64], description=self.redact(description)[:160],
                            source_bytes=len(raw), stored_bytes=len(kept), storage_complete=len(raw) == len(kept),
                            upstream_complete=upstream_complete, read_via='read_artifact')
                body_path = safe_child(self.store.root, meta['path'])
                if not body_path.exists(): atomic_write(body_path, kept)
                elif body_path.read_bytes() != kept: return {'code': 'artifact_integrity_error'}
                atomic_write(safe_child(self.store.root, f'materials/{identifier}.json'), canonical_bytes(meta))
                self._entries[identifier] = meta
                return deepcopy(meta)
            except (OSError, ValueError, TypeError, UnicodeError):
                return {'code': 'material_store_error'}

    def read(self, artifact_id, offset=1, limit=100, max_bytes=32768, column=0):
        if (type(offset) is not int or type(limit) is not int or offset < 1 or not 1 <= limit <= 200
                or type(column) is not int or not 0 <= column <= self.item_limit):
            return {'code': 'invalid_artifact_range'}
        with self.store._lock:
            if not isinstance(artifact_id, str) or artifact_id not in self._entries:
                return {'code': 'unknown_artifact'}
            meta = self._entries[artifact_id]
            try:
                with safe_child(self.store.root, meta['path']).open('rb') as handle:
                    raw = handle.read(self.item_limit + 1)
                if len(raw) != meta['stored_bytes'] or bytes_hash(raw) != meta['hash']:
                    return {'code': 'artifact_integrity_error'}
                text = raw.decode('utf-8')
            except (OSError, ValueError, UnicodeError):
                return {'code': 'artifact_integrity_error'}
        lines = text.splitlines(keepends=True)
        selected = lines[offset-1:offset-1+limit]
        if column and (not selected or column >= len(selected[0])):
            return {'code':'invalid_artifact_range'}
        if selected: selected[0] = selected[0][column:]
        end = offset - 1 + len(selected)
        result = dict(code='ready', id=artifact_id, offset=offset, text=''.join(selected),
                      next_offset=end+1 if end < len(lines) else None, next_column=0, excerpt_truncated=False,
                      storage_complete=meta['storage_complete'], upstream_complete=meta['upstream_complete'])
        if len(canonical_bytes(result)) <= max_bytes: return result
        # Whole lines when possible; one oversized line is an explicitly partial view.
        while len(selected) > 1 and len(canonical_bytes(result)) > max_bytes:
            selected.pop()
            result.update(text=''.join(selected), next_offset=offset+len(selected), excerpt_truncated=True)
        if len(canonical_bytes(result)) > max_bytes:
            result.update(text='', next_offset=offset, next_column=column, excerpt_truncated=True)
            value = selected[0] if selected else ''
            lo,hi=0,len(value)
            while lo < hi:
                mid=(lo+hi+1)//2
                result.update(text=value[:mid],next_column=column+mid)
                if len(canonical_bytes(result)) <= max_bytes:lo=mid
                else:hi=mid-1
            if lo == 0: return {'code':'response_capacity'}
            result.update(text=value[:lo],next_column=column+lo)
        return result if len(canonical_bytes(result)) <= max_bytes else {'code': 'response_capacity'}
