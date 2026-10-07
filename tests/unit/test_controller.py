"""The Controller's business state machine, driven by scripted exploration phases.

The phases here are planned, not explored: a plan entry is what one phase decided, and the
scripted explorer publishes through the same product candidate service the SDK phase tools
use, so publication rules, contract binding and the snapshot are the real ones.  Every
phase also charges the one step a phase's own model request costs, because that is the
port's unit of work -- the Controller's execute and confirm steps are not exploration and
do not spend one.
"""
import asyncio
import importlib
import json
import sys
import time
from dataclasses import replace

from reproagent.core.budget import Budget, BudgetStopped
from reproagent.core.candidate_service import CandidateService
from reproagent.core.models import (BudgetLimits, CandidateDraft, DraftFile, EvidenceLevel, EvidenceRef,
    FixValidationRequest, ModelConfig, ModelResponse, PythonPytestConfig, RunContext, TaskRequest, TaskState)
from reproagent.core.phase import PhaseResult
from reproagent.core.protocol import ModelProtocolError
from reproagent.core.agent import ReproAgent
from reproagent.paths import relative_name

BUGGY = 'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n'
PASSING = 'from example.parser import parse\ndef test_nonempty(): assert parse([1]) == [1]\n'


class Clock:
    """A monotonic clock the test moves, so a deadline can be reached deliberately."""

    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class ScriptedModel:
    """The contract/verdict model. Exploration is planned by the phase script instead."""

    def __init__(self, missing=False, classification='REPRODUCED'):
        self.messages, self.kinds, self.missing, self.classification = [], [], missing, classification

    async def complete(self, request, context):
        self.messages.extend(request.messages)
        self.kinds.append(request.response_kind)
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            result = {'trigger':'empty list','expected':'' if self.missing else 'parse([]) returns []','reported_actual':'IndexError',
                'source_indices':[] if self.missing else [0], 'missing_information':['expected?'] if self.missing else [],
                'observable_checks':[],'assumptions':[]}
        elif request.response_kind == 'verdict':
            result = {'classification':self.classification,'reason':'reported IndexError on empty input','evidence_refs':data['available_refs'],
                'expected_assertion':True,'target_triggered':self.classification == 'REPRODUCED','failure_matches_issue':True}
        else:
            raise AssertionError(f'the Controller makes no {request.response_kind} request to the domain model')
        return ModelResponse(json.dumps(result))


class InvalidContract(ScriptedModel):
    """A contract analysis that never returns a usable contract."""

    def __init__(self, missing=False, classification='REPRODUCED'):
        super().__init__(missing, classification)
        self.analyses = 0

    async def complete(self, request, context):
        if request.response_kind == 'contract':
            self.analyses += 1
            self.messages.extend(request.messages)
            self.kinds.append(request.response_kind)
            if self.analyses > 1:
                return ModelResponse(json.dumps({'trigger':'empty list','expected':'parse([]) returns []'}))
        return await super().complete(request, context)


# ---------------------------------------------------------------------------
# The scripted exploration phase
# ---------------------------------------------------------------------------

def publish(path='tests/test_repro.py', content=BUGGY, hypothesis='empty input', role='test', extra=()):
    """One phase that publishes a candidate through the product's candidate service."""
    def step(explorer, context):
        files = ((path, content, role), *extra)
        draft = CandidateDraft(tuple(DraftFile(item, text.encode('utf-8'), item_role) for item, text, item_role in files),
                               explorer.service.snapshot.snapshot_id, context.contract.contract_id,
                               context.contract.version, hypothesis, expectation_sources=context.contract.sources)
        return PhaseResult('candidate', candidate_id=explorer.service.publish(draft).candidate_id)
    return step


def report(candidate_id):
    """One phase that reports a candidate id without publishing anything."""
    return lambda explorer, context: PhaseResult('candidate', candidate_id=_value(candidate_id))


