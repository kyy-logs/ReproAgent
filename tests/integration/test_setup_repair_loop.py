"""Real SDK / local pytest repair chain, with only HTTP model answers scripted."""
import asyncio
import json

import httpx

from reproagent.app import create_controller
from reproagent.core.models import BudgetLimits, EvidenceLevel, ModelConfig, ModelRequest, TaskState
from reproagent.core.serialization import bytes_hash
from tests.integration.test_agentscope_runtime import tool_reply, wire_progress
from tests.unit.test_controller import setup, ScriptedModel
from tests.unit.test_setup_diagnostics import MISSING_RESOURCE


def test_setup_failure_reaches_wire_then_targeted_read_and_resource_republish(tmp_path, projects, facts):
    request, _, _, ctx = setup(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=20))
    before = bytes_hash((request.repo / 'example/parser.py').read_bytes())
    wires, semantic = [], ScriptedModel()

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            n = len(wires)
            if n <= 12 or 14 <= n <= 17:
                return tool_reply('Read', {'file_path':'example/parser.py'},call_id=f'read-{n}')
            if n in (13,18):
                files = [{'path':'tests/test_repro.py','content':MISSING_RESOURCE,'role':'test'}]
                if n == 18:
                    files.append({'path':'tests/missing-resource.txt','content':'example resource','role':'data'})
                return tool_reply('write_candidate',{'files':files,'hypothesis':'empty input'},call_id=f'publish-{n}')
            raise AssertionError('repair must finish within reserved budget')
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        reply = await semantic.complete(ModelRequest(tuple(payload['messages']),kind),ctx)
        return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,
            'model':'offline','choices':[{'index':0,'finish_reason':'stop',
                'message':{'role':'assistant','content':reply.text}}]})

    controller = create_controller(request,ModelConfig(base_url='https://offline.example/v1',
        model='offline',output_limit_field='max_tokens'),transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request,ctx))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert ctx.budget.steps_used == len(wires) == 18
    phases = []
    for message in wires[13]['messages']:
        if message['role'] != 'user' or not isinstance(message['content'],str):
            continue
        try:
            data = json.loads(message['content'])
        except ValueError:
            continue
        if isinstance(data,dict) and 'feedback' in data:
            phases.append(data)
    phase = phases[-1]
    assert phase['original_execution_count'] == 1
    feedback = json.loads(phase['feedback'])
    issue = feedback['verdict']['issues'][0]
    assert issue['code'] == 'TEST_SETUP_FAILED' and issue['evidence_refs']
    assert 'missing-resource.txt' in issue['measurements']['setup_failures'][0]['message']
    for index in range(13,17):
        assert wire_progress(wires[index])['facts']['readers_available_next_call'] is True
        result_message = next(m for m in wires[index+1]['messages'] if m.get('tool_call_id')==f'read-{index+1}')
        assert 'refused' not in result_message['content'] and 'def parse' in result_message['content']
    runs = [json.loads(p.read_text(encoding='utf-8')) for p in (request.output_dir/'runs').glob('*/execution.json')]
    assert len(runs) == 3 and all(r['execution_role']=='original' for r in runs)
    assert sum(bool(r['observation']['executed']) for r in runs) == 2
    assert semantic.kinds == ['contract','verdict','verdict']
    assert bytes_hash((request.repo/'example/parser.py').read_bytes()) == before
    snapshots = list((request.output_dir/'snapshots').glob('*/code/example/parser.py'))
    assert snapshots and all(bytes_hash(p.read_bytes())==before for p in snapshots)
