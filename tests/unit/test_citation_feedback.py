import copy
import json

import pytest

from reproagent.adapters.agentscope.citation_feedback import render_citation_feedback


def errors():
    ref = {'path': 'example/中文模块.py', 'content_hash': '0' * 64, 'start_line': 1, 'end_line': 20}
    return [{'index': i, 'code': 'SOURCE_RANGE_NOT_DISPLAYED',
             'submitted': {**ref, 'start_line': 3, 'end_line': 5},
             'available_refs': [ref, {**ref, 'start_line': 2}]} for i in range(4)]


@pytest.mark.parametrize('limit', [32768, 1024, 256, 64, 2, 1, 0])
def test_feedback_fits_utf8_budget(limit):
    data = errors(); before = copy.deepcopy(data)
    result = render_citation_feedback(code=data[0]['code'], errors=data, byte_limit=limit)
    assert len(result.encode('utf-8')) <= limit
    assert data == before
    if result.startswith('{'):
        payload = json.loads(result)
        assert payload['error_count'] == 4
        assert payload['omitted_errors'] == 4 - len(payload['errors'])
        assert [e['index'] for e in payload['errors']] == list(range(len(payload['errors'])))
        if limit == 32768:
            assert payload['errors'] == data
            assert not payload['available_refs_truncated']
        else:
            assert payload['available_refs_truncated']
    else:
        assert result == 'Citation refused.'[:limit]


def test_feedback_sheds_suggestions_before_error_entries():
    data = errors()
    without_refs = [{**e, 'available_refs': []} for e in data]
    minimal = render_citation_feedback(code=data[0]['code'], errors=without_refs,
                                      byte_limit=32768, available_refs_truncated=True)
    result = json.loads(render_citation_feedback(code=data[0]['code'], errors=data,
                                                byte_limit=len(minimal.encode('utf-8'))))
    assert len(result['errors']) == len(data)
    assert result['omitted_errors'] == 0 and result['available_refs_truncated']
    assert all(not e['available_refs'] for e in result['errors'])


def test_feedback_order_is_stable():
    data = errors()
    for limit in (32768, 1400, 700, 200):
        first = render_citation_feedback(code=data[0]['code'], errors=data, byte_limit=limit)
        assert first == render_citation_feedback(code=data[0]['code'], errors=data, byte_limit=limit)
    full = json.loads(render_citation_feedback(code='SOURCE_HASH_MISMATCH', errors=data,
                                             byte_limit=32768, available_refs_truncated=True))
    assert full['code'] == 'SOURCE_HASH_MISMATCH'
    assert full['available_refs_truncated']


@pytest.mark.parametrize('limit', [-1, True, 1.5])
def test_feedback_rejects_invalid_budget(limit):
    with pytest.raises(ValueError):
        render_citation_feedback(code='SOURCE_HASH_MISMATCH', errors=errors(), byte_limit=limit)
