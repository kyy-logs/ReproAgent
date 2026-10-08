"""The Controller and the SDK exploration phase together, on a real frozen snapshot.

The product strategy is assembled directly here: an ``AgentScopeExplorer`` over a model
factory whose transport is a mock, so every exploration request in these runs is a real SDK
reply and every tool call runs the real tool.  The domain requests (contract and verdict)
are scripted, which is what makes the Controller's own decisions -- execute, verify, repeat,
revise, stop -- the subject of the assertions.
"""
import asyncio
import json
import sys
from dataclasses import replace

import httpx
import pytest

from reproagent.adapters.agentscope.explorer import agentscope_explorer_factory
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.core.models import (AgentContext, BudgetLimits, EvidenceLevel, FileEntry, IssueContract, ModelConfig,
    PythonPytestConfig, TaskRequest, TaskState)
from reproagent.store import TaskStore
from reproagent.app import create_controller
from tests.integration.test_agentscope_runtime import pending_tasks, text_reply, tool_reply, until, write_payload
from tests.unit.test_controller import BUGGY, ScriptedModel, action_endings, phase_results


def exploration(tmp_path, projects, facts, answers, *, classification='REPRODUCED', limits=None, missing=False):
    """One task whose exploration phases are real SDK replies over a mock transport."""
    repo = projects.plain(tmp_path / 'repo')
    request = TaskRequest(repo, tmp_path / '任务', repo / 'issue.md',
        language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',)),
        limits=limits or BudgetLimits())
    config = ModelConfig(base_url='https://offline.example/v1', model='offline',
                         output_limit_field='max_tokens', max_output_tokens=1024)
    model = ScriptedModel(missing=missing, classification=classification)
    requests = []

    def handler(http_request):
        requests.append(json.loads(http_request.content))
        return answers.pop(0)

    factory = agentscope_explorer_factory(config, TaskStore(request.output_dir),
                                          transport=httpx.MockTransport(handler))
    controller = create_controller(request, config, gateway=model, explorer_factory=factory)
    return request, controller, facts.context(limits=request.limits), requests, answers


def run_task(request, controller, context, fixed=None):
    return asyncio.run(controller.run(request, context, fixed))


def test_plain_success_text_and_missing_information_do_not_publish_success(tmp_path, projects, facts):
    """The SDK's own ending is not a result: only the Controller's state decides the outcome.

    A phase that answers in prose -- however confidently it is phrased -- published nothing,
    so nothing is executed and no reproduction package is delivered; a phase that reports
    what it is missing stops the task for that reason instead.
    """
    request, controller, context, _, _ = exploration(tmp_path / 'prose', projects, facts, [
        text_reply('The bug is reproduced successfully: the test fails exactly as the issue reports.')])
    result = run_task(request, controller, context)
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'NO_CANDIDATE'
    assert result.evidence_level == EvidenceLevel.NONE and not result.accepted_candidate_id
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    # Nothing was executed and no candidate exists: a prose ending never becomes evidence.
    assert not (request.output_dir / 'runs').exists()
    assert not (request.output_dir / 'candidates').exists()
    assert phase_results(controller.store.read_events()[0]) == [('no_candidate', 'NO_CANDIDATE')]

    request, controller, context, _, _ = exploration(tmp_path / 'asking', projects, facts, [
        tool_reply('request_information', {'question': 'which parser should reproduce it?'})])
    result = run_task(request, controller, context)
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'MISSING_INFORMATION'
    assert 'which parser should reproduce it?' in result.uncertainties
    assert not (request.output_dir / 'artifacts/reproduction').exists()


def test_an_original_run_that_passes_returns_to_the_phase_and_is_never_delivered(tmp_path, projects, facts):
    """A candidate the original version passes is not a reproduction: the phase is asked again."""
    request, controller, context, requests, _ = exploration(tmp_path / 'passing', projects, facts, [
        tool_reply('write_candidate', write_payload(content=BUGGY), call_id='call-1'),
        tool_reply('request_information', {'question': 'the candidate passed; what is the trigger?'}, call_id='call-2')],
        classification='NOT_REPRODUCED')
    result = run_task(request, controller, context)
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'MISSING_INFORMATION'
    # The candidate was executed and verified once, and never repeated or exported.
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 1
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    assert len(requests) == 2
    # The next phase was told what the original run showed, in the Controller's own words.
    assert 'NOT_REPRODUCED' in json.dumps(requests[1]['messages'])
    assert phase_results(controller.store.read_events()[0]) == [('candidate', 'NOT_REPRODUCED'),
                                                                ('request_information', 'MISSING_INFORMATION')]
    assert action_endings(controller.store.read_events()[0]) == [('action.completed', 'run_candidate', 'OK')]


def test_an_environment_block_stops_with_its_own_diagnostic(tmp_path, projects, facts):
    request, controller, context, requests, _ = exploration(tmp_path / 'blocked', projects, facts, [
        tool_reply('write_candidate', write_payload(content=BUGGY), call_id='call-1')],
        classification='ENVIRONMENT_BLOCKED')
    result = run_task(request, controller, context)
    assert result.status == TaskState.BLOCKED and result.stop_reason == 'ENVIRONMENT_BLOCKED'
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    # The blocked environment ends the task: the phase is not asked for another candidate.
    assert len(requests) == 1


