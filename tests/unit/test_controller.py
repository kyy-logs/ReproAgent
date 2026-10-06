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


class ScriptedRevision:
    """Reads one source, then revises the contract with a model that never returns a valid contract."""
    def __init__(self): self.contracts = 0; self.messages = []
    async def complete(self, request, context):
        self.messages.extend(request.messages)
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            self.contracts += 1
            if self.contracts > 1: return ModelResponse(json.dumps({'trigger':'empty list','expected':'parse([]) returns []'}))
            return ModelResponse(json.dumps({'trigger':'empty list','expected':'parse([]) returns []','reported_actual':'IndexError',
                'source_indices':[0],'missing_information':[],'observable_checks':[],'assumptions':[]}))
        refs = json.loads(data['feedback'])['evidence_refs'] if data['feedback'] else []
        action = ({'name':'read_file','parameters':{'path':'example/parser.py','start':1,'end':2}} if not refs
                  else {'name':'revise_contract','parameters':{'source_refs':list(refs),'reason':'narrow the cited lines'}})
        return ModelResponse(json.dumps(action))


class ScriptedCandidateRun:
    """Publishes a candidate, then keeps running it."""
    def __init__(self): self.messages = []
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
            candidate_id = (data['candidate_ids'] or [''])[0]
            result = ({'name':'run_candidate','parameters':{'candidate_id':candidate_id}} if candidate_id else
                {'name':'write_candidate','parameters':{'files':[{'path':'tests/test_repro.py','role':'test',
                    'content':'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n'}],'hypothesis':'empty input'}})
        return ModelResponse(json.dumps(result))


def read_action(data=None):
    return {'name':'read_file','parameters':{'path':'example/parser.py','start':1,'end':2}}


def write_action(data=None):
    return {'name':'write_candidate','parameters':{'files':[{'path':'tests/test_repro.py','role':'test',
        'content':'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n'}],'hypothesis':'empty input'}}


def write_passing_action(data=None):
    return {'name':'write_candidate','parameters':{'files':[{'path':'tests/test_repro.py','role':'test',
        'content':'from example.parser import parse\ndef test_nonempty(): assert parse([1]) == [1]\n'}],'hypothesis':'nonempty input'}}


def run_action(data):
    return {'name':'run_candidate','parameters':{'candidate_id':data['candidate_ids'][0]}}


def submit_action(data):
    return {'name':'submit_candidate','parameters':{'candidate_id':data['candidate_ids'][0]}}


def revise_action(data):
    return {'name':'revise_contract','parameters':{'source_refs':json.loads(data['feedback'])['evidence_refs'],'reason':'cite the parser source'}}


class ScriptedLifecycle:
    """A grounded contract and a scripted verdict; subclasses script only the actions."""
    classification = 'REPRODUCED'

    def __init__(self): self.messages, self.kinds = [], []

    def reply(self, request, data): raise NotImplementedError

    async def complete(self, request, context):
        self.messages.extend(request.messages)
        self.kinds.append(request.response_kind)
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            result = {'trigger':'empty list','expected':'parse([]) returns []','reported_actual':'IndexError','source_indices':[0],
                      'missing_information':[],'observable_checks':[],'assumptions':[]}
        elif request.response_kind == 'verdict':
            result = {'classification':self.classification,'reason':'reported IndexError on empty input','evidence_refs':data['available_refs'],
                      'expected_assertion':True,'target_triggered':self.classification == 'REPRODUCED','failure_matches_issue':True}
        else:
            result = self.reply(request, data)
        return ModelResponse(json.dumps(result))


class ScriptedSequence(ScriptedLifecycle):
    """Runs one scripted action per decision, repeating the last one when it runs dry."""
    def __init__(self, sequence, classification='REPRODUCED'):
        super().__init__()
        self.sequence, self.step, self.classification = list(sequence), 0, classification

    def reply(self, request, data):
        action = self.sequence[min(self.step, len(self.sequence) - 1)]
        self.step += 1
        return action(data)


class ScriptedReadLoop(ScriptedLifecycle):
    """Publishes one candidate, then reads forever instead of running or submitting it."""
    def reply(self, request, data):
        return write_action() if not data['candidate_ids'] else read_action()