def revise(source_refs, reason='cite the parser source'):
    """One phase that asks the Controller for a contract revision of those lines."""
    return lambda explorer, context: PhaseResult('revise_contract', source_refs=tuple(_value(source_refs)), reason=reason)


def _value(value):
    """A plan entry's argument, resolved when the phase runs if it is deferred."""
    return value() if callable(value) else value


def ask(question='Provide expected behavior'):
    """One phase that reports the information it is missing."""
    return lambda explorer, context: PhaseResult('request_information', question=question)


def nothing(reason='the phase ended without publishing a candidate'):
    """One phase that produced nothing the Controller may act on."""
    return lambda explorer, context: PhaseResult('no_candidate', reason=reason)


def fail(error):
    """One phase that fails instead of answering."""
    def step(explorer, context):
        raise error
    return step


def source_ref(explorer, path='example/parser.py', start=1, end=2):
    """A citation of a registered original file, as a phase tool would issue it.

    The citation names the file the way the rest of the task does -- relative to the task
    store, carrying the frozen hash -- which is exactly what the evidence ledger issues.
    """
    snapshot = explorer.service.snapshot
    entry = next(entry for entry in snapshot.files if entry.path == path)
    return EvidenceRef(relative_name(snapshot.root / path, explorer.service.workspace.root), entry.content_hash, start, end)


class ScriptedExplorer(ReproAgent):
    """The product strategy with phases planned by the test.

    It implements the same port as the SDK runtime: ``analyze`` is the product's contract
    analysis, each phase is one logical exploration request (and so spends the one step the
    runtime's guard would spend), publication goes through the phase's candidate service,
    and ``aclose`` is awaited once when the task is over.
    """

    def __init__(self, gateway, context, candidate_parent='tests', *, project, workspace, plan=(), close_error=None):
        super().__init__(gateway, context, candidate_parent)
        self.workspace, self.store = workspace, workspace.store
        self.service = CandidateService(project, workspace, context)
        self.plan, self.results, self.contexts = list(plan), [], []
        self.close_error, self.closed = close_error, False

    async def explore(self, context):
        self.service.bind_contract(context.contract)
        self.context.budget.take_step()
        self.contexts.append(context)
        step = self.plan.pop(0) if self.plan else nothing('the plan is exhausted')
        result = step(self, context)
        self.results.append(result)
        return result

    async def aclose(self):
        await asyncio.sleep(0)
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class PhasePlan:
    """Builds each task's scripted explorer and keeps it for the test's assertions."""

    def __init__(self, plan=()):
        self.plan, self.explorer, self.close_error = list(plan), None, None

    def __call__(self, gateway, context, candidate_parent, *, project, workspace):
        self.explorer = ScriptedExplorer(gateway, context, candidate_parent, project=project, workspace=workspace,
                                         plan=self.plan, close_error=self.close_error)
        return self.explorer


def controller_for(tmp_path, projects, facts, model, limits=None, plan=()):
    repo = projects.plain(tmp_path / 'repo')
    request = TaskRequest(repo, tmp_path / '任务', repo / 'issue.md', language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',)), limits=limits or BudgetLimits())
    app = importlib.import_module('reproagent.app')
    controller = app.create_controller(request, ModelConfig(), gateway=model, explorer_factory=PhasePlan(plan))
    return request, controller, facts.context(limits=request.limits)


def setup(tmp_path, projects, facts, limits=None, missing=False, plan=None):
    """One scripted task: the standard plan publishes the regression candidate."""
    plan = [publish()] if plan is None else list(plan)
    model = ScriptedModel(missing)
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, limits, plan)
    return request, model, controller, ctx


def event_dump(events): return json.dumps([{'kind': event.kind, 'payload': event.payload} for event in events])


def action_endings(events):
    return [(event.kind, event.payload['action'], event.payload['result_code'])
            for event in events if event.kind in ('action.completed', 'action.rejected')]


