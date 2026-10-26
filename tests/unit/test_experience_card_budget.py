from dataclasses import replace

import pytest

from reproagent import experience as exp
from reproagent.core.serialization import canonical_bytes
from reproagent.experience_catalog import ExperienceCatalogView
from tests.unit.test_experience_store import card
from tests.unit.test_learning_validation_errors import capsule, proposal


def sized_card(size):
    base = replace(card(), description='定位入口困难时使用。', detail='事实：读取范围被拒。')
    overhead = len(canonical_bytes(exp._card_record(base)))
    return replace(base, detail=base.detail + 'x' * (size - overhead))


@pytest.mark.parametrize('directory', [False, True])
def test_measured_2308_byte_card_writes_and_is_readable(tmp_path, directory):
    sample = sized_card(2308)
    assert len(canonical_bytes(exp._card_record(sample))) == 2308
    path = tmp_path / ('memory' if directory else 'memory.json')
    assert exp.append_experience(path, sample, None) == 'written'
    view = ExperienceCatalogView(exp.load_experience_snapshot(path))
    assert view.list('model')['code'] == 'ready'
    loaded = view.read(sample.id)
    assert loaded['detail'] == sample.detail
    assert len(canonical_bytes(loaded)) > 2048
    assert len(canonical_bytes(loaded)) <= 4096


def test_full_card_4096_boundary_remains_hard(tmp_path):
    assert exp.append_experience(tmp_path / 'accepted.json', sized_card(4096), None) == 'written'
    assert exp.append_experience(tmp_path / 'rejected.json', sized_card(4097), None) == 'invalid'
    assert not (tmp_path / 'rejected.json').exists()


def test_learning_size_error_has_exact_safe_numbers(capsule):
    material, store = capsule
    response = proposal()
    response['experience']['detail'] = '敏感原文不可回显' * 700
    expected = {
        'id': exp._identifier('workflow', response['experience']['tags'], response['experience']['summary']),
        **{k: v for k, v in response['experience'].items() if k not in ('evidence_ids', 'name')},
        'source_task_id': material.source_task_id,
        'evidence_refs': [{'path':ref.path,'content_hash':ref.content_hash,'start_line':ref.start_line,'end_line':ref.end_line}
                          for ref in material.evidence_map.values()]}
    needed = len(canonical_bytes(expected))
    with pytest.raises(exp.LearningValidationError) as caught:
        exp.validate_experience(response, material, store)
    error = caught.value
    assert error.code == 'invalid_card_size'
    assert error.needed_bytes == needed
    assert error.limit_bytes == 4096
    assert error.exceeded_bytes == needed - 4096
    assert '敏感原文' not in str(error)


def test_learning_size_is_reported_after_controlled_metadata(tmp_path):
    from tests.unit.test_experience_triggers import learning_fixture
    store, material, response = learning_fixture(tmp_path)
    response['experience']['detail'] = 'x' * 5000
    with pytest.raises(exp.LearningValidationError) as failure:
        exp.validate_experience(response, material, store)
    # A temporarily high limit reconstructs the validated final record; the real
    # validator must count normative tags and triggers too.
    from unittest.mock import patch
    with patch.object(exp, 'CARD_BYTES', 20000):
        completed = exp.validate_experience(response, material, store)
    assert failure.value.needed_bytes == len(canonical_bytes(exp._card_record(completed)))