class ScriptedEscape(ScriptedLifecycle):
    """Publishes one candidate, then tries to end the task with a question."""
    def reply(self, request, data):
        return write_action() if not data['candidate_ids'] else {'name':'request_information','parameters':{'question':'stop'}}


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
    events, _ = controller.store.read_events()
    assert action_endings(events)[-1] == ('action.rejected', 'run_candidate', 'CLEANUP_FAILED')


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


def test_repeated_failure_with_failed_fix_is_not_differential_success(tmp_path, projects, facts):
    # The candidate fails twice on the original version, so the task still finishes; the
    # supplied fixed version runs the same candidate and fails too. The pipeline status
    # stays DONE and the repeated observation stays visible, but neither the report nor
    # the evaluation may present this as a differential success.
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    still_broken = projects.plain(tmp_path / 'fixed-version-still-broken')
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(still_broken, sys.executable)))
    assert result.status == TaskState.DONE and result.export_state == 'published'
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert result.fix_validation_status == 'failed'
    package = request.output_dir / 'artifacts/reproduction'
    report = json.loads((package / 'report.json').read_text(encoding='utf-8'))
    assert report['fix_validation_status'] == 'failed' and report['verified'] is True
    text = (package / 'report.md').read_text(encoding='utf-8')
    assert '仅确认原版重复失败，差分复现未成立' in text
    assert '修复版对照通过' not in text
    # The hidden fixed version still never reaches the explorer.
    assert str(still_broken) not in json.dumps(model.messages)
    # The evaluation keeps the repeated observation but counts zero differential successes.
    from evals.run import summarize
    from evals.schema import EvalResult
    summary = summarize((EvalResult('pallets__flask-4992', status='DONE', evidence_level='REPEATED_OBSERVATION',
        reproduced=True, fix_validation_status=result.fix_validation_status),))
    assert summary['differential_successes'] == 0 and summary['fix_validation_failed'] == 1


def test_no_fixed_version_preserves_buggy_only_workflow(tmp_path, projects, facts):
    # The normal product workflow supplies no fixed version: the repeated original
    # observation is still delivered, and the fixed-version status says so instead of
    # inventing a differential claim.
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert result.fix_validation_status == 'not_provided'
    runs = [controller.store.load_record('runs', path.parent.name) for path in (request.output_dir / 'runs').glob('*/execution.json')]
    assert {run.execution_role for run in runs} == {'original'}
    package = request.output_dir / 'artifacts/reproduction'
    report = json.loads((package / 'report.json').read_text(encoding='utf-8'))
    assert report['verified'] is True and report['fix_validation_status'] == 'not_provided'
    text = (package / 'report.md').read_text(encoding='utf-8')
    assert '已确认问题版的重复观察；未获得修复版通过的差异验证。' in text
    assert '差分复现未成立' not in text
    from evals.schema import EvalResult
    assert EvalResult('case').fix_validation_status == 'not_provided'


def test_legacy_record_without_fix_status_can_be_read(tmp_path):
    # Records written before this field existed must keep loading, and a freshly created
    # result must default to the same value: no fixed version was provided.
    from reproagent.core.models import TaskResult
    from reproagent.store import TaskStore
    root = tmp_path / 'legacy'
    root.mkdir()
    (root / 'task.json').write_text(json.dumps({'schema_version': 1, 'task_id': 'pallets__flask-4992',
        'status': 'DONE', 'stop_reason': '', 'evidence_level': 'REPEATED_OBSERVATION', 'export_state': 'published',
        'accepted_candidate_id': 'candidate-1', 'uncertainties': ['Fixed-version validation failed or was incompatible.'],
        'duration': 3.5}), encoding='utf-8')
    legacy = TaskStore(root).load_record('task', 'task')
    assert legacy.fix_validation_status == 'not_provided'
    assert legacy.status == TaskState.DONE and legacy.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert TaskResult('fresh', TaskState.PREPARING).fix_validation_status == 'not_provided'


def event_dump(events): return json.dumps([{'kind': event.kind, 'payload': event.payload} for event in events])


def action_endings(events):
    return [(event.kind, event.payload['action'], event.payload['result_code'])
            for event in events if event.kind in ('action.completed', 'action.rejected')]


def test_pending_candidate_forces_run_without_model_decision(tmp_path, projects, facts):
    # The model writes one candidate and then only ever offers to read. The freshly
    # published candidate is still executed and submitted, each for one step.
    model = ScriptedReadLoop()
    request, controller, ctx = controller_for(tmp_path, projects, facts, model)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert model.kinds == ['contract', 'action', 'verdict', 'verdict']
    assert ctx.budget.steps_used == 3
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.completed','write_candidate','OK'), ('action.completed','run_candidate','OK'),
        ('action.completed','submit_candidate','OK')]
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2