def phase_results(events):
    return [(event.payload['kind'], event.payload['result_code']) for event in events if event.kind == 'phase.result']


def verdict_records(request):
    return sorted((request.output_dir / 'verdicts').glob('*.json')) if (request.output_dir / 'verdicts').exists() else []


def test_candidate_is_run_and_confirmed_without_run_submit_decisions(tmp_path, projects, facts):
    """A phase result is acted on: execute, verify, repeat -- and no step for either."""
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.export_state == 'published'
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2
    assert len(verdict_records(request)) == 2
    assert (request.output_dir / 'artifacts/reproduction/replay.py').exists()
    # The phase is the only thing that spends exploration steps; executing, verifying and
    # repeating the candidate it published spend none.
    assert ctx.budget.steps_used == 1
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.completed', 'run_candidate', 'OK'),
                                      ('action.completed', 'submit_candidate', 'OK')]
    assert phase_results(events) == [('candidate', 'REPRODUCED')]
    assert model.kinds == ['contract', 'verdict', 'verdict']
    assert controller.explorer.closed


def test_confirmed_candidate_is_not_offered_back_to_the_phase(tmp_path, projects, facts):
    # The phase would stop the task with a question on its second turn; the candidate it
    # already published reproduced, so the Controller repeats it and delivers instead.
    request, model, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask('stop')])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert len(controller.explorer.results) == 1
    assert action_endings(controller.store.read_events()[0])[-1] == ('action.completed', 'submit_candidate', 'OK')


def test_a_non_reproducing_candidate_returns_to_the_phase_with_its_verdict(tmp_path, projects, facts):
    # The candidate is executed and verified, does not reproduce, and the next phase is
    # told so; only then does the task stop, and it stops for the phase's own reason.
    model = ScriptedModel(classification='NOT_REPRODUCED')
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, plan=[publish(), ask('revise it')])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'MISSING_INFORMATION'
    assert result.evidence_level == EvidenceLevel.NONE and not result.accepted_candidate_id
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 1
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    ending = controller.explorer.contexts[1]
    assert 'NOT_REPRODUCED' in ending.feedback


def test_deadline_after_first_observation_preserves_partial_evidence(tmp_path, projects, facts):
    """Executing and confirming do not spend exploration steps, so the deadline bounds them.

    The first execution and its semantic review are inside the deadline, the independent
    repeat is not: the task keeps the single observation it really made.
    """
    clock = Clock()
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(), plan=[publish()])
    ctx = RunContext(budget=Budget(BudgetLimits(task_timeout_seconds=10), clock=clock))
    original = controller.verifier.evaluate
    async def reviewed(*args, **kwargs):
        verdict = await original(*args, **kwargs)
        clock.now += 100
        return verdict
    controller.verifier.evaluate = reviewed
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    assert result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION and result.accepted_candidate_id
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 1
    assert len(verdict_records(request)) == 1
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    assert action_endings(controller.store.read_events()[0])[-1] == ('action.rejected', 'submit_candidate', 'INTERRUPTED')


def test_a_phase_loop_stops_at_the_exploration_step_budget(tmp_path, projects, facts):
    # A phase that keeps publishing candidates which do not reproduce cannot consume
    # unbounded work: each phase costs one exploration step, and the fourth is refused.
    model = ScriptedModel(classification='NOT_REPRODUCED')
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, BudgetLimits(agent_steps=3),
                                              plan=[publish(), publish(), publish(), publish()])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED and ctx.budget.steps_used == 3
    assert len(controller.explorer.results) == 3
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 3
    assert len(list((request.output_dir / 'candidates').glob('*/manifest.json'))) == 3
    assert not (request.output_dir / 'artifacts/reproduction').exists()


def test_a_candidate_published_with_the_last_step_is_still_confirmed(tmp_path, projects, facts):
    # The step budget bounds exploration, never the confirmation of what was explored.
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(), BudgetLimits(agent_steps=1),
                                              plan=[publish()])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and ctx.budget.steps_used == 1
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2


