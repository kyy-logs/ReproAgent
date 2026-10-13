import asyncio
import json

from tests.integration.test_agentscope_experience_catalog import catalog_environment
from tests.integration.test_agentscope_runtime import agent_context, tool_reply, write_payload
from tests.unit.test_experience_store import card


def run(env):
    async def execute():
        try:
            return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    return asyncio.run(execute())


def test_sdk_first_browse_reaches_actual_input_before_detail(tmp_path, projects, facts):
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
        tool_reply('read_experience', {'id': card().id}, call_id='detail'),
        tool_reply('write_candidate', write_payload(), call_id='publish')])
    assert run(env).kind == 'candidate'
    assert env.runtime.browse_gate.state == 'satisfied'
    result = next(m for m in env.requests[1]['messages'] if m.get('tool_call_id') == 'catalog')
    assert json.loads(result['content'])['items']
    assert 'UNIQUE_LOADED_DETAIL' not in json.dumps(env.requests[1]['messages'])
    assert 'UNIQUE_LOADED_DETAIL' in json.dumps(env.requests[2]['messages'])
    assert 'Current experience browse state: satisfied' in json.dumps(env.requests[1]['messages'])
    assert view.read_ids == (card().id,)


def test_two_rejected_publications_have_no_side_effect_then_bypass(tmp_path, projects, facts):
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('write_candidate', write_payload(), call_id='first'),
        tool_reply('write_candidate', write_payload(), call_id='second'),
        tool_reply('request_information', {'question': 'No relevant history'}, call_id='stop')])
    assert run(env).kind == 'request_information'
    assert env.runtime.browse_gate.state == 'bypassed'
    assert env.runtime.browse_gate.reason == 'noncompliant'
    assert not list((env.task / 'candidates').glob('*'))
    events, _ = env.store.read_events()
    states = [e.payload['state'] for e in events if e.kind == 'experience.browse_gate']
    # The helper constructs a no-library runtime before replacing it with this one.
    assert states[-2:] == ['pending', 'bypassed']


def test_empty_chosen_category_releases_gate_without_hiding_other_categories(tmp_path, projects, facts):
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'framework'}, call_id='empty'),
        tool_reply('list_experiences', {'category': 'model'}, call_id='other'),
        tool_reply('request_information', {'question': 'Need source'}, call_id='stop')])
    assert run(env).kind == 'request_information'
    assert env.runtime.browse_gate.state == 'satisfied'
    first = next(m for m in env.requests[1]['messages'] if m.get('tool_call_id') == 'empty')
    assert json.loads(first['content'])['code'] == 'empty'
    assert card().id in view._shown


def test_missing_category_is_refused_then_explicit_category_succeeds(tmp_path, projects, facts):
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {}, call_id='implicit'),
        tool_reply('list_experiences', {'category': 'model'}, call_id='explicit'),
        tool_reply('request_information', {'question': 'Need facts'}, call_id='stop')])
    assert run(env).kind == 'request_information'
    assert view.catalog_calls == 1
    assert env.runtime.browse_gate.state == 'satisfied'
    assert env.runtime.browse_gate.refusals == 1


def test_first_catalog_capacity_failure_bypasses_without_fake_delivery(tmp_path, projects, facts):
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
        tool_reply('request_information', {'question': 'Need facts'}, call_id='stop')])
    # Only the catalog gets a small response ceiling; model input remains runnable.
    original = view.list
    view.list = lambda *args, **kwargs: original(*args, **{**kwargs, 'max_bytes': 32})
    assert run(env).kind == 'request_information'
    assert env.runtime.browse_gate.state == 'bypassed'
    assert env.runtime.browse_gate.reason == 'response_capacity'
    assert view.catalog_calls == 0


def test_browse_gate_does_not_reset_on_next_phase(tmp_path, projects, facts):
    from dataclasses import replace
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
        tool_reply('request_information', {'question': 'Need first facts'}, call_id='first'),
        tool_reply('request_information', {'question': 'Need more facts'}, call_id='second')])
    async def execute():
        try:
            assert (await env.runtime.explore(agent_context(env))).kind == 'request_information'
            env.service.bind_contract(replace(env.service.contract, version=2))
            return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(execute()).kind == 'request_information'
    assert env.runtime.browse_gate.state == 'satisfied'
    assert view.catalog_calls == 1


def test_catalog_io_failure_bypasses_but_does_not_claim_delivery(tmp_path, projects, facts):
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
        tool_reply('request_information', {'question': 'Need facts'}, call_id='stop')])
    def unavailable(*args, **kwargs):
        raise OSError('catalog unavailable')
    view.list = unavailable
    assert run(env).kind == 'request_information'
    assert env.runtime.browse_gate.state == 'bypassed'
    assert env.runtime.browse_gate.reason == 'catalog_unavailable'


def test_relevant_card_can_be_loaded_from_later_page(tmp_path, projects, facts, monkeypatch):
    from reproagent import experience_catalog
    monkeypatch.setattr(experience_catalog, 'PAGE_ITEMS', 1)
    env, view = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='first-page'),
        tool_reply('list_experiences', {'category': 'model'}, call_id='placeholder'),
        tool_reply('read_experience', {'id': sorted(view_id for view_id in [card().id, card('other').id])[-1]}, call_id='detail'),
        tool_reply('request_information', {'question': 'Need source'}, call_id='stop')])
    original = view.list
    def page(*args, **kwargs):
        result = original(*args, **kwargs)
        if result['next_cursor']:
            env.answers[0] = tool_reply('list_experiences',
                {'category': 'model', 'cursor': result['next_cursor']}, call_id='second-page')
        return result
    view.list = page
    assert run(env).kind == 'request_information'
    assert view.catalog_calls == 2
    assert view.read_ids == (sorted(view._cards)[-1],)
    assert env.runtime.browse_gate.state == 'satisfied'