def test_forced_action_does_not_bypass_cancellation(tmp_path, projects, facts):
    class CancellingReads(ScriptedReadLoop):
        async def complete(self, request, context):
            response = await super().complete(request, context)
            if request.response_kind == 'action':
                context.cancel_event.set()
            return response
    request, controller, ctx = controller_for(tmp_path, projects, facts, CancellingReads())
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.CANCELLED
    assert not list((request.output_dir / 'runs').glob('*/execution.json'))


def test_reproduced_candidate_forces_submit_without_model_decision(tmp_path, projects, facts):
    # After the REPRODUCED verdict the model would stop the task with a question;
    # the Controller submits the reproduced candidate first, so the task is exported.
    model = ScriptedEscape()
    request, controller, ctx = controller_for(tmp_path, projects, facts, model)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert model.kinds == ['contract', 'action', 'verdict', 'verdict']
    assert action_endings(controller.store.read_events()[0])[-1] == ('action.completed','submit_candidate','OK')


def test_two_remaining_steps_do_not_allow_new_unrunnable_candidate(tmp_path, projects, facts):
    # Two steps left is enough for a write and the forced run it triggers: the new
    # candidate is executed inside the budget instead of being left unrunnable.
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(), BudgetLimits(agent_steps=2))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 1
    assert result.accepted_candidate_id and result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION
    # Both steps went into writing and running: nothing was spent on a read instead.
    assert ctx.budget.steps_used == 2


def test_missing_facts_still_allow_information(tmp_path, projects, facts):
    model = ScriptedModel(missing=True)
    request, controller, ctx = controller_for(tmp_path, projects, facts, model)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    payload = json.loads(model.messages[-1]['content'])
    assert 'write_candidate' not in payload['allowed_actions']
    assert {'request_information','revise_contract','read_file'} <= set(payload['allowed_actions'])
    assert [event.payload['action'] for event in controller.store.read_events()[0]
            if event.kind == 'action.selected'] == ['request_information']


def test_revised_contract_cannot_submit_stale_candidate(tmp_path, projects, facts):
    # The candidate passes on the original snapshot, so it carries no reproduced
    # verdict and the contract is revised from the parser source instead.
    model = ScriptedSequence([write_passing_action, read_action, revise_action, submit_action,
        lambda data: {'name':'request_information','parameters':{'question':'stop'}}])
    request, controller, ctx = controller_for(tmp_path, projects, facts, model)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    # Nothing was replayed or exported from the version 1 candidate, and the version
    # 2 contract left the choice to the explorer instead of forcing it.
    assert result.evidence_level == EvidenceLevel.NONE and not result.accepted_candidate_id
    assert len(list((request.output_dir / 'contracts').glob('*.json'))) == 2
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 1
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    events = controller.store.read_events()[0]
    assert [event.payload['action'] for event in events if event.kind == 'action.selected'] == ['write_candidate',
        'run_candidate', 'read_file', 'revise_contract', 'submit_candidate', 'request_information']
    assert action_endings(events) == [('action.completed','write_candidate','OK'), ('action.completed','run_candidate','OK'),
        ('action.completed','read_file','OK'), ('action.completed','revise_contract','OK'),
        ('action.rejected','submit_candidate','CANDIDATE_NOT_REPRODUCED'), ('action.completed','request_information','OK')]


def test_same_action_and_result_twice_cannot_consume_all_twenty_steps(tmp_path, projects, facts):
    model = ScriptedSequence([read_action, read_action, read_action, write_action])
    request, controller, ctx = controller_for(tmp_path, projects, facts, model)
    result = asyncio.run(controller.run(request, ctx))
    # The third identical read is refused, so the model moves on: two reads, one
    # refused attempt, then write/run/submit instead of twenty identical reads.
    assert result.status == TaskState.DONE and ctx.budget.steps_used == 6
    selected = [event.payload['action'] for event in controller.store.read_events()[0] if event.kind == 'action.selected']
    assert selected == ['read_file','read_file','write_candidate','run_candidate','submit_candidate']
    # The refused third read was answered with feedback instead of another attempt.
    assert 'same result twice' in json.dumps(model.messages)


