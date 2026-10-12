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


@pytest.mark.parametrize('field', ['category', 'tags', 'summary', 'detail'])
def test_unpaired_unicode_is_invalid_model_fields_not_internal_error(capsule, field):
    material, store = capsule
    response = proposal()
    response['experience'][field] = ['\ud800'] if field == 'tags' else '\ud800'
    with pytest.raises(ValueError) as raised:
        validate_experience(response, material, store)
    assert getattr(raised.value, 'code', None) == 'invalid_fields'
