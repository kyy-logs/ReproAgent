import asyncio
import json
import pytest
from reproagent.core.models import BudgetLimits, TaskState, Verdict, VerificationIssue
from reproagent.core.budget import BudgetStopped
from tests.unit.test_controller import setup, publish, ask
from tests.unit.test_controller_execution_recovery import fault_sequence


def feedback_events(controller):
    events, errors = controller.store.read_events()
    assert not errors
    return [event for event in events if event.kind == 'verification.feedback']


def test_semantic_failure_reaches_next_phase_with_bound_evidence_and_decision(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    model.classification = 'NOT_REPRODUCED'
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    data = json.loads(controller.explorer.contexts[1].feedback)
    assert data['verdict']['issues'][0]['code'] == 'TARGET_TRIGGER_UNCONFIRMED'
    assert data['verdict']['issues'][0]['origin'] == 'provider_claim'
    assert data['decision'] == 'continue' and data['instruction']
    run_id = data['verdict']['run_ids'][0]
    saved = controller.store.load_record('verdicts', 'verdict-' + run_id)
    assert saved.issues[0].evidence_refs
    event = feedback_events(controller)[0]
    assert event.payload['diagnostic_codes'] == ['TARGET_TRIGGER_UNCONFIRMED']
    assert event.refs == ('verdicts/verdict-' + run_id + '.json',)


def test_timeout_continuation_and_stop_are_explicit_without_extra_semantic_calls(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), publish()])
    fault_sequence(controller, ['COMMAND_TIMEOUT', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED and result.stop_reason == 'REPEATED_COMMAND_TIMEOUT'
    events = feedback_events(controller)
    assert [event.payload['decision'] for event in events] == ['continue', 'stop']
    assert all(event.payload['diagnostic_codes'] == ['COMMAND_TIMEOUT'] for event in events)
    assert 'verdict' not in model.kinds


def test_replay_feedback_preserves_first_and_second_runs_and_single_observation(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    fault_sequence(controller, ['EXITED', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    data = json.loads(controller.explorer.contexts[1].feedback)
    assert data['result_code'] == 'REPLAY_UNCONFIRMED'
    assert data['previous_run_ids'] and data['previous_run_ids'] != data['verdict']['run_ids']
    assert data['verdict']['issues'][0]['code'] == 'COMMAND_TIMEOUT'
    assert result.evidence_level.value == 'SINGLE_OBSERVATION'


def test_minimal_feedback_capacity_stops_without_overwriting_saved_verdict(tmp_path, projects, facts):
    _, _, controller, _ = setup(tmp_path, projects, facts)
    ctx = facts.context(limits=BudgetLimits(tool_response_bytes=64))
    item = VerificationIssue('EXPECTATION_UNGROUNDED', 'expectation_is_grounded', 'program_check',
                             '无依据', recovery='revise_contract')
    verdict = Verdict(None, '未确认', run_ids=('example',), issues=(item,))
    controller.store.save_record('verdicts', 'verdict-example', verdict)
    with pytest.raises(BudgetStopped) as caught:
        controller.candidate_feedback(verdict, ctx, result_code='NOT_REPRODUCED')
    assert caught.value.reason == 'NEEDS_INFORMATION'
    assert controller.store.load_record('verdicts', 'verdict-example') == verdict


def test_advisory_observation_failure_cannot_change_business_verdict(tmp_path, projects, facts, monkeypatch):
    _, _, controller, ctx = setup(tmp_path, projects, facts)
    verdict = Verdict(None, 'no evidence', issues=(VerificationIssue('EXECUTION_EVIDENCE_MISSING',
        'execution_evidence_present', 'program_check', '无执行'),))
    append = controller.store.append_event

    def broken(kind, *args):
        if kind == 'verification.feedback':
            raise OSError('advisory sink failed')
        return append(kind, *args)

    monkeypatch.setattr(controller.store, 'append_event', broken)
    data = json.loads(controller.candidate_feedback(verdict, ctx, result_code='NOT_REPRODUCED'))
    assert data['verdict']['issues'][0]['code'] == 'EXECUTION_EVIDENCE_MISSING'


def test_terminal_stop_is_not_rewritten_by_unusable_optional_feedback(tmp_path, projects, facts):
    _, _, controller, _ = setup(tmp_path, projects, facts)
    ctx = facts.context(limits=BudgetLimits(tool_response_bytes=64))
    verdict = Verdict(None, 'timeout', issues=(VerificationIssue('COMMAND_TIMEOUT',
        'binding_and_integrity_ok', 'program_check', '超时', recovery='narrow_candidate'),))
    text = controller.candidate_feedback(verdict, ctx, result_code='REPEATED_COMMAND_TIMEOUT', decision='stop')
    assert text == ''
    event = feedback_events(controller)[0]
    assert event.payload['decision'] == 'stop' and event.payload['feedback_state'] == 'capacity_unavailable'


def test_replay_signature_mismatch_is_explained_in_next_phase(tmp_path, projects, facts):
    from dataclasses import replace
    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    execute = controller.runner.execute
    count = 0
    async def changed(*args, **kwargs):
        nonlocal count
        run = await execute(*args, **kwargs)
        count += 1
        if count == 2:
            tests = tuple(dict(test, assertion_comparison=None, crash={**test['crash'], 'message': 'different target failure'})
                          if test['outcome'] == 'failed' else test for test in run.observation.tests)
            run = replace(run, observation=replace(run.observation, tests=tests))
        return run
    controller.runner.execute = changed
    result = asyncio.run(controller.run(request, ctx))
    data = json.loads(controller.explorer.contexts[1].feedback)
    assert result.evidence_level.value == 'SINGLE_OBSERVATION'
    assert data['result_code'] == 'REPLAY_UNCONFIRMED'
    assert data['replay_unmet_checks'] == ['failure_signature_matches']
    assert data['previous_run_ids'] and data['verdict']['run_ids']


def test_replay_diagnostic_is_preserved_in_observation_event(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    fault_sequence(controller, ['EXITED', 'COMMAND_TIMEOUT'])
    asyncio.run(controller.run(request, ctx))
    event = feedback_events(controller)[0]
    assert event.payload['replay_unmet_checks'] == ['verdicts_reproduce_bound_candidate']
