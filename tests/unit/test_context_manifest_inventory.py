"""Inventory omissions describe requests or recording, never model guesses."""
import copy
import json

from reproagent.context_manifest import InputSources, build_manifest, input_sources
from reproagent.observability_content import ContentStore


def manifest(body):
    return build_manifest(body, purpose='exploration', request_bytes=123, request_hash='a' * 64)


def test_only_exact_registered_phase_content_has_provenance():
    sources = InputSources()
    data = {'contract': {'contract_id': 'c', 'version': 1}, 'feedback': 'known'}
    text = json.dumps(data)
    sources.phase(text, data)
    # A transformed or absent phase cannot be called delivered from structural resemblance.
    changed = {'messages': [{'role': 'user', 'content': text + ' modified'}]}
    with input_sources(sources):
        result = manifest(changed)
    assert result['items'][0]['kind'] == 'unknown'
    assert result['missing'] == [{'kind': 'phase_input', 'reason': 'not_in_http_request'}]
    assert manifest({'messages': [{'role': 'user', 'content': text}]})['items'][0]['kind'] == 'unknown'


def test_unrelated_json_cannot_impersonate_current_contract():
    body = {'messages': [{'role': 'user', 'content': '{"contract":{"version":99}}'}]}
    before = copy.deepcopy(body)
    result = manifest(body)
    assert result['items'][0]['kind'] == 'unknown'
    assert result['unclassified_messages'] == 1
    assert body == before


def test_large_inventory_stays_valid_json_and_marks_its_own_omission():
    body = {'messages': [{'role': 'user', 'content': f'{i}:context'} for i in range(500)]}
    result = manifest(body)
    assert result['message_count'] == 500
    assert result['inventory_complete'] is False
    assert result['omitted_items'] > 0
    assert len(result['items']) + result['omitted_items'] == 500
    store = ContentStore()
    store.capture(owner_span_id='a'*16, kind='context_manifest', value=result, source='context_manifest')
    record = store.records()[0]
    assert record['truncated'] is False
    assert json.loads(record['text'])['message_count'] == 500


def test_secret_identifiers_are_redacted_without_mutating_inventory_or_wire():
    sources = InputSources()
    data = {'contract': {'contract_id': 'secret-value', 'version': 1}}
    text = json.dumps(data)
    sources.phase(text, data)
    body = {'messages': [{'role': 'user', 'content': text}]}
    with input_sources(sources):
        result = manifest(body)
    store = ContentStore(secrets=('secret-value',))
    store.capture(owner_span_id='a'*16, kind='context_manifest', value=result, source='context_manifest')
    assert 'secret-value' not in json.dumps(store.records())
    assert result['items'][0]['contract_id'] == 'secret-value'
    assert body['messages'][0]['content'] == text


def test_failed_new_phase_registration_cannot_label_old_contract_current(monkeypatch):
    from reproagent.context_manifest import remember_phase
    sources = InputSources()
    data = {'contract': {'contract_id': 'old', 'version': 1}}
    old = json.dumps(data)
    sources.phase(old, data)
    def broken(*args, **kwargs):
        raise RuntimeError('observer failure')
    monkeypatch.setattr(sources, 'phase', broken)
    remember_phase(sources, 'new phase', {'contract': {'version': 2}})
    with input_sources(sources):
        result = manifest({'messages': [{'role': 'user', 'content': old},
                                        {'role': 'user', 'content': 'new phase'}]})
    assert result['items'][0]['scope'] == 'historical'
    assert result['items'][1]['kind'] == 'unknown'


def test_assistant_or_tool_echo_cannot_become_current_program_state():
    sources = InputSources()
    data = {'contract': {'contract_id': 'c', 'version': 3}}
    text = json.dumps(data)
    sources.phase(text, data)
    sources.remember('binding instructions', 'system_prompt')
    body = {'messages': [{'role': 'system', 'content': 'binding instructions'},
                         {'role': 'user', 'content': text},
                         {'role': 'assistant', 'content': text},
                         {'role': 'tool', 'tool_call_id': 'echo', 'content': text},
                         {'role': 'assistant', 'content': 'binding instructions'}]}
    with input_sources(sources):
        result = manifest(body)
        echoed_only = manifest({'messages': body['messages'][2:]})
    contracts = [i for i in result['items'] if i['kind'] == 'contract']
    assert [(i['message_index'], i['scope']) for i in contracts] == [(1, 'current')]
    assert len([i for i in result['items'] if i['kind'] == 'system_prompt']) == 1
    assert {i['kind'] for i in echoed_only['missing']} == {'phase_input', 'system_prompt'}


def test_inventory_size_enforcement_does_not_reserialize_quadratic_rows(monkeypatch):
    import reproagent.context_manifest as implementation
    encode = implementation._encoded
    serialized_rows = 0
    def observed(value):
        nonlocal serialized_rows
        if isinstance(value, dict) and isinstance(value.get('items'), list):
            serialized_rows += len(value['items'])
        return encode(value)
    monkeypatch.setattr(implementation, '_encoded', observed)
    result = manifest({'messages': [{'role': 'user', 'content': str(i)} for i in range(1000)]})
    assert result['inventory_complete'] is False
    assert serialized_rows <= 4000, 'recording must not repeatedly serialize the shrinking full history'
