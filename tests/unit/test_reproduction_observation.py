"""What a passing run can say about why it passed.

A candidate that passes on the original snapshot is not evidence of anything.  The run
does know whether it read the candidate's input and which diagnostics it emitted, and
that is what tells a next phase where to look.  The conclusion is drawn only when every
call phase was monitored completely; otherwise it stays unknown, because an unmonitored
run and a run that reached nothing look identical from the outside.
"""
import json

import pytest

from reproagent.core.models import TestObservation as Observation, Verdict, VerificationIssue


def phase(**overrides):
    payload = {'nodeid': 't.py::test_x', 'when': 'call', 'outcome': 'passed'}
    payload.update(overrides)
    return payload


def observation(*phases):
    return Observation(collected=True, executed=True, probe_complete=True, tests=tuple(phases))


def captured(*, diagnostics=()):
    return {'inputs': 'captured', 'functions': 'captured', 'diagnostics': 'captured'}, list(diagnostics)


def test_a_captured_run_with_no_diagnostic_says_the_diagnostic_was_not_emitted():
    from reproagent.core.reproduction_observation import reproduction_observation
    monitoring, diagnostics = captured()
    recorded = reproduction_observation(observation(phase(monitoring=monitoring, diagnostics=diagnostics)))
    assert recorded == {'inputs': 'captured', 'diagnostics': 'captured', 'emitted': 0,
                        'conclusion': 'no_diagnostic_emitted'}


def test_emitted_diagnostics_are_reported_as_present():
    from reproagent.core.reproduction_observation import reproduction_observation
    monitoring, diagnostics = captured(diagnostics=[{'message': 'boom', 'path': 'x.py', 'lineno': 1, 'level': 'WARNING'}])
    recorded = reproduction_observation(observation(phase(monitoring=monitoring, diagnostics=diagnostics)))
    assert recorded['emitted'] == 1 and recorded['conclusion'] == 'diagnostics_emitted'


@pytest.mark.parametrize('monitoring', [
    {'inputs': 'captured', 'functions': 'captured', 'diagnostics': 'incomplete'},
    {'inputs': 'incomplete', 'functions': 'captured', 'diagnostics': 'captured'},
    {'inputs': 'not_configured', 'functions': 'captured', 'diagnostics': 'not_configured'},
])
def test_incomplete_monitoring_is_unknown_never_an_absence(monitoring):
    from reproagent.core.reproduction_observation import reproduction_observation
    recorded = reproduction_observation(observation(phase(monitoring=monitoring)))
    assert recorded['conclusion'] == 'unknown'


def test_a_phase_without_monitoring_is_unknown():
    from reproagent.core.reproduction_observation import reproduction_observation
    assert reproduction_observation(observation(phase()))['conclusion'] == 'unknown'


def test_every_call_phase_has_to_be_captured_for_a_conclusion():
    from reproagent.core.reproduction_observation import reproduction_observation
    monitoring, _ = captured()
    recorded = reproduction_observation(observation(
        phase(monitoring=monitoring),
        phase(nodeid='t.py::test_y', monitoring={'inputs': 'captured', 'functions': 'captured', 'diagnostics': 'incomplete'})))
    assert recorded['conclusion'] == 'unknown' and recorded['diagnostics'] == 'incomplete'


def test_only_call_phases_count():
    # Setup and teardown cannot say whether the test body reached the diagnostic.
    from reproagent.core.reproduction_observation import reproduction_observation
    monitoring, _ = captured()
    recorded = reproduction_observation(observation(
        {'nodeid': 't.py::test_x', 'when': 'setup', 'outcome': 'passed'},
        phase(monitoring=monitoring),
        {'nodeid': 't.py::test_x', 'when': 'teardown', 'outcome': 'passed'}))
    assert recorded['conclusion'] == 'no_diagnostic_emitted'


def verdict_with(record):
    issue = VerificationIssue('CANDIDATE_NOT_REPRODUCED', 'candidate_reproduced', 'program_check',
        'candidate passes on original snapshot', measurements={'reproduction_observation': record},
        recovery='revise_candidate')
    return Verdict(None, 'candidate passes on original snapshot', issues=(issue,))


def test_feedback_tells_the_phase_the_reported_diagnostic_was_never_emitted():
    from reproagent.core.verification_feedback import render_verification_feedback
    text = render_verification_feedback(verdict_with({'inputs': 'captured', 'diagnostics': 'captured',
        'emitted': 0, 'conclusion': 'no_diagnostic_emitted'}), result_code='NOT_REPRODUCED', byte_limit=8192)
    instruction = json.loads(text)['instruction']
    assert '没有发出任何诊断' in instruction
    # The measured facts travel too, not only the sentence.
    assert json.loads(text)['verdict']['issues'][0]['measurements']['reproduction_observation']['emitted'] == 0


def test_an_unknown_observation_adds_no_claim_at_all():
    from reproagent.core.verification_feedback import render_verification_feedback
    text = render_verification_feedback(verdict_with({'inputs': 'incomplete', 'diagnostics': 'incomplete',
        'emitted': 0, 'conclusion': 'unknown'}), result_code='NOT_REPRODUCED', byte_limit=8192)
    assert '没有发出任何诊断' not in json.loads(text)['instruction']


@pytest.mark.parametrize('record', [
    {'inputs': 'captured', 'diagnostics': 'captured', 'emitted': 0, 'conclusion': 'maybe'},
    {'inputs': 'captured', 'diagnostics': 'captured', 'emitted': -1, 'conclusion': 'unknown'},
    {'inputs': 'captured', 'conclusion': 'unknown'},
])
def test_a_malformed_observation_cannot_reach_the_model(record):
    with pytest.raises(ValueError):
        verdict_with(record)
