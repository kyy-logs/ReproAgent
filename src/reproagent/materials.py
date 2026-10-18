"""Bounded, redacted diagnostic material; IDs never accept filesystem paths."""
from __future__ import annotations

import re
from copy import deepcopy

from .core.serialization import bytes_hash, canonical_bytes, parse_json
from .store import atomic_write, safe_child


class MaterialStore:
    item_limit = 256 * 1024
    total_limit = 4 * 1024 * 1024
    count_limit = 128

    def __init__(self, store, secrets=()):
        self.store = store
        self.secrets = tuple(s for s in secrets if s)
        self._entries = {}
        self._unavailable = False
        self._orphan_bytes = 0
        self._orphan_count = 0
        try:
            directory = safe_child(store.root, 'materials')
            if not directory.is_dir():
                return
            for path in directory.glob('m-*.json'):
                if len(self._entries) >= self.count_limit:
                    break
                try:
                    if path.stat().st_size > 4096:
                        continue
                    data = parse_json(safe_child(store.root, 'materials/' + path.name).read_text(encoding='utf-8'))
                    if not self._valid_metadata(data, path.name):
                        continue
                    if sum(x['stored_bytes'] for x in self._entries.values()) + data['stored_bytes'] > self.total_limit:
                        continue
                    self._entries[data['id']] = data
                except (OSError, ValueError, TypeError, KeyError):
                    continue
            # A crash between body and metadata writes must not reset its disk quota.
            for path in directory.glob('m-*.txt'):
                if path.stem not in self._entries:
                    self._orphan_bytes += safe_child(store.root, 'materials/' + path.name).stat().st_size
                    self._orphan_count += 1
        except (OSError, ValueError):
            self._unavailable = True

    def _valid_metadata(self, data, filename):
        if not isinstance(data, dict):
            return False
        required = {'id','path','hash','source_hash','kind','description','source_bytes',
                    'stored_bytes','storage_complete','upstream_complete','read_via'}
        if set(data) != required:
            return False
        identifier = data['id']
        return (isinstance(identifier, str) and re.fullmatch(r'm-[a-f0-9]{64}', identifier)
                and filename == identifier + '.json'
                and data['path'] == f'materials/{identifier}.txt'
                and data['source_hash'] == identifier[2:]
                and isinstance(data['hash'], str) and re.fullmatch(r'[a-f0-9]{64}', data['hash'])
                and isinstance(data['kind'], str) and len(data['kind']) <= 64
                and isinstance(data['description'], str) and len(data['description']) <= 160
                and type(data['stored_bytes']) is int and 0 <= data['stored_bytes'] <= self.item_limit
                and type(data['source_bytes']) is int and data['source_bytes'] >= data['stored_bytes']
                and type(data['storage_complete']) is bool
                and data['storage_complete'] == (data['source_bytes'] == data['stored_bytes'])
                and (data['upstream_complete'] is None or type(data['upstream_complete']) is bool)
                and data['read_via'] == 'read_artifact')

    def redact(self, text):
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        return text

    def catalog(self):
        with self.store._lock:
            return deepcopy(list(self._entries.values()))

    def capture(self, text: str, *, kind: str, description: str, upstream_complete=None) -> dict:
        with self.store._lock:
            if self._unavailable:
                return {'code': 'material_store_error'}
            created_body = None
            try:
                raw = self.redact(text).encode('utf-8')
                identifier = 'm-' + bytes_hash(kind.encode('utf-8') + b'\0' + raw)
                if identifier in self._entries:
                    return deepcopy(self._entries[identifier])
                remaining = max(0, self.total_limit - self._orphan_bytes
                                - sum(e['stored_bytes'] for e in self._entries.values()))
                kept = raw[:min(self.item_limit, remaining)].decode('utf-8', errors='ignore').encode('utf-8')
                if len(self._entries) + self._orphan_count >= self.count_limit or (raw and not kept):
                    return {'code': 'material_capacity'}
                meta = dict(id=identifier, path=f'materials/{identifier}.txt', hash=bytes_hash(kept), source_hash=identifier[2:],
                            kind=self.redact(kind)[:64], description=self.redact(description)[:160],
                            source_bytes=len(raw), stored_bytes=len(kept), storage_complete=len(raw) == len(kept),
                            upstream_complete=upstream_complete, read_via='read_artifact')
                if not self._valid_metadata(meta, identifier + '.json'):
                    return {'code': 'material_store_error'}
                body_path = safe_child(self.store.root, meta['path'])
                if not body_path.exists():
                    atomic_write(body_path, kept)
                    created_body = body_path
                elif body_path.read_bytes() != kept:
                    return {'code': 'artifact_integrity_error'}
                else:
                    # This existing body was accounted as an orphan on reload.
                    self._orphan_bytes = max(0, self._orphan_bytes - len(kept))
                    self._orphan_count = max(0, self._orphan_count - 1)
                    created_body = body_path
                atomic_write(safe_child(self.store.root, f'materials/{identifier}.json'), canonical_bytes(meta))
                self._entries[identifier] = meta
                return deepcopy(meta)
            except (OSError, ValueError, TypeError, UnicodeError):
                if created_body is not None:
                    try:
                        created_body.unlink(missing_ok=True)
                    except OSError:
                        # Stop further writes if rollback itself is unavailable.
                        self._unavailable = True
                return {'code': 'material_store_error'}

    def _catalog_page(self, offset, limit, max_bytes):
        entries = list(self._entries.values())
        selected = entries[offset-1:offset-1+limit]
        keys = ('id','kind','description','stored_bytes','storage_complete','upstream_complete')
        result = dict(code='ready', id='catalog', offset=offset, total=len(entries),
                      items=[{key:item[key] for key in keys} for item in selected], next_offset=None)
        while True:
            end = offset - 1 + len(result['items'])
            result['next_offset'] = end + 1 if end < len(entries) else None
            if len(canonical_bytes(result)) <= max_bytes:
                if not result['items'] and selected:
                    return {'code': 'response_capacity'}
                return result
            if not result['items']:
                return {'code': 'response_capacity'}
            result['items'].pop()

    def read(self, artifact_id, offset=1, limit=100, max_bytes=32768, column=0):
        if (type(offset) is not int or type(limit) is not int or offset < 1 or not 1 <= limit <= 200
                or type(column) is not int or not 0 <= column <= self.item_limit):
            return {'code': 'invalid_artifact_range'}
        with self.store._lock:
            if artifact_id == 'catalog':
                if column:
                    return {'code': 'invalid_artifact_range'}
                return self._catalog_page(offset, limit, max_bytes)
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
