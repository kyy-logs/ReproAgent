"""Actual SDK wire and controller recovery; no real model judgment is implied."""
import asyncio
import json

import httpx

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, TaskState
from tests.integration.test_agentscope_runtime import tool_reply
from tests.unit.test_controller import ScriptedModel, setup


def test_precision_refusal_reaches_next_exploration_request(tmp_path, projects, facts):
    request, _, _, context = setup(tmp_path, projects, facts)
    script = ScriptedModel()
    wires, reviews = [], []
    candidate = ('from example.parser import parse\n'
                 'def test_empty():\n'
                 '    assert parse([]) == []\n'
                 '    assert "orphan:table-1" in "rendered"\n')

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            if len(wires) == 1:
                return tool_reply('write_candidate', {'files':[
                    {'path':'tests/test_repro.py', 'role':'test', 'content':candidate}],
                    'hypothesis':'Empty input reproduction with an excessive output control'}, call_id='publish')
            return tool_reply('request_information', {'question':'Need source support for the output control'}, call_id='stop')
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        reply = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        answer = json.loads(reply.text)
        if kind == 'verdict':
            reviews.append(data)
            ids = {row['id'] for row in data.get('oracle_checks', ()) if row['kind'] == 'literal_comparison'}
            for row in answer.get('oracle_reviews', ()):
                if row['check_id'] in ids:
                    row.update(supported=False, basis='unknown', evidence_refs=[], source_quote='',
                               reason='Exact representation is not required by the report.')
        return httpx.Response(200, json={'id':'offline', 'object':'chat.completion', 'created':1,
            'model':'offline', 'choices':[{'index':0, 'finish_reason':'stop',
                'message':{'role':'assistant', 'content':json.dumps(answer)}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    assert len(wires) == context.budget.steps_used == 2
    assert len(reviews) == 1
    sites = reviews[0]['assertion_sites']
    assert sites[0]['execution_observation']['state'] == 'failure_location_overlap'
    assert sites[1]['execution_observation']['state'] == 'not_established'
    ids = {row['id'] for row in reviews[0]['oracle_checks'] if row['kind'] == 'literal_comparison'}
    assert ids
    phases = []
    for message in wires[1]['messages']:
        if message['role'] != 'user' or not isinstance(message['content'], str):
            continue
        try:
            data = json.loads(message['content'])
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('feedback'):
            phases.append(json.loads(data['feedback']))
    feedback = phases[-1]
    assert feedback['decision'] == 'continue'
    assert feedback['verdict']['classification'] == 'INVALID_CANDIDATE'
    issue = feedback['verdict']['issues'][0]
    assert issue['code'] == 'CANDIDATE_ORACLES_UNCONFIRMED'
    assert ids <= set(issue['measurements']['oracle_ids'])