def test_revision_invalidates_old_candidate_and_requires_read_sources(tmp_path, projects, facts):
    """A revision re-derives the contract, and the version it left behind keeps nothing.

    The version-1 candidate was executed once and did not reproduce; the revision clears
    that evidence, and the phase that then reports the old candidate id is refused, so an
    accepted candidate cannot survive the contract version that produced it.
    """
    model = ScriptedModel(classification='NOT_REPRODUCED')
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, BudgetLimits(agent_steps=5))
    plan = PhasePlan()
    stale = {}

    def first(explorer, context):
        result = publish()(explorer, context)
        stale['candidate_id'] = result.candidate_id
        stale['ref'] = source_ref(explorer)
        return result

    plan.plan = [first, revise(lambda: [stale['ref']]), report(lambda: stale['candidate_id']), ask('stop')]
    controller.explorer_factory = plan
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert result.evidence_level == EvidenceLevel.NONE and not result.accepted_candidate_id
    # Two contract versions, one contract, and only the version-1 candidate's single run.
    contracts = [controller.store.load_record('contracts', path.stem) for path in (request.output_dir / 'contracts').glob('*.json')]
    assert {contract.version for contract in contracts} == {1, 2}
    assert len({contract.contract_id for contract in contracts}) == 1
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 1
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    events = controller.store.read_events()[0]
    assert phase_results(events) == [('candidate', 'NOT_REPRODUCED'), ('revise_contract', 'REVISION_REQUESTED'),
                                     ('candidate', 'STALE_CANDIDATE'), ('request_information', 'MISSING_INFORMATION')]
    assert action_endings(events) == [('action.completed', 'run_candidate', 'OK'),
                                      ('action.completed', 'revise_contract', 'OK')]
    # The phase that asked for the revision is told the new version and what is still missing.
    assert json.loads(controller.explorer.contexts[2].feedback)['contract_version'] == 2


def test_revision_may_cite_only_original_evidence(tmp_path, projects, facts):
    # A revision that cites something the task never verified is refused with its own code,
    # and the contract the task already has stays the one in force.
    forged = EvidenceRef('example/parser.py', '0' * 64, 1, 2)
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(),
                                              plan=[revise([forged]), ask('stop')])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert phase_results(controller.store.read_events()[0]) == [('revise_contract', 'UNAUTHORIZED_EVIDENCE_REF'),
                                                                ('request_information', 'MISSING_INFORMATION')]
    assert len(list((request.output_dir / 'contracts').glob('*.json'))) == 1


def test_revision_that_cannot_be_grounded_keeps_the_previous_contract(tmp_path, projects, facts):
    # The revision analysis fails its protocol: the task fails with that code, and the
    # failed step is recorded as a rejected revision rather than a completed one.
    request, controller, ctx = controller_for(tmp_path, projects, facts, InvalidContract(), BudgetLimits(agent_steps=4))
    plan = PhasePlan()
    plan.plan = [revise(lambda: [source_ref(plan.explorer)])]
    controller.explorer_factory = plan
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.FAILED and result.stop_reason == 'MODEL_PROTOCOL_ERROR'
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.rejected', 'revise_contract', 'MODEL_PROTOCOL_ERROR')]
    assert all(code in importlib.import_module('reproagent.core.controller').ACTION_RESULT_CODES
               for _, _, code in action_endings(events))