def test_a_published_candidate_is_executed_verified_and_repeated_without_a_decision(tmp_path, projects, facts):
    """One phase, one candidate, and the whole lifecycle without asking the model again."""
    request, controller, context, requests, _ = exploration(tmp_path / 'confirmed', projects, facts, [
        tool_reply('write_candidate', write_payload(content=BUGGY), call_id='call-1')])
    result = run_task(request, controller, context)
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert result.export_state == 'published'
    # One exploration request, and the run/submit steps the Controller owns.
    assert len(requests) == 1 and context.budget.steps_used == 1
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2
    events = controller.store.read_events()[0]
    assert action_endings(events) == [('action.completed', 'run_candidate', 'OK'),
                                      ('action.completed', 'submit_candidate', 'OK')]
    assert phase_results(events) == [('candidate', 'REPRODUCED')]
    assert (request.output_dir / 'artifacts/reproduction/replay.py').is_file()


def test_the_fixed_version_never_reaches_the_exploration(tmp_path, projects, facts):
    from reproagent.core.models import FixValidationRequest
    request, controller, context, requests, _ = exploration(tmp_path / 'fixed', projects, facts, [
        tool_reply('write_candidate', write_payload(content=BUGGY), call_id='call-1')])
    fixed = projects.fixed(tmp_path / 'fixed-secret-location')
    result = run_task(request, controller, context, FixValidationRequest(fixed, sys.executable))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.DIFFERENTIAL_VALIDATED
    assert len(requests) == 1
    assert str(fixed) not in json.dumps(requests)
    assert 'fixed' not in json.dumps(requests[0]['messages']) or 'fixed' in json.dumps(BUGGY)


def test_a_large_snapshot_never_enters_the_phase_input(tmp_path, projects, facts):
    """The file index is the SDK's to search, not a payload the Controller has to truncate.

    The action protocol had to fit a thousand file names into the context budget; the phase
    is sent the contract and the feedback instead, so the size of the snapshot cannot
    enlarge, or overflow, what a phase is asked to work from.
    """
    from reproagent.core.candidate_service import CandidateService
    from reproagent.core.models import ProjectView
    from reproagent.adapters.agentscope.evidence import EvidenceLedger
    from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
    from reproagent.adapters.agentscope.snapshot_backend import SnapshotBackend
    from reproagent.adapters.agentscope.tools import build_toolkit
    from reproagent.core.phase import PhaseGate
    from reproagent.workspace import Workspace

    repo = projects.plain(tmp_path / 'repo')
    request = TaskRequest(repo, tmp_path / '任务', repo / 'issue.md', language=PythonPytestConfig())
    store = TaskStore(request.output_dir)
    workspace = Workspace(request.output_dir, store)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    huge = replace(snapshot, files=tuple(FileEntry('tests/' + str(index) + 'x' * 100 + '.py', 'h', 1)
                                          for index in range(1000)))
    contract = IssueContract('contract-1', 1, expected='parse([]) returns []')
    service = CandidateService(ProjectView(huge), workspace, context)
    service.bind_contract(contract)
    gate = PhaseGate()
    factory = AgentScopeModelFactory(ModelConfig(base_url='https://offline.example/v1', model='offline',
                                                 output_limit_field='max_tokens', max_output_tokens=1024), store,
                                     transport=httpx.MockTransport(lambda http: text_reply()))
    runtime = AgentScopeRuntime(factory.create(context, 'exploration'),
                                build_toolkit(SnapshotBackend(ProjectView(huge), store, context), EvidenceLedger(ProjectView(huge), store), service, gate, context),
                                gate, context, system_prompt='explore', store=store)
    message = runtime._message(AgentContext(contract, ProjectView(huge)))
    text = message.get_text_content() or ''
    assert len(text.encode('utf-8')) <= context.budget.limits.tool_response_bytes
    assert 'tests/' + '0' * 100 not in text and '.py' not in text
    assert 'contract-1' in text


def test_a_cancelled_task_closes_the_phase_session_and_stops_with_a_diagnostic(tmp_path, projects, facts):
    async def scenario():
        seen, release = [], asyncio.Event()

        async def held(http_request):
            seen.append(json.loads(http_request.content))
            await release.wait()
            return text_reply()

        repo = projects.plain(tmp_path / 'repo')
        request = TaskRequest(repo, tmp_path / '任务', repo / 'issue.md',
            language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',)))
        config = ModelConfig(base_url='https://offline.example/v1', model='offline',
                             output_limit_field='max_tokens', max_output_tokens=1024)
        controller = create_controller(request, config, gateway=ScriptedModel(),
            explorer_factory=agentscope_explorer_factory(config, TaskStore(request.output_dir),
                                                         transport=httpx.MockTransport(held)))
        context = facts.context()
        running = asyncio.ensure_future(controller.run(request, context))
        await until(lambda: seen)
        context.cancel_event.set()
        release.set()
        result = await running
        assert result.status == TaskState.CANCELLED and result.stop_reason == 'CANCELLED'
        assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
        assert not (request.output_dir / 'artifacts/reproduction').exists()
        assert len(seen) == 1
        # The phase session is closed and no SDK task survives the run; the explorer's own
        # model factory is released with it, so the client it built is not left open.
        assert controller.explorer.runtime is None
        assert controller.explorer.model_factory is None
        assert pending_tasks() == set()

    asyncio.run(scenario())
