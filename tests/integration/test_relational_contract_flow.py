"""Real SDK delivery/state-binding regressions; no claim about a real model's judgment."""
import asyncio
import json

import httpx

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, TaskState
from tests.integration.test_agentscope_backends import scripted_transport
from tests.integration.test_agentscope_domain_tools import sidecar_refs
from tests.integration.test_agentscope_runtime import read_reply, tool_reply, write_payload
from tests.unit.test_controller import ScriptedModel, setup


GAP = 'The report gives no literal output for the equivalent representation.'


def _model_config():
    return ModelConfig(base_url='https://offline.example/v1', model='offline', output_limit_field='max_tokens')


def test_source_backed_equivalence_and_oracle_scope_policy_reach_all_real_requests(tmp_path, projects, facts):
    request, _, _, context = setup(tmp_path, projects, facts)
    wires, calls, toolkit = [], [], []
    delegate = scripted_transport(ScriptedModel(), context, calls, toolkit)

    async def handle(http_request):
        wires.append(json.loads(http_request.content))
        return await delegate(http_request)

    controller = create_controller(request, _model_config(), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.DONE
    assert calls == ['contract', 'exploration', 'verdict']
    for payload in wires:
        system = '\n'.join(m['content'] for m in payload['messages'] if m['role'] == 'system')
        assert 'source-backed equivalence' in system
        assert 'oracle variables' in system
        assert 'not merely the absence of an exception' in system


def test_grounded_refusal_details_reach_wire_and_revision_rebinds_before_publication(tmp_path, projects, facts):
    request, _, _, context = setup(tmp_path, projects, facts)
    script = ScriptedModel()
    wires, domain_kinds, feedback = [], [], []
    analyses = 0
    controller = None

    async def handle(http_request):
        nonlocal analyses
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            number = len(wires)
            if number == 1:
                return tool_reply('write_candidate', write_payload(content='from example.parser import parse\ndef test_empty():\n    assert parse([]) == []\n'), call_id='blocked')
            if number == 2:
                message = next(m for m in payload['messages'] if m.get('tool_call_id') == 'blocked')
                feedback.append(json.loads(message['content']))
                snapshot_file = next((request.output_dir / 'snapshots').glob('*/snapshot.json'))
                snapshot = json.loads(snapshot_file.read_text(encoding='utf-8'))
                from pathlib import Path
                return read_reply(Path(snapshot['root']) / 'example/parser.py', 'original-source')
            if number == 3:
                message = next(m for m in payload['messages'] if m.get('tool_call_id') == 'original-source')
                return tool_reply('revise_contract', {'source_refs': sidecar_refs(message['content']),
                    'reason': '原版证据支持等价行为，无须发明未说明的输出形式'}, call_id='revision')
            assert number == 4
            return tool_reply('write_candidate', write_payload(content='from example.parser import parse\ndef test_empty():\n    assert parse([]) == []\n'), call_id='supported')
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        domain_kinds.append(kind)
        response = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        value = json.loads(response.text)
        if kind == 'contract':
            analyses += 1
            value['missing_information'] = [GAP] if analyses == 1 else []
            if analyses == 2:
                assert len(data['sources']) == 2 and 'def parse' in data['texts'][1]
                value['source_indices'] = [0, 1]
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 1,
            'model': 'offline', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(value)}}]})

    controller = create_controller(request, _model_config(), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.DONE
    assert len(wires) == context.budget.steps_used == 4
    assert domain_kinds == ['contract', 'contract', 'verdict']
    refusal = feedback[0]
    assert refusal['code'] == 'GROUNDED_CONTRACT_REQUIRED'
    assert refusal['missing'] == ['missing_information']
    assert refusal['missing_information'] == [GAP]
    assert refusal['contract_version'] == 1
    assert 'source-backed equivalence' in refusal['next_action']
    assert 'revise_contract' in refusal['next_action']
    records = [json.loads(p.read_text(encoding='utf-8')) for p in (request.output_dir / 'contracts').glob('*.json')]
    records.sort(key=lambda c: c['version'])
    assert len(records) == 2
    assert records[0]['missing_information'] == [GAP] and records[1]['missing_information'] == []
    assert records[0]['contract_id'] == records[1]['contract_id'] == refusal['contract_id']
    candidate = controller.store.load_record('candidates', result.accepted_candidate_id)
    assert candidate.contract_version == 2 and candidate.contract_id == refusal['contract_id']
    events, errors = controller.store.read_events()
    assert not errors
    assert sum(e.kind == 'candidate.published' for e in events) == 1
    assert sum(e.kind == 'run.completed' for e in events) == 2
