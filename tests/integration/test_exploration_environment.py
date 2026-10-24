"""Environment facts must reach the actual SDK request, without fixed-side leakage."""
import asyncio
import json
import sys
from dataclasses import replace

import pytest

from reproagent.core.budget import BudgetStopped
from reproagent.core.models import BudgetLimits, EnvironmentSnapshot, FixValidationRequest, TaskState
from tests.integration.test_agentscope_explorer import exploration, run_task
from tests.integration.test_agentscope_runtime import (
    agent_context, environment as runtime_environment, tool_reply, write_payload,
)
from tests.unit.test_controller import BUGGY


def phase_data(request):
    phases = []
    for message in request['messages']:
        if message.get('role') != 'user':
            continue
        try:
            data = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and 'contract' in data and 'budget' in data:
            phases.append(data)
    assert phases
    return phases[-1]


def test_measured_environment_reaches_each_phase_without_an_extra_probe(tmp_path, projects, facts, monkeypatch):
    request, controller, context, requests, answers = exploration(tmp_path, projects, facts, [
        tool_reply('write_candidate', write_payload(content=BUGGY), call_id='candidate'),
        tool_reply('request_information', {'question': 'which trigger?'}, call_id='ask'),
    ], classification='NOT_REPRODUCED')
    prepare = controller.runner.prepare
    measured = []

    async def record(*args, **kwargs):
        value = await prepare(*args, **kwargs)
        measured.append(value)
        return value

    monkeypatch.setattr(controller.runner, 'prepare', record)
    result = run_task(request, controller, context)
    assert result.status == TaskState.NEEDS_INFORMATION
    assert len(measured) == 1 and measured[0].tool_version
    assert len(requests) == context.budget.steps_used == 2 and not answers
    for wire in requests:
        delivered = phase_data(wire)['environment']
        assert delivered['environment_id'] == measured[0].environment_id
        assert delivered['pytest_version'] == measured[0].tool_version
        assert delivered['python'] == measured[0].python
        assert delivered['candidate_parent'] == request.language.candidate_parent
        assert delivered['pytest_args'] == list(request.language.pytest_args)
        assert delivered['fixture_inventory_status'] == 'not_collected'
        assert {t['function']['name'] for t in wire['tools']} == {
            'Read', 'Grep', 'Glob', 'write_candidate', 'revise_contract', 'request_information'}


def test_only_original_environment_is_disclosed_with_fixed_validation(tmp_path, projects, facts, monkeypatch):
    request, controller, context, requests, _ = exploration(tmp_path, projects, facts, [
        tool_reply('write_candidate', write_payload(content=BUGGY), call_id='candidate')])
    fixed = projects.fixed(tmp_path / 'fixed-private-environment')
    prepare = controller.runner.prepare
    environments = []
    fixed_marker = 'FIXED_ENVIRONMENT_CANARY_8a3b14'

    async def record(*args, **kwargs):
        value = await prepare(*args, **kwargs)
        if environments:
            value = replace(value, tool_version=fixed_marker, limitations=(fixed_marker,))
        environments.append(value)
        return value

    monkeypatch.setattr(controller.runner, 'prepare', record)
    result = run_task(request, controller, context, FixValidationRequest(fixed, sys.executable))
    assert result.status == TaskState.DONE
    assert len(environments) == 2 and len(requests) == 1
    original = phase_data(requests[0])['environment']
    assert original['environment_id'] == environments[0].environment_id
    assert original['pytest_version'] == environments[0].tool_version
    sent = json.dumps(requests)
    assert fixed_marker not in sent and fixed.name not in sent
    assert environments[1].environment_id not in sent


def test_environment_unknowns_are_not_inferred_and_secrets_are_redacted(tmp_path, projects, facts):
    env = runtime_environment(tmp_path, projects, facts)
    secret = 'environment-only-secret-43e821'
    env.runtime.secrets = (secret,)
    original = EnvironmentSnapshot('env-original', '/runtime/' + secret + '/python',
        limitations=('dependency ' + secret + ' was not inspected',))
    try:
        phase = replace(agent_context(env), environment=original)
        text = env.runtime._message(phase).get_text_content()
        data = json.loads(text)['environment']
        assert secret not in text and '[REDACTED]' in text
        assert secret in original.python  # Redaction must not mutate recorded facts.
        assert data['pytest_version'] is None
        assert data['fixture_inventory_status'] == 'not_collected'
        assert 'fixtures' not in data and 'python_version' not in data
        assert 'environment' not in json.loads(env.runtime._message(agent_context(env)).get_text_content())
    finally:
        asyncio.run(env.factory.aclose())


def test_environment_material_cannot_bypass_phase_capacity(tmp_path, projects, facts):
    env = runtime_environment(tmp_path, projects, facts,
        limits=BudgetLimits(tool_response_bytes=2048))
    try:
        phase = replace(agent_context(env), environment=EnvironmentSnapshot(
            'env-original', sys.executable, limitations=('x' * 4096,)))
        with pytest.raises(BudgetStopped):
            env.runtime._message(phase)
        assert not env.requests
    finally:
        asyncio.run(env.factory.aclose())