def test_an_unconfirmed_replay_returns_to_the_phase_without_a_package(tmp_path, projects, facts):
    # The candidate reproduced twice, but the two observations do not confirm each other:
    # the task keeps the single observation it verified and asks the phase again.
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(),
                                              plan=[publish(), ask('the replay did not confirm it')])
    def unconfirmed(*args, **kwargs): return False
    controller.verifier.confirm = unconfirmed
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.completed', 'run_candidate', 'OK'),
                                      ('action.rejected', 'submit_candidate', 'REPLAY_UNCONFIRMED')]
    assert phase_results(events) == [('candidate', 'REPLAY_UNCONFIRMED'), ('request_information', 'MISSING_INFORMATION')]
    assert 'did not confirm' in controller.explorer.contexts[1].feedback


def test_a_revision_that_exceeds_the_context_budget_is_refused(tmp_path, projects, facts):
    # A revision whose cited sources do not fit the context budget is refused rather than
    # sent: the phase is told to narrow the range, and the contract in force is unchanged.
    plan = PhasePlan()
    # The issue itself fits the budget; the revision, which must carry it and the cited
    # lines, does not.
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(),
                                              BudgetLimits(tool_response_bytes=100))
    plan.plan = [revise(lambda: [source_ref(plan.explorer)]), ask('stop')]
    controller.explorer_factory = plan
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert phase_results(controller.store.read_events()[0]) == [('revise_contract', 'REVISION_BUDGET_EXCEEDED'),
                                                                ('request_information', 'MISSING_INFORMATION')]
    assert len(list((request.output_dir / 'contracts').glob('*.json'))) == 1
    assert 'narrow line ranges' in controller.explorer.contexts[1].feedback


def test_a_session_that_cannot_be_closed_is_recorded_not_raised(tmp_path, projects, facts):
    # Closing the task's phase session happens before delivery, and a session that fails to
    # close is recorded as a cleanup failure: it never rewrites the conclusion the run
    # already reached, and it never becomes an unrelated task outcome.
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    controller.explorer_factory.close_error = RuntimeError('the SDK session would not close')
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.export_state == 'published'
    events, errors = controller.store.read_events()
    assert not errors
    assert [event.payload['result_code'] for event in events if event.kind == 'explorer.cleanup_failed'] == \
        ['CLEANUP_FAILED']
    assert controller.explorer.closed


def test_unknown_candidate_id_is_refused_and_answered_with_feedback(tmp_path, projects, facts):
    model = ScriptedModel()
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, BudgetLimits(agent_steps=3),
                                              plan=[report('candidate-never-published'), report('candidate-never-published'),
                                                    publish()])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE
    events, errors = controller.store.read_events()
    assert not errors
    assert phase_results(events)[:2] == [('candidate', 'UNKNOWN_CANDIDATE')] * 2
    assert 'task store does not hold' in controller.explorer.contexts[1].feedback
    # The rejected ids never enter an event: only the fixed code does.
    assert 'candidate-never-published' not in event_dump(events)


def test_missing_information_and_environment_block_have_diagnostic_packages(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path / 'missing', projects, facts, missing=True, plan=[ask()])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert result.stop_reason == 'MISSING_INFORMATION' and 'Provide expected behavior' in result.uncertainties
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    request, _, controller, ctx = setup(tmp_path / 'blocked', projects, facts)
    request = replace(request, language=replace(request.language, python=str(tmp_path / 'no-python.exe')))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()


def test_a_phase_that_reports_nothing_stops_with_its_own_diagnostic(tmp_path, projects, facts):
    # An SDK phase that answers in prose published nothing: no candidate, no success, and
    # the task is not asked to keep replying forever.
    request, _, controller, ctx = setup(tmp_path / 'silent', projects, facts,
                                        plan=[nothing('the phase answered without publishing')])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'NO_CANDIDATE'
    assert 'the phase answered without publishing' in result.uncertainties
    assert not list((request.output_dir / 'candidates').glob('*/manifest.json'))
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    assert len(controller.explorer.results) == 1


