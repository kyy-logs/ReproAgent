"""The input inventory describes the actual HTTP body, even when its copy is cut."""
import asyncio
import json
from dataclasses import replace

import httpx

from reproagent.observability import TraceRecorder
from reproagent.core.models import BudgetLimits
from tests.integration.test_agentscope_observability import (
    agent_context, environment, read_reply, text_reply, tool_reply,
)


def manifests(doc):
    return [json.loads(c['text']) for c in doc['contents']
            if c['source'] == 'context_manifest' and not c['truncated']]


def run(env, *contexts):
    async def scenario():
        try:
            for context in contexts:
                await env.runtime.explore(context)
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    asyncio.run(scenario())


def test_inventory_is_bound_to_wire_and_keeps_source_ranges(tmp_path, projects, facts):
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[text_reply(), text_reply()])
        env.answers[0] = read_reply(env.module, 'source-read')
        run(env, agent_context(env, feedback='latest refusal'))
        doc = recorder.finish(task_id='inventory', status='EXHAUSTED', main_duration=1)

    inventories = manifests(doc)
    assert len(inventories) == 2, 'each HTTP request needs a material inventory'
    assert inventories[0]['basis'] == 'http_request_body'
    assert inventories[0]['message_count'] == len(env.requests[0]['messages'])
    assert {'contract', 'feedback', 'budget', 'system_prompt'} <= {
        i['kind'] for i in inventories[0]['items']}
    source = next(i for i in inventories[1]['items'] if i['kind'] == 'source_result')
    assert source['tool_call_id'] == 'source-read'
    assert source['source_refs'][0]['path'] == 'example/parser.py'
    assert source['source_refs'][0]['start_line'] == 1
    assert source['source_refs'][0]['end_line'] == 2
    message = env.requests[1]['messages'][source['message_index']]
    assert message['role'] == 'tool' and message['tool_call_id'] == 'source-read'
    assert inventories[1]['tool_definition_count'] == len(env.requests[1]['tools'])


def test_retry_attempts_share_inventory_without_counting_an_extra_model_call(tmp_path, projects, facts):
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[
            httpx.Response(503, json={'error': {'message': 'busy'}}), text_reply()])
        run(env, agent_context(env))
        doc = recorder.finish(task_id='retry-inventory', status='EXHAUSTED', main_duration=1)
    inventories = manifests(doc)
    assert len(inventories) == 1
    attempts = [s for s in doc['spans'] if s['name'] == 'model.http_attempt']
    inventory_ids = {c['content_id'] for c in doc['contents'] if c['source'] == 'context_manifest'}
    assert len(attempts) == 2
    assert all(inventory_ids.intersection(s['content_refs']) for s in attempts)
    assert doc['summary']['logical_calls'] == 1
    assert env.context.budget.steps_used == 1


def test_prior_contract_is_labeled_historical_and_not_current(tmp_path, projects, facts):
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[
            tool_reply('request_information', {'question': 'missing input'}, call_id='first'),
            text_reply()])
        first = agent_context(env)
        second = replace(first, contract=replace(first.contract, version=2))
        run(env, first, second)
        doc = recorder.finish(task_id='versions', status='EXHAUSTED', main_duration=1)
    latest = manifests(doc)[-1]
    contracts = [i for i in latest['items'] if i['kind'] == 'contract']
    assert [(i['version'], i['scope']) for i in contracts] == [(1, 'historical'), (2, 'current')]


def test_inventory_survives_truncated_request_capture(tmp_path, projects, facts):
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[text_reply()])
        # SDK history is independently retained; a long history can exceed the per-capture cap.
        from agentscope.message import UserMsg
        env.runtime._agent.state.context.append(UserMsg(name='prior-user', content='middle-only-' + 'x' * 40000))
        run(env, agent_context(env))
        doc = recorder.finish(task_id='large-input', status='EXHAUSTED', main_duration=1)
    wire = next(c for c in doc['contents'] if c['source'] == 'wire_request')
    assert wire['truncated'] is True
    inventory = manifests(doc)[0]
    assert inventory['message_count'] == len(env.requests[0]['messages'])
    unknown = next(i for i in inventory['items'] if i['kind'] == 'unknown')
    assert unknown['bytes'] > 40000
    assert 'middle-only-' not in json.dumps(inventory)


