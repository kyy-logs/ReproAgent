"""Category JSON libraries with disposable, verified catalog caches."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .core.serialization import canonical_bytes, canonical_hash, bytes_hash, parse_json
from .paths import workspace_path, directory_path, is_within
from .store import atomic_write
from .experience_catalog import entry_from_card


def directory_mode(path: Path) -> bool:
    path = workspace_path(path)
    if path.is_dir():
        if path.suffix.lower() == '.json':
            raise ValueError('JSON experience path is a directory')
        return True
    if path.exists() and path.suffix.lower() != '.json':
        raise ValueError('experience files must use .json')
    return path.suffix.lower() != '.json'


def _notify(callback, category, code):
    if callback is not None:
        try: callback(category, code)
        except (OSError, ValueError, RuntimeError): pass


def _paths(root):
    from .experience import CATEGORIES
    root = Path(root).resolve()
    lock = workspace_path(root.with_name(root.name + '.lock'))
    if lock.is_symlink(): raise ValueError('experience lock cannot be a link')
    for category in CATEGORIES:
        for name in ('experiences.json', 'index.json'):
            path = root / category / name
            if not is_within(path.resolve(), root):
                raise ValueError('experience category path escapes library')
    return root


def _read_categories(root):
    from .experience import CATEGORIES, LIBRARY_BYTES, _card
    cards, bodies, total = [], {}, 0
    for category in CATEGORIES:
        path = workspace_path(root / category / 'experiences.json')
        try:
            with path.open('rb') as handle: content = handle.read(LIBRARY_BYTES + 1)
        except FileNotFoundError:
            bodies[category] = None
            continue
        total += len(content)
        if total > LIBRARY_BYTES: raise ValueError('experience library exceeds byte limit')
        data = parse_json(content.decode('utf-8'))
        if set(data) != {'schema_version', 'category', 'items'} or type(data['schema_version']) is not int or data['schema_version'] != 2 or data['category'] != category or not isinstance(data['items'], list):
            raise ValueError('invalid category library')
        current = tuple(_card(item) for item in data['items'])
        if any(card.category != category for card in current): raise ValueError('category mismatch')
        cards.extend(current)
        bodies[category] = content
    if len({card.id for card in cards}) != len(cards): raise ValueError('duplicate experience ID')
    return tuple(cards), bodies


def _fingerprint(bodies):
    return canonical_hash({category + '/experiences.json': bytes_hash(content) if content is not None else 'missing'
                           for category, content in bodies.items()})


def _index(category, content, cards):
    return {'schema_version': 1, 'category': category, 'source_hash': bytes_hash(content),
            'items': [asdict(entry_from_card(card)) for card in sorted(cards, key=lambda c: c.id) if card.category == category]}


def load_directory_snapshot(root: Path, *, secrets=(), diagnostic=None):
    from .experience import ExperienceSnapshot, LIBRARY_BYTES, _library_lock, _Busy
    try:
        root = _paths(root)
        with _library_lock(root):
            cards, bodies = _read_categories(root)
            if any(secret and secret in canonical_bytes(asdict(card)).decode('utf-8') for card in cards for secret in secrets):
                raise ValueError('known credential in experience')
            cache_total = 0
            for category, content in bodies.items():
                if content is None: continue
                expected = _index(category, content, cards)
                try:
                    with workspace_path(root / category / 'index.json').open('rb') as handle:
                        cached = handle.read(LIBRARY_BYTES + 1)
                    cache_total += len(cached)
                    if cache_total > LIBRARY_BYTES: raise ValueError('index capacity')
                    actual = parse_json(cached.decode('utf-8'))
                    if actual != expected:
                        _notify(diagnostic, category, 'stale' if actual.get('source_hash') != expected['source_hash'] else 'invalid')
                except FileNotFoundError: _notify(diagnostic, category, 'missing')
                except (OSError, ValueError, UnicodeError, TypeError, RecursionError): _notify(diagnostic, category, 'invalid')
            return ExperienceSnapshot('ready' if any(c is not None for c in bodies.values()) else 'missing', _fingerprint(bodies), cards)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, _Busy):
        return ExperienceSnapshot('store_error', '')


def directory_fingerprint(root: Path) -> str:
    snapshot = load_directory_snapshot(root)
    if snapshot.state == 'store_error': raise ValueError('invalid directory library')
    return snapshot.content_hash


def append_directory_experience(root: Path, card, context, *, diagnostic=None) -> str:
    from .experience import _card, _card_record, _check, _library_lock, _Busy, LIBRARY_BYTES
    from .core.budget import BudgetStopped
    try: card = _card(_card_record(card))
    except (ValueError, TypeError): return 'invalid'
    try:
        _check(context)
        root = _paths(root)
        with _library_lock(root):
            cards, bodies = _read_categories(root)
            if any(c.id == card.id for c in cards): return 'duplicate'
            members = tuple(c for c in cards if c.category == card.category) + (card,)
            content = canonical_bytes({'schema_version': 2, 'category': card.category, 'items': [_card_record(c) for c in members]})
            bodies[card.category] = content
            if sum(len(c) for c in bodies.values() if c is not None) > LIBRARY_BYTES: return 'store_error'
            index_content = canonical_bytes(_index(card.category, content, members))
            # The valid cache set is bounded by its derived projection, not arbitrary cache bytes.
            projected = sum(len(canonical_bytes(_index(k, v, (*cards, card)))) for k, v in bodies.items() if v is not None)
            if projected > LIBRARY_BYTES: return 'store_error'
            _check(context)
            atomic_write(root / card.category / 'experiences.json', content)
            try: atomic_write(root / card.category / 'index.json', index_content)
            except OSError: _notify(diagnostic, card.category, 'index_update_failed')
            return 'written'
    except _Busy: return 'busy'
    except BudgetStopped as exc: return 'skipped_cancelled' if exc.reason == 'CANCELLED' else 'timeout'
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError): return 'store_error'
