import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.models import TaskState, EvidenceLevel, FixValidationRequest, ProtectionCheck
from tests.unit.test_controller import setup, publish, ask, revise, source_ref, PhasePlan


def fault_sequence(controller, codes):
    execute = controller.runner.backend.execute
    seen = []
    async def wrapped(spec, context):
        raw = await execute(spec, context)
        if not spec.candidate_id:
            return raw
        index = len(seen)
        seen.append(spec)
        code = codes[index] if index < len(codes) else 'EXITED'
        if code != 'EXITED':
            # Inject before Runner saves its immutable record; retain only setup evidence.
            probe = raw.probe_artifacts.path
            kept = []
            for line in probe.read_bytes().splitlines(keepends=True):
                kept.append(line)
                event = json.loads(line)
                if event['event'] == 'test_phase' and event['payload']['when'] == 'setup':
                    break
            probe.write_bytes(b''.join(kept))
            raw = replace(raw, stop_reason=code, exit_code=None, duration=60.01,
                          cleanup_ok=code != 'BAD_CLEANUP')
        return raw
    controller.runner.backend.execute = wrapped
    return seen


def test_first_timeout_provides_actionable_feedback_then_next_candidate_can_finish(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts,
        plan=[publish(), publish(hypothesis='simpler candidate')])
    seen = fault_sequence(controller, ['COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and len(seen) == 3
    feedback = json.loads(controller.explorer.contexts[1].feedback)
    assert feedback['result_code'] == 'COMMAND_TIMEOUT'
    assert feedback['verdict']['execution_diagnostic']['last_test_stage'] == 'setup:passed'
    assert feedback['consecutive_command_timeouts'] == 1
    # Both original executions are still verified; equivalent material reuses the
    # first semantic approval instead of making a second provider request.
    assert model.kinds.count('verdict') == 1
    verdicts = [controller.store.load_record('verdicts', path.stem)
                for path in (request.output_dir / 'verdicts').glob('*.json')]
    assert sum(v.classification is not None and v.classification.value == 'REPRODUCED'
               for v in verdicts) == 2


def test_two_different_timeout_candidates_stop_before_third_execution(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts, plan=[
        publish(), publish(content='from example.parser import parse\ndef test_other(): assert parse([]) == []\n'), publish()])
    seen = fault_sequence(controller, ['COMMAND_TIMEOUT', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED
    assert result.stop_reason == 'REPEATED_COMMAND_TIMEOUT'
    assert len(seen) == 2 and len(controller.explorer.contexts) == 2
    assert result.evidence_level == EvidenceLevel.NONE and 'verdict' not in model.kinds
    assert len(list((request.output_dir / 'verdicts').glob('*.json'))) == 2


def test_non_timeout_execution_resets_streak(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts,
        plan=[publish(), publish(hypothesis='second'), publish(hypothesis='third'), ask()])
    model.classification = 'NOT_REPRODUCED'
    seen = fault_sequence(controller, ['COMMAND_TIMEOUT', 'EXITED', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and len(seen) == 3
    feedback = json.loads(controller.explorer.contexts[3].feedback)
    assert feedback['consecutive_command_timeouts'] == 1


def test_contract_revision_does_not_reset_execution_streak(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    plan = PhasePlan()
    plan.plan = [publish(), revise(lambda: [source_ref(plan.explorer)]), publish(), publish()]
    controller.explorer_factory = plan
    seen = fault_sequence(controller, ['COMMAND_TIMEOUT', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED and result.stop_reason == 'REPEATED_COMMAND_TIMEOUT'
    assert len(seen) == 2 and plan.explorer.contexts[-1].contract.version == 2


@pytest.mark.parametrize('code,status', [('CANCELLED', TaskState.CANCELLED), ('EXHAUSTED', TaskState.EXHAUSTED),
                                       ('BAD_CLEANUP', TaskState.FAILED)])
def test_cancellation_and_cleanup_out_rank_retry_policy(tmp_path, projects, facts, code, status):
    request, _, controller, ctx = setup(tmp_path, projects, facts,
        plan=[publish(), publish(hypothesis='next')])
    seen = fault_sequence(controller, ['COMMAND_TIMEOUT', code])
    result = asyncio.run(controller.run(request, ctx))
    assert len(seen) == 2 and result.status == status
    assert result.stop_reason != 'REPEATED_COMMAND_TIMEOUT'


def test_replay_timeout_keeps_specific_diagnostic_and_single_observation(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    seen = fault_sequence(controller, ['EXITED', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx))
    assert result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION and len(seen) == 2
    feedback = json.loads(controller.explorer.contexts[1].feedback)
    assert feedback['result_code'] == 'REPLAY_UNCONFIRMED'
    assert feedback['verdict']['execution_diagnostic']['code'] == 'COMMAND_TIMEOUT'


def test_fixed_timeout_is_blocked_and_preserves_original_confirmation(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / 'fixed')
    seen = fault_sequence(controller, ['EXITED', 'EXITED', 'COMMAND_TIMEOUT'])
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed)))
    assert result.status == TaskState.DONE and len(seen) == 3
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert result.fix_validation_status == 'blocked'
    assert any('COMMAND_TIMEOUT' in text for text in result.uncertainties)

@pytest.mark.parametrize('error_kind', ['model', 'value'])
def test_actual_completed_execution_resets_streak_even_when_verifier_raises(tmp_path, projects, facts, error_kind):
    from reproagent.core.protocol import ModelOutputError
    request, _, controller, ctx = setup(tmp_path, projects, facts,
        plan=[publish(), publish(hypothesis='second'), publish(hypothesis='third'), ask()])
    seen = fault_sequence(controller, ['COMMAND_TIMEOUT', 'EXITED', 'COMMAND_TIMEOUT'])
    evaluate = controller.verifier.evaluate
    async def wrapped(contract, candidate, runs, context):
        if runs[-1].raw.stop_reason == 'EXITED':
            if error_kind == 'model':
                raise ModelOutputError('provider response invalid')
            raise ValueError('unavailable semantic evidence')
        return await evaluate(contract, candidate, runs, context)
    controller.verifier.evaluate = wrapped
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and len(seen) == 3
    feedback = json.loads(controller.explorer.contexts[3].feedback)
    assert feedback['consecutive_command_timeouts'] == 1


def test_fixed_completed_protection_failure_is_failed_not_incomplete(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / 'fixed')
    execute = controller.runner.backend.execute
    async def wrapped(spec, context):
        raw = await execute(spec, context)
        if spec.candidate_id and spec.execution_role == 'fixed':
            path = spec.cwd / 'example/parser.py'
            path.write_bytes(path.read_bytes() + b'\n# modified after execution\n')
        return raw
    controller.runner.backend.execute = wrapped
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed)))
    assert result.status == TaskState.DONE
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert result.fix_validation_status == 'failed'
    assert any('SOURCE_PROTECTION_FAILED' in text for text in result.uncertainties)
    assert not any('execution incomplete' in text for text in result.uncertainties)


def test_environment_block_during_independent_replay_stops_exploration(tmp_path, projects, facts):
    from reproagent.core.models import CandidateClass
    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    evaluate = controller.verifier.evaluate
    seen = []
    async def replay_block(contract, candidate, runs, context):
        verdict = await evaluate(contract, candidate, runs, context)
        seen.append(verdict)
        if len(seen) == 2:
            # Inject a verdict at the semantic boundary; real original runs remain intact.
            return replace(verdict, classification=CandidateClass.ENVIRONMENT_BLOCKED,
                           reason='Required external service unavailable on replay', next_action='stop')
        return verdict
    controller.verifier.evaluate = replay_block
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED
    assert result.stop_reason == 'ENVIRONMENT_BLOCKED'
    assert result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION
    assert len(seen) == 2 and len(controller.explorer.contexts) == 1
    rejected = [e for e in controller.store.read_events()[0] if e.kind == 'action.rejected']
    assert rejected[-1].payload['action'] == 'submit_candidate'
    assert rejected[-1].payload['result_code'] == 'ENVIRONMENT_BLOCKED'


def test_real_missing_target_dependency_still_stops(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(), ask()])
    (request.repo / 'example/parser.py').write_text(
        'def parse(values):\n    import required_external_dependency_123456\n    return values\n', encoding='utf-8')
    model.classification = 'ENVIRONMENT_BLOCKED'
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED
    assert result.stop_reason == 'ENVIRONMENT_BLOCKED'
    assert result.evidence_level == EvidenceLevel.NONE
    assert not result.accepted_candidate_id
    assert len(controller.explorer.contexts) == 1
    assert model.kinds.count('verdict') == 1
    verdicts = [controller.store.load_record('verdicts', p.stem)
                for p in (request.output_dir / 'verdicts').glob('*.json')]
    assert len(verdicts) == 1 and verdicts[0].next_action == 'stop'
