import json
from dataclasses import replace

import pytest

from reproagent import experience as exp
from reproagent.core.serialization import canonical_bytes
from tests.unit.test_experience_store import card


def test_directory_write_and_category_index(tmp_path):
    root = tmp_path / 'memory'
    assert exp.append_experience(root, card(), None) == 'written'
    body = json.loads((root / 'model/experiences.json').read_text())
    index = json.loads((root / 'model/index.json').read_text())
    assert body['schema_version'] == 2 and body['category'] == 'model'
    assert set(index['items'][0]) == {'id', 'description'}
    snapshot = exp.load_experience_snapshot(root)
    assert snapshot.state == 'ready' and snapshot.cards[0].id == card().id
    assert not (root / 'workflow/experiences.json').exists()


@pytest.mark.parametrize('mode', ['missing', 'invalid', 'stale', 'forged'])
def test_cache_never_controls_body_or_fingerprint(tmp_path, mode):
    from reproagent.experience_storage import load_directory_snapshot
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    before = exp.load_experience_snapshot(root)
    index = root / 'model/index.json'
    if mode == 'missing':
        index.unlink()
    elif mode == 'invalid':
        index.write_bytes(b'bad')
    else:
        data = json.loads(index.read_text())
        if mode == 'stale': data['source_hash'] = 'b' * 64
        else: data['items'][0]['description'] = 'forged'
        index.write_bytes(canonical_bytes(data))
    written = index.read_bytes() if index.exists() else None
    diagnostics = []
    after = load_directory_snapshot(root, diagnostic=lambda c, code: diagnostics.append((c, code)))
    assert before == after
    assert diagnostics
    assert (index.read_bytes() if index.exists() else None) == written


def test_cache_write_failure_keeps_committed_card(tmp_path, monkeypatch):
    import reproagent.experience_storage as storage
    original = storage.atomic_write
    def fail_index(path, data):
        if path.name == 'index.json': raise OSError('cache unavailable')
        return original(path, data)
    monkeypatch.setattr(storage, 'atomic_write', fail_index)
    notes = []
    root = tmp_path / 'memory'
    assert storage.append_directory_experience(root, card(), None,
        diagnostic=lambda c, code: notes.append((c, code))) == 'written'
    assert notes == [('model', 'index_update_failed')]
    assert exp.load_experience_snapshot(root).cards == (card(),)


def test_corrupt_body_isolated_and_not_overwritten(tmp_path):
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    body = root / 'model/experiences.json'
    body.write_bytes(b'bad')
    assert exp.load_experience_snapshot(root).state == 'store_error'
    assert exp.append_experience(root, card('new'), None) == 'store_error'
    assert body.read_bytes() == b'bad'


def test_directory_duplicate_and_snapshot_freeze(tmp_path):
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    old = exp.load_experience_snapshot(root)
    assert exp.append_experience(root, replace(card(), detail='changed'), None) == 'duplicate'
    assert exp.append_experience(root, card('new'), None) == 'written'
    assert len(old.cards) == 1
    assert len(exp.load_experience_snapshot(root).cards) == 2


def test_directory_rejects_symlink_escape(tmp_path):
    root = tmp_path / 'memory'
    root.mkdir()
    external = tmp_path / 'outside'
    external.mkdir()
    try: (root / 'model').symlink_to(external, target_is_directory=True)
    except OSError: pytest.skip('symlink privileges unavailable')
    assert exp.append_experience(root, card(), None) == 'store_error'
    assert not list(external.iterdir())


def test_directory_long_path(tmp_path):
    root = tmp_path / ('a' * 100) / ('b' * 100) / 'memory'
    assert exp.append_experience(root, card(), None) == 'written'
    assert exp.load_experience_snapshot(root).cards == (card(),)


def test_busy_snapshot_and_append_do_not_modify_body(tmp_path):
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    before = (root / 'model/experiences.json').read_bytes()
    with exp._library_lock(root):
        assert exp.load_experience_snapshot(root).state == 'store_error'
        assert exp.append_experience(root, card('new'), None) == 'busy'
    assert (root / 'model/experiences.json').read_bytes() == before


def test_atomic_body_write_failure_preserves_previous_generation(tmp_path, monkeypatch):
    import reproagent.experience_storage as storage
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    before = (root / 'model/experiences.json').read_bytes()
    def fail(*args): raise OSError('cannot replace')
    monkeypatch.setattr(storage, 'atomic_write', fail)
    assert exp.append_experience(root, card('new'), None) == 'store_error'
    assert (root / 'model/experiences.json').read_bytes() == before


def test_total_library_limit_is_shared_by_categories(tmp_path):
    root = tmp_path / 'memory'
    for category in ('model', 'workflow'):
        members = []
        for i in range(340):
            sample = replace(card(str(i), 'x' * 1400), category=category)
            sample = replace(sample, id=exp._identifier(category, sample.tags, sample.summary))
            members.append(exp._card_record(sample))
        folder = root / category
        folder.mkdir(parents=True)
        (folder / 'experiences.json').write_bytes(canonical_bytes({'schema_version':2,'category':category,'items':members}))
    assert exp.load_experience_snapshot(root).state == 'store_error'
    before = (root / 'model/experiences.json').read_bytes()
    assert exp.append_experience(root, card('new'), None) == 'store_error'
    assert (root / 'model/experiences.json').read_bytes() == before
