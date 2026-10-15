"""A final refused publication preserves actionable contract blockers without another call."""
import asyncio
import json

import pytest

from agentscope.message import ToolResultState

from reproagent.adapters.agentscope.tools import WriteCandidate
from reproagent.core.budget import BudgetStopped
from reproagent.core.candidate_service import CandidateService, GroundedContractRequired
from reproagent.core.models import BudgetLimits
from reproagent.experience_triggers import ERROR_METADATA
from tests.integration.test_agentscope_runtime import (
    agent_context, candidate_ids, environment, tool_reply, write_payload,
)


BLOCKERS = ("缺少触发输入及其必要配置。", "缺少该输入的正确输出依据。")


def capture_publication_results(monkeypatch):
    """Observe actual SDK tool responses while leaving publication checks intact."""
    responses = []
    original = WriteCandidate.call

    async def observed(self, **kwargs):
        response = await original(self, **kwargs)
        responses.append(response)
        return response

    monkeypatch.setattr(WriteCandidate, 'call', observed)
    return responses


def test_final_grounded_refusal_ends_with_original_blockers_without_retry(tmp_path, projects, facts, monkeypatch):
    # Losing this gate result would turn the refusal into bare step exhaustion.
    responses = capture_publication_results(monkeypatch)
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1),
        missing_information=BLOCKERS,
        answers=[tool_reply('write_candidate', write_payload(), call_id='blocked-final')])

    async def scenario():
        try:
            return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()

    result = asyncio.run(scenario())
    assert result.kind == 'request_information'
    assert 'contract-1' in result.question and 'v1' in result.question
    assert all(blocker in result.question for blocker in BLOCKERS)
    assert len(env.requests) == 1 and env.context.budget.steps_used == 1
    assert candidate_ids(env) == [] and env.service.project.candidates == ()
    assert not (env.task / 'runs').exists()
    assert env.service.contract.missing_information == BLOCKERS
    assert len(responses) == 1
    response = responses[0]
    assert response.state is ToolResultState.ERROR
    assert response.metadata[ERROR_METADATA] == 'GROUNDED_CONTRACT_REQUIRED'
    payload = json.loads(''.join(block.text for block in response.content))
    assert payload['code'] == 'GROUNDED_CONTRACT_REQUIRED'
    assert payload['missing_information'] == list(BLOCKERS)


def test_grounded_refusal_with_steps_left_allows_evidence_based_revision(tmp_path, projects, facts):
    # Prematurely closing the gate would suppress a valid revision from displayed evidence.
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=6), missing_information=BLOCKERS)
    entry = next(item for item in env.snapshot.files if item.path == 'example/parser.py')
    reference = {'path': 'example/parser.py', 'content_hash': entry.content_hash,
                 'start_line': 1, 'end_line': 2}
    env.answers.extend([
        tool_reply('Read', {'file_path': str(env.module)}, call_id='original'),
        tool_reply('write_candidate', write_payload(), call_id='blocked-recoverable'),
        tool_reply('revise_contract', {'source_refs': [reference], 'reason': '依据已显示原始证据复核缺失信息。'},
                   call_id='revision'),
    ])

    async def scenario():
        try:
            return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()

    result = asyncio.run(scenario())
    assert result.kind == 'revise_contract'
    assert len(env.requests) == 3 and env.context.budget.steps_used == 3
    refusal = next(message for message in env.requests[2]['messages']
                   if message.get('tool_call_id') == 'blocked-recoverable')
    payload = json.loads(refusal['content'])
    assert payload['code'] == 'GROUNDED_CONTRACT_REQUIRED'
    assert payload['missing_information'] == list(BLOCKERS)
    assert candidate_ids(env) == []


def test_missing_expected_without_explicit_blocker_keeps_step_exhaustion(tmp_path, projects, facts):
    # An incomplete program contract is not automatically an external information request.
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1), expected='',
                      answers=[tool_reply('write_candidate', write_payload())])

    async def scenario():
        try:
            with pytest.raises(BudgetStopped) as failure:
                await env.runtime.explore(agent_context(env))
            assert failure.value.reason == 'EXHAUSTED'
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()

    asyncio.run(scenario())
    assert env.gate.result is None and candidate_ids(env) == []
    assert len(env.requests) == 1


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
@pytest.mark.parametrize('stop_when', ['before_tool', 'during_refusal'])
def test_stop_at_final_publication_takes_priority_over_missing_information(
        tmp_path, projects, facts, monkeypatch, stop, stop_when):
    # A cancelled/expired task must not be relabelled as a normal information request.
    def stop_task():
        if stop == 'cancel':
            env.context.cancel_event.set()
        else:
            env.context.budget.deadline = env.context.budget.clock() - 1

    def stop_before_tool(request):
        env.requests.append(json.loads(request.content))
        if stop_when == 'before_tool':
            stop_task()
        return tool_reply('write_candidate', write_payload())

    if stop_when == 'during_refusal':
        original = CandidateService.publish

        def stopped_publication(self, draft):
            try:
                return original(self, draft)
            except GroundedContractRequired:
                stop_task()
                raise

        monkeypatch.setattr(CandidateService, 'publish', stopped_publication)

    # Only the external HTTP boundary is scripted; the SDK, guard and domain tool stay real.
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1),
                      missing_information=BLOCKERS, handler=stop_before_tool)

    async def scenario():
        try:
            with pytest.raises(BudgetStopped) as failure:
                await env.runtime.explore(agent_context(env))
            assert failure.value.reason == ('CANCELLED' if stop == 'cancel' else 'EXHAUSTED')
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()

    asyncio.run(scenario())
    assert env.gate.result is None and candidate_ids(env) == []
    assert len(env.requests) == 1
