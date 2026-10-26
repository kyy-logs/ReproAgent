from dataclasses import asdict, replace

import pytest

from reproagent import experience as exp
from reproagent.core.protocol import learning_schema
from tests.unit.test_experience_store import card
from tests.unit.test_experience_learning import material_setup, proposed


def test_catalog_schema_requires_description_without_name():
    schema = str(learning_schema())
    assert 'description' in schema
    assert "'name'" not in schema
    assert exp.PROPOSAL_FIELDS == {'category', 'tags', 'description', 'summary', 'detail', 'evidence_ids'}


def test_old_proposal_rejected_with_precise_missing_fields(tmp_path, projects, facts):
    material, store, *_ = material_setup(tmp_path, projects, facts)
    response = proposed([next(iter(material.evidence_map))])
    response['experience'].pop('description', None)
    with pytest.raises(exp.LearningValidationError) as failure:
        exp.validate_experience(response, material, store)
    assert failure.value.code == 'invalid_missing_fields'
    assert failure.value.missing_fields == ('description',)


@pytest.mark.parametrize('field,value', [('description', 'x' * 161), ('description', 7)])
def test_invalid_catalog_metadata_rejected(field, value):
    data = exp._card_record(card())
    data.update(description='When the entry is unclear')
    data[field] = value
    with pytest.raises(ValueError):
        exp._card(data)


def test_catalog_projection_has_no_name_and_keeps_description():
    from reproagent.experience_catalog import entry_from_card
    legacy = card('x' * 200)
    before = exp._card_record(legacy)
    entry = entry_from_card(legacy)
    assert set(asdict(entry)) == {'id', 'description'}
    assert len(entry.description) == 160
    assert entry.description.endswith('…')
    assert before == exp._card_record(legacy)
    assert entry.id == legacy.id


def test_product_prompt_only_advertises_registered_experience_tools(tmp_path, projects, facts):
    from reproagent.core.agent import exploration_prompt
    from tests.unit.test_agent import phase_context
    phase = phase_context(facts, tmp_path, projects)
    assert 'list_experiences' not in exploration_prompt(phase)
    assert 'list_experiences' in exploration_prompt(phase, experience_enabled=True)


def test_the_prompt_states_how_much_history_exists_and_never_its_text(tmp_path, projects, facts):
    """The count is instruction the phase acts on; the cards stay behind the two tools.

    The line used to live in the phase's data message, where nothing acted on it: six real
    runs across two cases read the library and browsed it zero times.
    """
    from dataclasses import replace
    from reproagent.core.agent import exploration_prompt
    from reproagent.core.models import ExperienceCatalogNotice
    from tests.unit.test_agent import phase_context
    phase = phase_context(facts, tmp_path, projects)

    def built(notice):
        return exploration_prompt(replace(phase, experience_catalog=notice), experience_enabled=True)

    assert 'This round has no historical cards.' in built(None)
    assert 'This round has 1 historical card (model).' in built(ExperienceCatalogNotice(1, ('model',)))
    assert 'This round has 3 historical cards across framework and workflow.' in built(
        ExperienceCatalogNotice(3, ('framework', 'workflow')))
    assert '{experience_catalog}' not in built(ExperienceCatalogNotice(2, ('model', 'workflow')))