def test_fixed_validation_is_separate_and_same_candidate(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / 'fixed-secret-location')
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed, sys.executable)))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.DIFFERENTIAL_VALIDATED
    runs = [controller.store.load_record('runs', p.parent.name) for p in (request.output_dir / 'runs').glob('*/execution.json')]
    assert sorted(r.execution_role for r in runs) == ['fixed', 'original', 'original']
    assert len({r.candidate_id for r in runs}) == 1
    # The hidden fixed version never reaches the model and never reaches a phase.
    assert str(fixed) not in json.dumps(model.messages)
    assert all(str(fixed) not in json.dumps(context.contract, default=str) for context in controller.explorer.contexts)
    assert all(str(fixed) not in json.dumps(context.feedback) for context in controller.explorer.contexts)


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
    # The evaluation keeps the repeated observation visible, but this case is not an
    # effective reproduction: the candidate was reproduced, human-confirmed and replayed,
    # and the fixed version still did not pass, so the effective success count is zero.
    from evals.run import summarize
    from evals.schema import EvalResult
    summary = summarize((EvalResult('pallets__flask-4992', status='DONE', evidence_level='REPEATED_OBSERVATION',
        reproduced=True, human_judgement=True, export_replayed=True,
        fix_validation_status=result.fix_validation_status),))
    assert summary['effective_reproductions'] == 0 and summary['total_rate'] == 0.0
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


def test_cancel_waits_for_runner_cleanup_and_explorer_close(tmp_path, projects, facts):
    """The cancellation path still reaps the run and still closes the task's phase session."""
    # A cancelled execution keeps its own reason and is recorded as interrupted, never as
    # a completed step; the cleanup failure above still outranks it.
    request, _, controller, ctx = setup(tmp_path / 'cancelled', projects, facts)
    original = controller.runner.execute
    async def cancelled(*args, **kwargs):
        execution = await original(*args, **kwargs)
        ctx.cancel_event.set()
        return replace(execution, raw=replace(execution.raw, stop_reason='CANCELLED'))
    controller.runner.execute = cancelled
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.CANCELLED and (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    events, _ = controller.store.read_events()
    assert action_endings(events)[-1] == ('action.rejected', 'run_candidate', 'INTERRUPTED')
    assert not list((request.output_dir / 'verdicts').glob('*.json'))
    # The phase session is awaited closed before anything is delivered, and twice over:
    # once for the cancelled run above and once for this one.
    assert controller.explorer.closed

    request, _, controller, ctx = setup(tmp_path / 'cleanup', projects, facts)
    original = controller.runner.execute
    async def failing(*args, **kwargs):
        execution = await original(*args, **kwargs)
        ctx.cancel_event.set()
        return replace(execution, raw=replace(execution.raw, cleanup_ok=False, stop_reason='CANCELLED'))
    controller.runner.execute = failing
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.FAILED and result.stop_reason == 'CANCELLED'
    assert controller.explorer.closed


def test_action_interrupted_by_budget_is_not_recorded_as_completed(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    async def stopped(*args, **kwargs): raise BudgetStopped('EXHAUSTED', 'task time limit reached')
    controller.runner.execute = stopped
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.rejected', 'run_candidate', 'INTERRUPTED')]
    # The phase session is closed even when the interruption is an exhausted budget.
    assert controller.explorer.closed


def test_model_output_failure_in_a_business_step_keeps_its_own_code(tmp_path, projects, facts):
    # The provider failure is injected at the verifier boundary, so each executed candidate
    # is answered with feedback and its own code until the exploration budget runs out.
    from reproagent.core.protocol import ModelOutputError
    model = ScriptedModel()
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, BudgetLimits(agent_steps=3),
                                              plan=[publish(), publish(), publish()])
    async def unavailable(*args, **kwargs): raise ModelOutputError('provider response is not a JSON object')
    controller.verifier.evaluate = unavailable
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    events, errors = controller.store.read_events()
    assert not errors
    assert action_endings(events) == [('action.rejected', 'run_candidate', 'MODEL_OUTPUT_ERROR')] * 3
    assert phase_results(events) == [('candidate', 'MODEL_OUTPUT_ERROR')] * 3


def test_phase_protocol_failure_keeps_its_own_code_and_no_raw_text(tmp_path, projects, facts):
    from reproagent.core.controller import ACTION_RESULT_CODES
    secret = 'synthetic-provider-secret'
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(), BudgetLimits(agent_steps=3))
    controller.explorer_factory.plan = [fail(ModelProtocolError(f'UNKNOWN_TOOL: {secret}'))]
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.FAILED and result.stop_reason == 'MODEL_PROTOCOL_ERROR'
    events, errors = controller.store.read_events()
    assert not errors
    errors = [event.payload for event in events if event.kind == 'phase.error']
    assert [payload['result_code'] for payload in errors] == ['MODEL_PROTOCOL_ERROR']
    assert all(payload['result_code'] in ACTION_RESULT_CODES for payload in errors)
    assert not [event for event in events if event.kind in ('action.selected', 'action.completed', 'action.rejected')]
    assert secret not in event_dump(events) and 'UNKNOWN_TOOL' not in event_dump(events)


