import asyncio
import importlib
import json
import sys
from dataclasses import replace

from reproagent.core.models import BudgetLimits, EvidenceLevel, FixValidationRequest, ModelConfig, ModelResponse, PythonPytestConfig, TaskRequest, TaskState


class ScriptedModel:
    def __init__(self, missing=False): self.messages=[]; self.step=0; self.candidate_id=''; self.missing=missing
    async def complete(self, request, context):
        self.messages.extend(request.messages)
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            result = {'trigger':'empty list','expected':'' if self.missing else 'parse([]) returns []','reported_actual':'IndexError','source_indices':[] if self.missing else [0], 'missing_information':['expected?'] if self.missing else [], 'observable_checks':[], 'assumptions':[]}
        elif request.response_kind == 'verdict':
            result = {'classification':'REPRODUCED','reason':'reported IndexError on empty input', 'evidence_refs':data['available_refs'], 'expected_assertion':True,'target_triggered':True,'failure_matches_issue':True}
        else:
            if self.missing:
                return ModelResponse('{"name":"request_information","parameters":{"question":"Provide expected behavior"}}')
            names = ('write_candidate', 'run_candidate', 'submit_candidate')
            name = names[min(self.step, 2)]; self.step += 1
            self.candidate_id = (data['candidate_ids'] or [''])[0]
            parameters = {'files':[{'path':'tests/test_repro.py','content':'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n','role':'test'}], 'hypothesis':'empty input'} if name == 'write_candidate' else {'candidate_id':self.candidate_id}
            result = {'name':name, 'parameters':parameters}
        return ModelResponse(json.dumps(result))


class ScriptedActions:
    """Proposes fixed actions; the last one repeats, so the script never runs dry."""
    def __init__(self, actions): self.actions = list(actions); self.messages = []
    async def complete(self, request, context):
        self.messages.extend(request.messages)
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            result = {'trigger':'empty list','expected':'parse([]) returns []','reported_actual':'IndexError','source_indices':[0],
                      'missing_information':[],'observable_checks':[],'assumptions':[]}
        elif request.response_kind == 'verdict':
            result = {'classification':'REPRODUCED','reason':'reported IndexError on empty input','evidence_refs':data['available_refs'],
                      'expected_assertion':True,'target_triggered':True,'failure_matches_issue':True}
        else:
            result = self.actions[0] if len(self.actions) == 1 else self.actions.pop(0)
        return ModelResponse(json.dumps(result))


def controller_for(tmp_path, projects, facts, model, limits=None):
    repo = projects.plain(tmp_path / 'repo')
    request = TaskRequest(repo, tmp_path / '任务', repo / 'issue.md', language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',)), limits=limits or BudgetLimits())
    app = importlib.import_module('reproagent.app')
    return request, app.create_controller(request, ModelConfig(), gateway=model), facts.context(limits=request.limits)


def setup(tmp_path, projects, facts, limits=None, missing=False):
    model = ScriptedModel(missing)
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, limits)
    return request, model, controller, ctx


def test_controller_requires_replay_export_and_cleanup_before_done(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.export_state == 'published'
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2
    assert (request.output_dir / 'artifacts/reproduction/replay.py').exists()


def test_budget_after_first_observation_preserves_partial_evidence(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts, BudgetLimits(agent_steps=2))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    assert result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION and result.accepted_candidate_id
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()


def test_cleanup_failure_overrides_cancel_with_original_reason_saved(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    original = controller.runner.execute
    async def failed(*args, **kwargs):
        execution = await original(*args, **kwargs)
        return replace(execution, raw=replace(execution.raw, cleanup_ok=False, stop_reason='CANCELLED'))
    controller.runner.execute = failed
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.FAILED and result.stop_reason == 'CANCELLED'


def test_missing_information_and_environment_block_have_diagnostic_packages(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path / 'missing', projects, facts, missing=True)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    request, _, controller, ctx = setup(tmp_path / 'blocked', projects, facts)
    request = replace(request, language=replace(request.language, python=str(tmp_path / 'no-python.exe')))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()


def test_fixed_validation_is_separate_and_same_candidate(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / 'fixed-secret-location')
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed, sys.executable)))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.DIFFERENTIAL_VALIDATED
    runs = [controller.store.load_record('runs', p.parent.name) for p in (request.output_dir / 'runs').glob('*/execution.json')]
    assert sorted(r.execution_role for r in runs) == ['fixed','original','original']
    assert len({r.candidate_id for r in runs}) == 1
    assert str(fixed) not in json.dumps(model.messages)


