import pytest

from reproagent.adapters.agentscope.experience_browse import ExperienceBrowseGate


def test_first_browse_is_task_scoped_and_refusals_are_bounded():
    gate = ExperienceBrowseGate(True)
    assert gate.state == 'pending'
    assert gate.reject() is True
    assert gate.state == 'pending'
    assert gate.reject() is True
    assert (gate.state, gate.reason, gate.refusals) == ('bypassed', 'noncompliant', 2)
    gate.satisfy()
    assert gate.state == 'bypassed'


def test_no_library_does_not_block_and_terminal_states_do_not_change():
    gate = ExperienceBrowseGate(False)
    assert gate.state == 'bypassed'
    gate.satisfy()
    assert gate.state == 'bypassed'
    gate = ExperienceBrowseGate(True)
    gate.satisfy()
    gate.bypass('response_capacity')
    assert gate.state == 'satisfied'


def test_notice_has_all_category_counts_from_frozen_snapshot():
    from reproagent.experience import ExperienceSnapshot
    from reproagent.experience_catalog import ExperienceCatalogView
    from tests.unit.test_experience_store import card
    view = ExperienceCatalogView(ExperienceSnapshot('ready', 'a' * 64, (card(),)))
    assert dict(view.notice().category_counts) == {'framework': 0, 'model': 1, 'workflow': 0}
