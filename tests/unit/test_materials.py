import json
import pytest
from reproagent.store import TaskStore


def material_store(tmp_path, **kwargs):
    from reproagent.materials import MaterialStore
    return MaterialStore(TaskStore(tmp_path), **kwargs)


def test_capture_read_redacts_and_paginates(tmp_path):
    store = material_store(tmp_path, secrets=('secret-42',))
    meta = store.capture('first\nsecret-42\nlast', kind='tool', description='Read result')
    page = store.read(meta['id'], offset=2, limit=1)
    assert page['text'] == '[REDACTED]\n'
    assert page['next_offset'] == 3
    assert 'secret-42' not in ''.join(p.read_text(encoding='utf-8') for p in (tmp_path/'materials').rglob('*') if p.is_file())
    assert store.read(meta['id'], offset=3)['text'] == 'last'
    assert meta['storage_complete'] is True


def test_material_limits_are_utf8_safe_and_explicit(tmp_path):
    store = material_store(tmp_path)
    store.item_limit = 10
    store.total_limit = 14
    a = store.capture('中'*20, kind='tool', description='long')
    assert a['stored_bytes'] == 9 and a['source_bytes'] == 60
    assert a['storage_complete'] is False
    assert store.read(a['id'])['text'] == '中'*3
    b = store.capture('abcdefg', kind='tool', description='next')
    assert b['stored_bytes'] == 5
    assert store.capture('z', kind='tool', description='full')['code'] == 'material_capacity'


def test_unknown_paths_and_tampered_bodies_are_refused(tmp_path):
    store = material_store(tmp_path)
    for bad in ('../outside', '/etc/passwd', 'C:/Windows/win.ini', 'unknown'):
        assert store.read(bad)['code'] == 'unknown_artifact'
    meta = store.capture('real evidence', kind='tool', description='test')
    (tmp_path / meta['path']).write_text('changed', encoding='utf-8')
    assert store.read(meta['id'])['code'] == 'artifact_integrity_error'


def test_reopen_checks_registered_metadata_and_count_cap(tmp_path):
    store = material_store(tmp_path)
    store.count_limit = 1
    first = store.capture('first', kind='tool', description='one')
    assert store.capture('second', kind='tool', description='two')['code'] == 'material_capacity'
    reopened = material_store(tmp_path)
    assert reopened.read(first['id'])['text'] == 'first'
    entry = next((tmp_path/'materials').glob('*.json'))
    data = json.loads(entry.read_text()); data['path'] = '../outside'
    entry.write_text(json.dumps(data), encoding='utf-8')
    assert material_store(tmp_path).read(first['id'])['code'] == 'unknown_artifact'


@pytest.mark.parametrize('offset,limit', [(0,1), (1,0), (1,201), (True,1), (1,False)])
def test_bad_read_bounds(tmp_path, offset, limit):
    store = material_store(tmp_path)
    meta = store.capture('abc', kind='tool', description='test')
    assert store.read(meta['id'], offset, limit)['code'] == 'invalid_artifact_range'


def test_tiny_response_and_long_line_do_not_loop(tmp_path):
    store = material_store(tmp_path)
    meta = store.capture('中'*1000+'\nend', kind='tool', description='long line')
    result = store.read(meta['id'], max_bytes=512)
    assert len(json.dumps(result, ensure_ascii=False, separators=(',',':')).encode()) <= 512
    assert result['excerpt_truncated'] is True
    assert result['next_offset'] == 2
    assert store.read(meta['id'], offset=2)['text'] == 'end'


def test_failed_write_returns_no_readable_id(tmp_path, monkeypatch):
    store = material_store(tmp_path)
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr('reproagent.materials.atomic_write', fail)
    data = store.capture('abc', kind='tool', description='test')
    assert data['code'] == 'material_store_error' and 'id' not in data
    assert store.catalog() == []