def test_state_change_allows_repeating_a_blocked_read(tmp_path, projects, facts):
    model = ScriptedSequence([read_action, read_action, read_action, write_action, read_action,
        lambda data: {'name':'request_information','parameters':{'question':'stop'}}], classification='NOT_REPRODUCED')
    request, controller, ctx = controller_for(tmp_path, projects, facts, model)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    events, errors = controller.store.read_events()
    assert not errors
    selected = [event.payload for event in events if event.kind == 'action.selected']
    assert [payload['action'] for payload in selected] == ['read_file','read_file','write_candidate','run_candidate',
        'read_file','request_information']
    # The identical read arguments are legal again once the candidate set changed.
    assert selected[0]['parameters_hash'] == selected[4]['parameters_hash']
    assert action_endings(events)[-2:] == [('action.completed','read_file','OK'), ('action.completed','request_information','OK')]


def test_action_error_events_use_codes_not_raw_response(tmp_path, projects, facts, monkeypatch):
    from reproagent.core.controller import ACTION_RESULT_CODES
    from reproagent.core.serialization import canonical_hash
    secret = 'synthetic-provider-secret'
    monkeypatch.setenv('REPROAGENT_API_KEY', secret)
    bad_read = {'name':'read_file','parameters':{'path':f'../{secret}','start':1,'end':2}}
    secret_candidate = {'name':'write_candidate','parameters':{'files':[{'path':'tests/test_repro.py','role':'test',
        'content':f'from example.parser import parse\ndef test_nonempty(): assert parse([1]) == [1]  # {secret}\n'}],'hypothesis':'empty input'}}
    unknown_run = {'name':'run_candidate','parameters':{'candidate_id':'candidate-never-published'}}
    model = ScriptedSequence([lambda data: bad_read, lambda data: secret_candidate, lambda data: unknown_run],
        classification='NOT_REPRODUCED')
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, BudgetLimits(agent_steps=6))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    selected = [event.payload for event in events if event.kind == 'action.selected']
    completed = [event.payload for event in events if event.kind == 'action.completed']
    rejected = [event.payload for event in events if event.kind == 'action.rejected']
    # The published candidate is run by the Controller; the two identical attempts to
    # run a candidate that was never published are still refused with their own code.
    assert [payload['action'] for payload in selected] == ['read_file','write_candidate','run_candidate','run_candidate','run_candidate']
    assert [payload['result_code'] for payload in rejected] == ['INVALID_ARGUMENT','UNKNOWN_CANDIDATE','UNKNOWN_CANDIDATE']
    assert [payload['result_code'] for payload in completed] == ['OK','OK']
    assert all(payload['result_code'] in ACTION_RESULT_CODES for payload in rejected + completed)
    assert all(type(payload['steps_remaining']) is int and payload['steps_remaining'] >= 0 for payload in selected)
    assert selected[0]['steps_remaining'] == 5 and selected[0]['parameters_hash'] == canonical_hash(bad_read['parameters'])
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
    assert action_endings(events) == [('action.completed', 'write_candidate', 'OK'), ('action.rejected', 'run_candidate', 'INTERRUPTED')]


def test_model_protocol_failure_in_an_action_keeps_its_own_code(tmp_path, projects, facts):
    from reproagent.core.controller import ACTION_RESULT_CODES
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedRevision(), BudgetLimits(agent_steps=6))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.FAILED and result.stop_reason == 'MODEL_PROTOCOL_ERROR'
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.completed', 'read_file', 'OK'), ('action.rejected', 'revise_contract', 'MODEL_PROTOCOL_ERROR')]
    assert all(code in ACTION_RESULT_CODES for _, _, code in action_endings(events))


def test_model_output_failure_in_an_action_keeps_its_own_code(tmp_path, projects, facts):
    # The provider failure is injected at the verifier boundary: ReproAgent and
    # Verifier convert provider failures themselves, and this asserts how the
    # Controller classifies one that reaches its action handler.
    from reproagent.core.protocol import ModelOutputError
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedCandidateRun(), BudgetLimits(agent_steps=3))
    async def unavailable(*args, **kwargs): raise ModelOutputError('provider response is not a JSON object')
    controller.verifier.evaluate = unavailable
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.completed', 'write_candidate', 'OK'),
        ('action.rejected', 'run_candidate', 'MODEL_OUTPUT_ERROR'), ('action.rejected', 'run_candidate', 'MODEL_OUTPUT_ERROR')]