def event_dump(events): return json.dumps([{'kind': event.kind, 'payload': event.payload} for event in events])


def test_action_error_events_use_codes_not_raw_response(tmp_path, projects, facts, monkeypatch):
    from reproagent.core.controller import ACTION_RESULT_CODES
    from reproagent.core.serialization import canonical_hash
    secret = 'synthetic-provider-secret'
    monkeypatch.setenv('REPROAGENT_API_KEY', secret)
    actions = [
        {'name':'read_file','parameters':{'path':f'../{secret}','start':1,'end':2}},
        {'name':'write_candidate','parameters':{'files':[{'path':'tests/test_repro.py','role':'test',
            'content':f'from example.parser import parse\ndef test_empty(): assert parse([]) == []  # {secret}\n'}],'hypothesis':'empty input'}},
        {'name':'run_candidate','parameters':{'candidate_id':'candidate-never-published'}}]
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedActions(actions), BudgetLimits(agent_steps=6))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    selected = [event.payload for event in events if event.kind == 'action.selected']
    completed = [event.payload for event in events if event.kind == 'action.completed']
    rejected = [event.payload for event in events if event.kind == 'action.rejected']
    assert [payload['action'] for payload in selected] == ['read_file','write_candidate','run_candidate','run_candidate','run_candidate','run_candidate']
    assert [payload['result_code'] for payload in rejected] == ['INVALID_ARGUMENT','UNKNOWN_CANDIDATE','UNKNOWN_CANDIDATE','UNKNOWN_CANDIDATE','UNKNOWN_CANDIDATE']
    assert [payload['result_code'] for payload in completed] == ['OK']
    assert all(payload['result_code'] in ACTION_RESULT_CODES for payload in rejected + completed)
    assert all(type(payload['steps_remaining']) is int and payload['steps_remaining'] >= 0 for payload in selected)
    assert selected[0]['steps_remaining'] == 5 and selected[0]['parameters_hash'] == canonical_hash(actions[0]['parameters'])
    assert all(len(payload['parameters_hash']) == 64 and 'duration' in payload for payload in rejected + completed)
    # Only bounded identifiers, hashes and codes enter events: the response body, the
    # raw arguments and the provider key stay out.
    recorded = event_dump(events)
    assert secret not in recorded and '../' + secret not in recorded
    assert '"parameters"' not in recorded and 'from example.parser import parse' not in recorded
    assert 'candidate-never-published' not in recorded


def test_protocol_error_events_use_codes_not_raw_response(tmp_path, projects, facts, monkeypatch):
    from reproagent.core.agent import PROTOCOL_ERROR_CODES
    secret = 'synthetic-provider-secret'
    monkeypatch.setenv('REPROAGENT_API_KEY', secret)
    body = {'name':'read_file','parameters':{'path':'example/parser.py','start':1,'end':1},
            'reasoning_content':secret,'error':{'message':secret}}
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedActions([body]), BudgetLimits(agent_steps=3))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    protocol = [event.payload for event in events if event.kind == 'protocol.error']
    assert len(protocol) == 1 and protocol[0]['result_code'] in PROTOCOL_ERROR_CODES
    assert protocol[0]['response_kind'] == 'action' and protocol[0]['steps_remaining'] == 0
    assert not [event for event in events if event.kind in ('action.selected','action.completed','action.rejected')]
    recorded = event_dump(events)
    assert secret not in recorded and 'reasoning_content' not in recorded and '"message"' not in recorded


def test_action_interrupted_by_budget_is_not_recorded_as_completed(tmp_path, projects, facts):
    from reproagent.core.budget import BudgetStopped
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    async def stopped(*args, **kwargs): raise BudgetStopped('EXHAUSTED', 'task time limit reached')
    controller.runner.execute = stopped
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    ends = [(event.kind, event.payload['action'], event.payload['result_code'])
            for event in events if event.kind in ('action.completed', 'action.rejected')]
    assert ends == [('action.completed', 'write_candidate', 'OK'), ('action.rejected', 'run_candidate', 'INTERRUPTED')]
