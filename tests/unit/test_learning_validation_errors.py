"""Learning rejects bad proposals with actionable, bounded diagnostics."""
import pytest

from reproagent.core.models import EvidenceRef
from reproagent.core.serialization import bytes_hash
from reproagent.experience import LearningInput, validate_experience
from reproagent.store import TaskStore, atomic_write


@pytest.fixture
def capsule(tmp_path):
    store = TaskStore(tmp_path)
    content = b'{"id":"E1","kind":"measurement","steps":20}\n'
    atomic_write(store.root / 'learning/evidence.jsonl', content)
    material = LearningInput({}, {'E1': EvidenceRef('learning/evidence.jsonl', bytes_hash(content), 1, 1)}, 'task@scope', 0)
    return material, store


def proposal():
    return {'experience': {'category': 'workflow', 'tags': ['pytest'], 'name':'Bounded evidence', 'description':'When evidence is incomplete or exceeds bounds.',
        'summary': 'Use bounded source reads.', 'detail': 'Observed twenty steps before exhaustion; this does not prove cause.',
        'evidence_ids': ['E1']}}


@pytest.mark.parametrize('mutation,code,missing', [
    ('missing_evidence', 'invalid_missing_fields', ('evidence_ids',)),
    ('missing_summary', 'invalid_missing_fields', ('summary',)),
    ('extra', 'invalid_extra_fields', ()),
    ('root_extra', 'invalid_structure', ()),
    ('not_object', 'invalid_structure', ()),
    ('unknown_id', 'invalid_evidence_ids', ()),
    ('duplicate_id', 'invalid_evidence_ids', ()),
    ('category', 'invalid_fields', ()),
    ('tags', 'invalid_fields', ()),
    ('oversize', 'invalid_card_size', ()),
    ('changed_source', 'invalid_evidence_changed', ()),
])
def test_invalid_proposal_reports_specific_reason_without_echoing_provider_data(capsule, mutation, code, missing):
    material, store = capsule
    response = proposal()
    secret = 'synthetic-private-extra-field'
    if mutation == 'missing_evidence': del response['experience']['evidence_ids']
    if mutation == 'missing_summary': del response['experience']['summary']
    if mutation == 'extra': response['experience'][secret] = secret
    if mutation == 'root_extra': response[secret] = secret
    if mutation == 'not_object': response['experience'] = []
    if mutation == 'unknown_id': response['experience']['evidence_ids'] = ['E999']
    if mutation == 'duplicate_id': response['experience']['evidence_ids'] = ['E1', 'E1']
    if mutation == 'category': response['experience']['category'] = secret
    if mutation == 'tags': response['experience']['tags'] = [secret, {}]
    if mutation == 'oversize': response['experience']['detail'] = secret * 400
    if mutation == 'changed_source': atomic_write(store.root/'learning/evidence.jsonl', b'changed')
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert getattr(raised.value, 'code', None) == code
    assert getattr(raised.value, 'missing_fields', None) == missing
    assert secret not in str(raised.value)


def test_successful_learning_requires_explicit_ids_and_null_needs_no_ids(capsule):
    material, store = capsule
    assert validate_experience({'experience': None}, material, store) is None
    card = validate_experience(proposal(), material, store)
    assert card.evidence_refs == (material.evidence_map['E1'],)


@pytest.mark.parametrize(('field', 'length', 'valid'), [
    ('name', 64, True),
    ('name', 65, False),
    ('description', 160, True),
    ('description', 161, False),
    ('description', 165, False),
])
def test_catalog_field_lengths_are_exact_python_character_counts(capsule, field, length, valid):
    material, store = capsule
    response = proposal()
    response['experience'][field] = 'x' * length
    if valid:
        assert validate_experience(response, material, store) is not None
        return
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert raised.value.code == 'invalid_field_length'
    assert raised.value.field_errors == (
        {'field': field, 'actual_length': length, 'limit': 64 if field == 'name' else 160},)


def test_chinese_text_is_measured_in_characters_not_utf8_bytes(capsule):
    material, store = capsule
    response = proposal()
    response['experience']['description'] = '汉' * 160
    assert validate_experience(response, material, store) is not None
    response['experience']['description'] = '汉' * 161
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert raised.value.code == 'invalid_field_length'
    assert raised.value.field_errors[0]['actual_length'] == 161


def test_both_overlong_catalog_fields_are_reported_once_each(capsule):
    material, store = capsule
    response = proposal()
    response['experience']['name'] = 'n' * 70
    response['experience']['description'] = 'd' * 170
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert raised.value.code == 'invalid_field_length'
    assert raised.value.field_errors == (
        {'field': 'name', 'actual_length': 70, 'limit': 64},
        {'field': 'description', 'actual_length': 170, 'limit': 160},
    )


def test_redaction_expansion_is_measured_before_the_length_decision(capsule):
    material, store = capsule
    response = proposal()
    secret = 'top-secret'
    response['experience']['description'] = 'x' * 155 + secret
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store, secrets=(secret,))
    assert raised.value.code == 'invalid_field_length'
    assert raised.value.field_errors == (
        {'field': 'description', 'actual_length': 165, 'limit': 160},)
    assert secret not in str(raised.value)


@pytest.mark.parametrize('field,value', [
    ('name', 123), ('name', '   '), ('description', None), ('description', '')])
def test_type_and_blank_errors_stay_invalid_fields(capsule, field, value):
    material, store = capsule
    response = proposal()
    response['experience'][field] = value
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert raised.value.code == 'invalid_fields'
    assert getattr(raised.value, 'field_errors', ()) == ()


@pytest.mark.parametrize('field', ['category', 'tags', 'summary', 'detail'])
def test_unpaired_unicode_is_invalid_model_fields_not_internal_error(capsule, field):
    material, store = capsule
    response = proposal()
    response['experience'][field] = ['\ud800'] if field == 'tags' else '\ud800'
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert getattr(raised.value, 'code', None) == 'invalid_fields'
