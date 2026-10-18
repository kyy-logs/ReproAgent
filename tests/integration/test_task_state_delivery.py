import asyncio
import json
from dataclasses import replace
import pytest

from reproagent.core.models import BudgetLimits
from test_agentscope_runtime import (environment, agent_context, tool_reply, text_reply, explore)


def state_of(request):
    found=[]
    for message in request['messages']:
        try: data=json.loads(message.get('content',''))
        except (ValueError,TypeError): continue
        if isinstance(data,dict) and 'task_state' in data:
            assert message['role']=='user', 'external state text must not become a system rule'
            found.append(data['task_state'])
    assert len(found)==1
    return found[0]


def test_latest_refusal_and_budget_reach_next_real_http_without_history_growth(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts,inject_task_state=True,answers=[
        tool_reply('Read',{'file_path':'../outside'},call_id='bad-read'),
        tool_reply('read_artifact',{'artifact_id':'unknown'},call_id='bad-artifact'),
        tool_reply('request_information',{'question':'need evidence'},call_id='finish')])
    assert explore(env).kind=='request_information'
    assert len(env.requests)==3 and env.context.budget.steps_used==3
    states=[state_of(req) for req in env.requests]
    assert [s['budgets']['remaining_steps'] for s in states]==[20,19,18]
    assert all(s['contract']['version']==1 for s in states)
    assert states[1]['blockers'], 'the actual failed tool must become next-round progress'
    assert not any(getattr(m,'name','')=='task-state' for m in env.runtime.messages)
    for req in env.requests[1:]:
        assert any(m.get('role')=='tool' for m in req['messages'])


def test_offloaded_body_can_be_read_and_delivered_in_the_next_model_call(tmp_path,projects,facts,monkeypatch):
    import test_agentscope_runtime as helper
    monkeypatch.setattr(helper,'MODULE_TEXT',''.join(f'line_{i} = {i}\n' for i in range(1,1501)))
    env=None
    def handler(request):
        payload=json.loads(request.content)
        env.requests.append(payload)
        if len(env.requests)==1:
            return tool_reply('Read',{'file_path':str(env.module)},call_id='large-read')
        if len(env.requests)==2:
            state=state_of(payload)
            assert state['material_count'] >= 1
            identifier=env.store.materials.catalog()[0]['id']
            return tool_reply('read_artifact',{'artifact_id':identifier,'offset':1000,'limit':2},call_id='read-detail')
        assert 'line_1000' in json.dumps(payload)
        return tool_reply('request_information',{'question':'ready'},call_id='finish')
    env=environment(tmp_path,projects,facts,inject_task_state=True,limits=BudgetLimits(tool_response_bytes=2048),handler=handler)
    assert explore(env).kind=='request_information'
    assert len(env.requests)==3
    assert all(len(json.dumps(state_of(req),ensure_ascii=False,separators=(',',':')).encode())<=4096 for req in env.requests)


def test_current_contract_replaces_current_state_across_phases(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts,inject_task_state=True,answers=[text_reply(),text_reply()])
    assert explore(env).kind=='no_candidate'
    changed=replace(env.service.contract,version=2)
    phase=replace(agent_context(env),contract=changed)
    asyncio.run(env.runtime.explore(phase))
    current=state_of(env.requests[-1])
    assert current['contract']['version']==2
    assert current['completion']['evidence_level']=='NONE'
    assert current['facts']==[]


def test_material_reader_respects_reserved_publish_steps_on_real_wire(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts,inject_task_state=True,limits=BudgetLimits(agent_steps=3),answers=[
        tool_reply('read_artifact',{'artifact_id':'catalog'},call_id='reserved-read'),
        tool_reply('request_information',{'question':'need remaining material'},call_id='finish')])
    assert explore(env).kind=='request_information'
    assert len(env.requests)==2
    tools=[m for m in env.requests[1]['messages'] if m.get('role')=='tool']
    assert len(tools)==1 and 'read_artifact is refused' in tools[0]['content']
    assert state_of(env.requests[1])['blockers'][0]['code']=='RESERVED_FOR_PUBLISHING'


def test_default_requests_skip_state_but_local_progress_keeps_updating(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, answers=[
        tool_reply('Read', {'file_path': '../outside'}, call_id='bad-read'),
        tool_reply('request_information', {'question': 'need evidence'}, call_id='finish')])
    assert explore(env).kind == 'request_information'
    assert len(env.requests) == 2
    for request in env.requests:
        for message in request['messages']:
            try: data = json.loads(message.get('content', ''))
            except (ValueError, TypeError): continue
            assert not (isinstance(data, dict) and 'task_state' in data)
    state = json.loads((env.task/'STATE.json').read_text())
    assert state['contract']['version'] == 1
    assert state['budgets']['remaining_steps'] == 19
    assert env.store.materials is not None
    events = env.store.read_events()[0]
    assert any(e.kind == 'exploration.tool_result' and e.payload.get('result_code') == 'TOOL_ERROR' for e in events)
    assert not any(e.kind == 'context.prepared' for e in events)


@pytest.mark.parametrize('enabled', [False, True])
def test_product_config_controls_actual_http_state_and_records_the_policy(tmp_path, projects, facts, enabled):
    import httpx
    from reproagent.app import create_controller
    from reproagent.cli import load_request
    from reproagent.core.models import ModelConfig, ModelRequest, TaskState
    from reproagent.core.serialization import canonical_bytes, encode_record
    from tests.unit.test_controller import ScriptedModel, setup

    request, _, _, context = setup(tmp_path, projects, facts)
    config = encode_record(request)
    if enabled:
        config['inject_task_state'] = True
    else:
        config.pop('inject_task_state', None)  # Existing task configs need no migration.
    config_path = tmp_path/'task-config.json'
    config_path.write_bytes(canonical_bytes(config))
    request = load_request(config_path)
    wires, script = [], ScriptedModel()

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            if len(wires) == 1:
                return tool_reply('Read', {'file_path': '../outside'}, call_id='bad-read')
            return tool_reply('request_information', {'question': 'need evidence'}, call_id='finish')
        reply = await script.complete(ModelRequest(tuple(payload['messages']), 'contract'), context)
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 1,
            'model': 'offline', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': reply.text}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert len(wires) == 2 and context.budget.steps_used == 2
    if enabled:
        assert [state_of(wire)['budgets']['remaining_steps'] for wire in wires] == [20, 19]
        assert state_of(wires[1])['blockers'][0]['code'] == 'TOOL_ERROR'
    else:
        assert not any('task_state' in message.get('content', '')
                       for wire in wires for message in wire['messages']
                       if isinstance(message.get('content'), str))
    saved_request = controller.store.load_record('request', 'request')
    assert saved_request.inject_task_state is enabled
    local = json.loads((request.output_dir/'STATE.json').read_text())
    assert local['status'] == 'NEEDS_INFORMATION'
    assert local['budgets']['remaining_steps'] == 19
    assert local['completion']['final_accepted'] is False
    assert local['blockers'][0]['code'] == 'TOOL_ERROR'