def test_phase_error_leaves_the_phase_step_to_the_controller(tmp_path, projects, facts):
    # A phase that refuses itself before answering is answered with feedback instead of a
    # new phase result: the Controller records its own code and asks again.
    request, controller, ctx = controller_for(tmp_path, projects, facts, ScriptedModel(), BudgetLimits(agent_steps=2),
                                             plan=[fail(ValueError('the issue cannot fit the configured budget')), ask('stop')])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'MISSING_INFORMATION'
    events, errors = controller.store.read_events()
    assert not errors
    assert [event.payload['result_code'] for event in events if event.kind == 'phase.error'] == ['INVALID_ARGUMENT']
    assert phase_results(events) == [('request_information', 'MISSING_INFORMATION')]


def test_phase_result_and_step_events_use_codes_not_raw_response(tmp_path, projects, facts, monkeypatch):
    from reproagent.core.controller import PHASE_RESULT_CODES
    secret = 'synthetic-provider-secret'
    monkeypatch.setenv('REPROAGENT_API_KEY', secret)
    carrying = 'from example.parser import parse\ndef test_empty(): assert parse([]) == []  # ' + secret + '\n'
    model = ScriptedModel(classification='NOT_REPRODUCED')
    request, controller, ctx = controller_for(tmp_path, projects, facts, model, BudgetLimits(agent_steps=3),
        plan=[publish(content=carrying), report('candidate-never-published'), publish(content=carrying)])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED and ctx.budget.steps_used == 3
    events, errors = controller.store.read_events()
    assert not errors
    selected = [event.payload for event in events if event.kind == 'action.selected']
    completed = [event.payload for event in events if event.kind == 'action.completed']
    rejected = [event.payload for event in events if event.kind == 'action.rejected']
    assert [payload['action'] for payload in selected] == ['run_candidate', 'run_candidate']
    assert [payload['result_code'] for payload in completed] == ['OK', 'OK'] and not rejected
    assert phase_results(events) == [('candidate', 'NOT_REPRODUCED'), ('candidate', 'UNKNOWN_CANDIDATE'),
                                     ('candidate', 'NOT_REPRODUCED')]
    assert all(type(payload['steps_remaining']) is int and payload['steps_remaining'] >= 0 for payload in selected)
    assert all(len(payload['parameters_hash']) == 64 and 'duration' in payload for payload in completed + rejected)
    # Only bounded identifiers, hashes and codes enter events: the candidate's content, the
    # provider key and the id a phase invented stay out.
    recorded = event_dump(events)
    assert secret not in recorded and 'from example.parser import parse' not in recorded
    assert 'candidate-never-published' not in recorded
    assert all(payload['result_code'] in PHASE_RESULT_CODES
               for payload in rejected + [event.payload for event in events if event.kind == 'phase.result'])
