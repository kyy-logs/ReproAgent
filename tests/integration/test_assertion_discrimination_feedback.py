"""Offline scripted requests prove delivery, not real-model adoption or benchmark success."""
import asyncio
import json

import httpx

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, TaskState
from tests.integration.test_agentscope_runtime import tool_reply
from tests.unit.test_controller import ScriptedModel, setup


def test_original_pass_guidance_reaches_next_actual_wire_without_semantic_retry(tmp_path, projects, facts):
    request, _, _, context = setup(tmp_path, projects, facts)
    script = ScriptedModel()
    wires, domain_kinds = [], []
    candidate = ('from example.parser import parse\n'
                 'def test_metadata_word():\n'
                 '    parse([1])\n'
                 '    block = "module_scoped_fixture"\n'
                 '    assert "module" in block\n')

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            if len(wires) == 1:
                return tool_reply('write_candidate', {'files': [
                    {'path': 'tests/test_repro.py', 'content': candidate, 'role': 'test'}],
                    'hypothesis': '验证元数据不能代替目标行为'}, call_id='publish')
            return tool_reply('request_information', {'question': '需要更有区分力的候选'}, call_id='stop')
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        domain_kinds.append(kind)
        reply = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 1,
            'model': 'offline', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': reply.text}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert len(wires) == context.budget.steps_used == 2
    assert domain_kinds == ['contract'], 'passing original must not buy a semantic diagnosis call'
    phases = []
    for message in wires[1]['messages']:
        if message['role'] != 'user' or not isinstance(message['content'], str):
            continue
        try:
            data = json.loads(message['content'])
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('feedback'):
            phases.append(data)
    feedback = json.loads(phases[-1]['feedback'])
    assert feedback['verdict']['classification'] == 'NOT_REPRODUCED'
    issue = feedback['verdict']['issues'][0]
    assert issue['code'] == 'CANDIDATE_NOT_REPRODUCED' and issue['origin'] == 'program_check'
    run_id = feedback['verdict']['run_ids'][0]
    saved = controller.store.load_record('verdicts', 'verdict-' + run_id)
    run = controller.store.load_record('runs', run_id)
    assert run.raw.exit_code == 0 and run.observation.executed and run.observation.probe_complete
    assert feedback['verdict']['candidate_id'] == saved.candidate_id
    assert all(word in feedback['instruction'] for word in ('原版', '名称', '说明', '输入'))
    assert feedback['decision'] == 'continue' and feedback['projection']['omitted_issues'] == 0
    system = '\n'.join(message['content'] for message in wires[0]['messages']
                       if message['role'] == 'system' and isinstance(message['content'], str))
    assert 'neutral fixture names' in system and 'docstrings' in system
    assert 'supported minimal candidate early' in system

    states=[]
    for message in wires[1]['messages']:
        if message['role']!='user':continue
        try:data=json.loads(message.get('content',''))
        except (ValueError,TypeError):continue
        if isinstance(data,dict) and 'task_state' in data:states.append(data['task_state'])
    assert len(states)==1
    state=states[0]
    assert any(f['run_id']==run_id and f['exit_code']==0 and f['executed'] and f['probe_complete'] for f in state['facts'])
    assert state['latest_candidate']['id']==saved.candidate_id
    assert state['latest_candidate']['result']=='NOT_REPRODUCED'
    assert state['completion']['accepted_candidate_id'] is None
    assert state['completion']['evidence_level']=='NONE'
    assert state['budgets']['remaining_steps']==19


def test_timeout_facts_reach_next_actual_exploration_request(tmp_path,projects,facts):
    from tests.unit.test_controller_execution_recovery import fault_sequence
    request,_,_,context=setup(tmp_path,projects,facts)
    script,wires=ScriptedModel(),[]
    async def handle(http_request):
        payload=json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            if len(wires)==1:
                return tool_reply('write_candidate',{'files':[{'path':'tests/test_repro.py',
                    'content':'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n','role':'test'}],
                    'hypothesis':'复现空输入异常'},call_id='publish')
            return tool_reply('request_information',{'question':'需要执行环境信息'},call_id='stop')
        data=json.loads(payload['messages'][-1]['content'])
        kind='verdict' if 'available_refs' in data else 'contract'
        reply=await script.complete(ModelRequest(tuple(payload['messages']),kind),context)
        return httpx.Response(200,json={'id':'offline','object':'chat.completion','created':1,'model':'offline',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':reply.text}}]})
    controller=create_controller(request,ModelConfig(base_url='https://offline.example/v1',model='offline',
        output_limit_field='max_tokens'),transport=httpx.MockTransport(handle))
    # Controlled timeout injection is before Runner persists its record, not a forged STATE.
    fault_sequence(controller,['COMMAND_TIMEOUT'])
    result=asyncio.run(controller.run(request,context))
    assert result.status==TaskState.NEEDS_INFORMATION and len(wires)==2
    states=[]
    for message in wires[1]['messages']:
        if message['role']!='user':continue
        try:value=json.loads(message.get('content',''))
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and 'task_state' in value:states.append(value['task_state'])
    assert len(states)==1
    state=states[0]
    fact=state['facts'][-1]
    run=controller.store.load_record('runs',fact['run_id'])
    assert fact['stop_reason']==run.raw.stop_reason=='COMMAND_TIMEOUT'
    assert fact['exit_code'] is None and fact['probe_complete'] is False
    assert state['completion']['accepted_candidate_id'] is None
    assert state['completion']['evidence_level']=='NONE'
    assert state['budgets']['remaining_steps']==19
