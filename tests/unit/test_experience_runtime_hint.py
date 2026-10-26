from types import SimpleNamespace
from dataclasses import replace

import pytest

from reproagent.adapters.agentscope.experience_hint import ExperienceHintState, HINT_TEXT
from reproagent.experience import ExperienceSnapshot
from reproagent.experience_catalog import ExperienceCatalogView
from tests.unit.test_experience_store import card


def prepare(state, **overrides):
    values = dict(remaining_before_call=6, reserved_steps=3,
                  allowed_tools=("list_experiences", "read_experience"),
                  view=ExperienceCatalogView(ExperienceSnapshot('ready', 'hash', (card(),))), browse_state="satisfied",
                  deadline_ok=True, cancelled=False, phase_finished=False)
    values.update(overrides)
    return state.prepare(**values)


def test_hint_requires_a_known_blocker_and_six_pre_call_steps():
    state = ExperienceHintState(True)
    assert prepare(state) is None
    state.observe({"kind": "tool_error", "code": "DENIED"})
    assert prepare(state, remaining_before_call=5) is None
    hint = prepare(state)
    assert hint == {"message": HINT_TEXT}
    assert len(HINT_TEXT.encode("utf-8")) <= 256
    assert prepare(state) is None  # prepared only once


def test_hint_is_suppressed_without_history_tools_quota_or_permission():
    for override in [
        {"view": None},
        {"browse_state": "pending"},
        {"allowed_tools": ()},
        {"deadline_ok": False},
        {"cancelled": True},
        {"phase_finished": True},
        {"remaining_before_call": 5},
    ]:
        state = ExperienceHintState(True)
        state.observe({"kind": "permission"})
        assert prepare(state, **override) is None, override


def test_hint_marks_delivery_only_when_a_response_arrived():
    delivered = ExperienceHintState(True)
    delivered.observe({"kind": "tool_error"})
    assert prepare(delivered) is not None
    delivered.finish(response_received=True)
    assert delivered.delivered and not delivered.omitted
    assert delivered.reason == "delivered"

    omitted = ExperienceHintState(True)
    omitted.observe({"kind": "tool_error"})
    assert prepare(omitted) is not None
    omitted.finish(response_received=False)
    assert omitted.omitted and not omitted.delivered
    assert omitted.reason == "not_delivered"
    assert prepare(omitted) is None


def test_disabled_hint_never_prepares():
    state = ExperienceHintState(False)
    state.observe({"kind": "tool_error"})
    assert prepare(state) is None


@pytest.mark.parametrize('exhausted', ['catalog_calls', 'catalog_bytes', 'details'])
def test_exhausted_view_never_invites_an_impossible_read(exhausted):
    view = ExperienceCatalogView(ExperienceSnapshot('ready', 'hash', (card(),)))
    if exhausted == 'catalog_calls':
        view.catalog_calls = 12
    elif exhausted == 'catalog_bytes':
        view.catalog_bytes = 16384
    else:
        page = view.list()
        identifier = page['items'][0]['id']
        view.read(identifier)
        view.read(identifier)
    before = (view.catalog_calls, view.catalog_bytes, view.read_ids)
    state = ExperienceHintState(True)
    state.observe({'kind': 'tool_error'})
    assert prepare(state, view=view) is None
    assert (view.catalog_calls, view.catalog_bytes, view.read_ids) == before


def test_hint_cannot_skip_an_undeliverable_first_catalog_entry():
    a, b = sorted((card('first'), card('second')), key=lambda c: c.id)
    first = replace(a, description='述' * 160)
    second = replace(b, description='short')
    view = ExperienceCatalogView(ExperienceSnapshot('ready', 'hash', (first, second)))
    state = ExperienceHintState(True)
    state.observe({'kind': 'tool_error'})
    assert prepare(state, view=view, max_bytes=400) is None
    assert view.catalog_calls == 0 and view.catalog_bytes == 0 and view.read_ids == ()
    assert view.list('model', max_bytes=400)['code'] == 'response_capacity'
    assert view.read(second.id)['code'] == 'not_displayed'
