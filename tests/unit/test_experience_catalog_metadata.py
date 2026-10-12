from dataclasses import asdict, replace

import pytest

from reproagent import experience as exp
from reproagent.core.protocol import learning_schema
from tests.unit.test_experience_store import card
from tests.unit.test_experience_learning import material_setup, proposed


def test_catalog_schema_requires_name_and_description():
    schema = str(learning_schema())
    assert 'name' in schema and 'description' in schema
    assert exp.PROPOSAL_FIELDS == {'category', 'tags', 'name', 'description', 'summary', 'detail', 'evidence_ids'}


def test_old_proposal_rejected_with_precise_missing_fields(tmp_path, projects, facts):
    material, store, *_ = material_setup(tmp_path, projects, facts)
    response = proposed([next(iter(material.evidence_map))])
    response['experience'].pop('name', None)
    response['experience'].pop('description', None)
    with pytest.raises(exp.LearningValidationError) as failure:
        exp.validate_experience(response, material, store)
    assert failure.value.code == 'invalid_missing_fields'
    assert failure.value.missing_fields == ('description', 'name')


@pytest.mark.parametrize('field,value', [('name', 'x' * 65), ('description', 'x' * 161), ('name', ' '), ('description', 7)])
def test_invalid_catalog_metadata_rejected(field, value):
    data = exp._card_record(card())
    data.update(name='Find entry', description='When the entry is unclear')
    data[field] = value
    with pytest.raises(ValueError):
        exp._card(data)


def test_legacy_catalog_projection_preserves_storage_and_id():
    from reproagent.experience_catalog import entry_from_card
    legacy = card('x' * 200)
    before = exp._card_record(legacy)
    entry = entry_from_card(legacy)
    assert set(asdict(entry)) == {'id', 'name', 'description'}
    assert len(entry.name) == 64 and len(entry.description) == 160
    assert entry.name.endswith('…') and entry.description.endswith('…')
    assert before == exp._card_record(legacy)
    assert entry.id == legacy.id


def test_product_prompt_only_advertises_registered_experience_tools(tmp_path, projects, facts):
    from reproagent.core.agent import exploration_prompt
    from tests.unit.test_agent import phase_context
    phase = phase_context(facts, tmp_path, projects)
    assert 'list_experiences' not in exploration_prompt(phase)
    assert 'list_experiences' in exploration_prompt(phase, experience_enabled=True)
