"""Generation guidance must reach all three real SDK request boundaries.

Scripted replies prove delivery and unchanged orchestration, not improved model judgment.
"""
import asyncio
import json
import sys

import httpx

from reproagent.app import create_controller
from reproagent.core.models import FixValidationRequest, ModelConfig
from tests.integration.test_agentscope_backends import scripted_transport
from tests.unit.test_controller import ScriptedModel, setup


def test_independent_scenarios_and_platform_rules_reach_all_model_roles(tmp_path, projects, facts):
    request, _, _, context = setup(tmp_path, projects, facts)
    script, calls, toolkit, wire = ScriptedModel(), [], [], []
    scripted = scripted_transport(script, context, calls, toolkit)

    async def handle(http_request):
        wire.append(json.loads(http_request.content))
        return await scripted(http_request)

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    fixed = projects.fixed(tmp_path / 'fixed-hidden')
    result = asyncio.run(controller.run(request, context, FixValidationRequest(fixed, sys.executable)))
    assert result.status.value == 'DONE'
    assert calls == ['contract', 'exploration', 'verdict']
    for payload in wire:
        system = '\n'.join(message['content'] for message in payload['messages']
                           if message['role'] == 'system')
        assert 'independent scenarios' in system
        assert 'platform' in system
        assert 'gold patch' in system
        assert 'incidental wording' in system
        assert 'test names' in system
    exploration = next(payload for payload in wire if payload.get('tools'))
    system = '\n'.join(message['content'] for message in exploration['messages'] if message['role'] == 'system')
    assert 'separate test functions' in system
    assert 'one focused regression test' not in system
    assert context.budget.steps_used == 1
    reviews = [json.loads(message['content']) for payload in wire for message in payload['messages']
               if message['role'] == 'user' and '"assertion_sites"' in message.get('content', '')]
    assert len(reviews) == 1
    for review in reviews:
        assert review['assertion_sites']
        assert 'assertion_reviews' in review['response_schema']['required']
    events, errors = controller.store.read_events()
    assert not errors
    assert sum(event.kind == 'verification.semantic_reused' for event in events) == 1
    assert sum(event.kind == 'run.completed' for event in events) == 3
    assert len(list((request.output_dir / 'verdicts').glob('*.json'))) == 2
