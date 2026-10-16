import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.models import BudgetLimits, TaskState
from tests.unit.test_controller import setup, revise, source_ref, ask


def revision_payloads(model):
    return [json.loads(message['content']) for message in model.messages
            if message['role'] == 'user' and 'response_schema' in message['content']]


def test_repeated_exact_source_is_sent_once_but_distinct_ranges_survive(tmp_path, projects, facts):
    request, model, controller, context = setup(tmp_path, projects, facts)
    original = model.complete

    async def choose_all_sources(call, ctx):
        response = await original(call, ctx)
        if call.response_kind == 'contract':
            data = json.loads(call.messages[-1]['content'])
            result = json.loads(response.text)
            result['source_indices'] = list(range(len(data['sources'])))
            return replace(response, text=json.dumps(result))
        return response

    model.complete = choose_all_sources
    plan = controller.explorer_factory
    ref = lambda: source_ref(plan.explorer)
    other = lambda: source_ref(plan.explorer, start=1, end=1)
    plan.plan = [revise(lambda: [ref(), ref()], 'first reason'),
                 revise(lambda: [ref(), other()], 'second reason'), ask()]
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.NEEDS_INFORMATION
    payloads = revision_payloads(model)
    assert [len(payload['sources']) for payload in payloads] == [1, 2, 3]
    assert payloads[2]['revision']['reason'] == 'second reason'
    assert payloads[2]['revision']['previous_contract']['version'] == 2
    assert len(payloads[2]['texts']) == 3


def test_last_revision_preserves_blockers_when_steps_expire(tmp_path, projects, facts):
    request, model, controller, context = setup(tmp_path, projects, facts,
        limits=BudgetLimits(agent_steps=1), missing=True)
    plan = controller.explorer_factory
    plan.plan = [revise(lambda: [source_ref(plan.explorer)], 'Need trigger facts')]
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.EXHAUSTED  # No claim that the dataset is at fault.
    assert 'agent step limit reached' in result.uncertainties
    assert any('expected?' in note and 'v2' in note for note in result.uncertainties)
    events, errors = controller.store.read_events()
    assert not errors
    stopped = [event for event in events if event.kind == 'contract.unresolved_at_stop']
    assert len(stopped) == 1
    assert stopped[0].payload['contract_version'] == 2
    current = controller.store.load_contract(stopped[0].payload['contract_id'], 2)
    assert stopped[0].payload['blocking_count'] == len(current.missing_information)
    assert stopped[0].refs == (f"contracts/{stopped[0].payload['contract_id']}-v2.json",)
    assert model.kinds == ['contract', 'contract']
    report = json.loads((request.output_dir/'artifacts/diagnostic/report.json').read_text(encoding='utf-8'))
    assert any('expected?' in note for note in report['uncertainties'])


def test_revision_reason_is_included_in_input_capacity_check(tmp_path, projects, facts):
    request, model, controller, context = setup(tmp_path, projects, facts)
    plan = controller.explorer_factory
    plan.plan = [revise(lambda: [source_ref(plan.explorer)], 'x' * 32768), ask()]
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert model.kinds == ['contract']
    assert 'narrow line ranges' in controller.explorer.contexts[1].feedback
    assert controller.explorer.contexts[1].contract.version == 1


def test_revision_capacity_counts_repeated_description_and_metadata_before_call(tmp_path, projects, facts):
    request, model, controller, context = setup(tmp_path, projects, facts)
    request.issue_file.write_text('a' * 6000, encoding='utf-8')
    original = model.complete

    async def large_previous(call, ctx):
        response = await original(call, ctx)
        if call.response_kind == 'contract':
            data = json.loads(response.text)
            data['assumptions'] = ['a' * 21000]
            return replace(response, text=json.dumps(data))
        return response

    model.complete = large_previous
    plan = controller.explorer_factory
    plan.plan = [revise(lambda: [source_ref(plan.explorer)], 'narrow correction'), ask()]
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert model.kinds == ['contract']  # Oversized revision must never reach the provider.
    assert 'REVISION_BUDGET_EXCEEDED' in json.dumps([
        event.payload for event in controller.store.read_events()[0]])


def test_protocol_correction_cannot_overflow_revision_request_capacity(facts):
    from reproagent.core.models import EvidenceContext, IssueContract, IssueDescription, SourceRef
    from tests.unit.test_agent import agent
    from reproagent.core.serialization import canonical_bytes
    source = SourceRef('issue.md', 'hash')
    previous = IssueContract('contract', trigger='empty', expected='return []',
        reported_actual='IndexError', sources=(source,))
    evidence = EvidenceContext((source,), ('bug',), previous)
    description = IssueDescription('bug', 'hash')
    strategy, model, ctx = agent({'trigger':'empty', 'expected':'return []',
        'reported_actual':'IndexError', 'source_indices':[0]}, facts)
    asyncio.run(strategy.analyze(description, evidence))
    actual_first = json.loads(model.messages[-1]['content'])
    ctx.budget.limits = replace(ctx.budget.limits, tool_response_bytes=len(canonical_bytes(actual_first)))
    model.messages.clear()
    model.data = {}  # One repairable schema error would require another, larger request.
    with pytest.raises(ValueError, match='contract revision input'):
        asyncio.run(strategy.analyze(description, evidence))
    assert len([m for m in model.messages if m['role'] == 'user']) == 1


def test_post_redaction_revision_cannot_exceed_provider_input_capacity(facts):
    from reproagent.core.agent import ReproAgent
    from reproagent.core.budget import BudgetedGateway
    from reproagent.core.models import EvidenceContext, IssueContract, IssueDescription, SourceRef
    from tests.unit.test_agent import agent
    source = SourceRef('issue.md', 'hash')
    previous = IssueContract('contract', trigger='empty', expected='return []',
        reported_actual='IndexError', sources=(source,))
    _, provider, ctx = agent({'trigger':'empty', 'expected':'return []',
        'reported_actual':'IndexError', 'source_indices':[0]}, facts)
    strategy = ReproAgent(BudgetedGateway(provider, secrets=('abc',)), ctx)
    with pytest.raises(ValueError, match='contract revision input'):
        asyncio.run(strategy.analyze(IssueDescription('abc' * 3000, 'hash'),
            EvidenceContext((source,), ('abc' * 3000,), previous)))
    assert provider.messages == []
    assert ctx.budget.unknown_cost_calls == 0