def test_catalog_is_not_confused_with_loaded_experience_body(tmp_path, projects, facts):
    from tests.integration.test_agentscope_experience_catalog import catalog_environment
    from tests.unit.test_experience_store import card
    with TraceRecorder() as recorder:
        env, _ = catalog_environment(tmp_path, projects, facts, [
            tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
            tool_reply('read_experience', {'id': card().id}, call_id='detail'),
            tool_reply('request_information', {'question': 'Need source'}, call_id='stop')])
        run(env, agent_context(env))
        doc = recorder.finish(task_id='experience-inventory', status='EXHAUSTED', main_duration=1)
    inventories = manifests(doc)
    assert not any(i['kind'] == 'experience_body' for i in inventories[0]['items'])
    catalog = next(i for i in inventories[1]['items'] if i['kind'] == 'experience_catalog')
    assert card().id in catalog['experience_ids']
    assert not any(i['kind'] == 'experience_body' for i in inventories[1]['items'])
    body = next(i for i in inventories[2]['items'] if i['kind'] == 'experience_body')
    assert body['experience_id'] == card().id and body['tool_call_id'] == 'detail'
    assert 'UNIQUE_LOADED_DETAIL' not in json.dumps(inventories)
    assert 'UNIQUE_LOADED_DETAIL' in json.dumps(env.requests[2])


def test_assembly_history_removal_is_distinct_from_capture_omission(tmp_path, projects, facts):
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[text_reply()],
                          limits=BudgetLimits(tool_response_bytes=4096))
        context = replace(agent_context(env), history=({'detail': 'x' * 8000}, {'detail': 'y' * 8000}))
        run(env, context)
        doc = recorder.finish(task_id='omitted-history', status='EXHAUSTED', main_duration=1)
    inventory = manifests(doc)[0]
    assert inventory['omissions'] == [{'kind': 'phase_history', 'message_index': 1,
                                      'scope': 'current', 'reason': 'phase_input_capacity', 'entry_count': 2}]
    assert inventory['inventory_complete'] is True
    history = next(i for i in inventory['items'] if i['kind'] == 'phase_history')
    assert history['entry_count'] == 0
    assert json.loads(env.requests[0]['messages'][1]['content'])['history'] == []


def test_inventory_failure_does_not_change_execution_or_drop_wire_copy(tmp_path, projects, facts, monkeypatch):
    import reproagent.context_manifest as implementation
    def broken(*args, **kwargs):
        raise RuntimeError('inventory unavailable')
    monkeypatch.setattr(implementation, 'build_manifest', broken)
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[text_reply()])
        run(env, agent_context(env))
        doc = recorder.finish(task_id='faulty-inventory', status='EXHAUSTED', main_duration=1)
    assert len(env.requests) == 1
    assert doc['summary']['logical_calls'] == 1
    assert any(c['source'] == 'wire_request' and c['availability'] == 'captured' for c in doc['contents'])
    assert any(c['source'] == 'context_manifest' and c['availability'] == 'capture_error' for c in doc['contents'])
    attempt = next(s for s in doc['spans'] if s['name'] == 'model.http_attempt')
    assert attempt['attributes']['context_manifest_state'] == 'capture_error'


def test_exhausted_capture_budget_marks_inventory_omitted_not_model_input_missing(tmp_path, projects, facts, monkeypatch):
    import reproagent.observability_content as content_store
    monkeypatch.setattr(content_store, 'MAX_CONTENT_ITEMS', 0)
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=[text_reply()])
        run(env, agent_context(env))
        doc = recorder.finish(task_id='full-recorder', status='EXHAUSTED', main_duration=1)
    assert len(env.requests) == 1 and len(env.requests[0]['messages']) >= 2
    assert doc['contents'] == []
    assert doc['content_complete'] is False
    attempt = next(s for s in doc['spans'] if s['name'] == 'model.http_attempt')
    assert attempt['attributes']['context_manifest_state'] == 'omitted_limit'
