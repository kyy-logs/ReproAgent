"""Replay confirmation for pytest boolean assertions with identity noise.

The two executions are real probe -> collector runs.  Only the failure display's
process-specific object addresses differ; the failure itself is the same.
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

from reproagent.core.models import CandidateClass
from tests.integration.test_runner import execute
from tests.unit.test_verifier import prepare


def source_project(projects, source):
    def plain(root):
        repo = projects.plain(root)
        (repo / 'example/parser.py').write_text(source, encoding='utf-8')
        return repo
    return SimpleNamespace(plain=plain)


def startswith_contract(tmp_path, projects, facts, test_text):
    return prepare(tmp_path, source_project(projects,
        'def render(values):\n'
        '    return "utils.py 114 DEBUG hello"\n'), facts, text=test_text)


def verdicts(contract, candidate, first, second, verifier, facts):
    return tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context()))
                 for run in (first, second))


def failure(run):
    return next(t for t in run.observation.tests
                if t['outcome'] == 'failed' and t['when'] == 'call')


def rewrite_failure(run, *, message=None, keep_display=True):
    tests = []
    for test in run.observation.tests:
        if test['outcome'] == 'failed' and test['when'] == 'call':
            test = dict(test)
            if message is not None:
                test['crash'] = {**test['crash'], 'message': message}
            if not keep_display:
                test = {key: value for key, value in test.items() if key != 'assertion_display'}
        tests.append(test)
    return replace(run, observation=replace(run.observation, tests=tuple(tests)))


def test_plain_boolean_assertion_confirms_after_address_only_display_difference(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    first_failure, second_failure = failure(first), failure(second)
    assert first_failure['assertion_display']['provenance'] == 'pytest_rewrite'
    assert second_failure['assertion_display']['provenance'] == 'pytest_rewrite'
    import re
    first_addresses = set(re.findall(r'0x[0-9a-fA-F]+', first_failure['crash']['message']))
    second_addresses = set(re.findall(r'0x[0-9a-fA-F]+', second_failure['crash']['message']))
    assert first_addresses and second_addresses and first_addresses != second_addresses
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert all(item.classification == CandidateClass.REPRODUCED for item in checks)
    assert verifier.confirm(contract, candidate, first, second, checks)
    events, errors = verifier.store.read_events()
    assert not errors
    compared = [event.payload for event in events if event.kind == 'replay.compared'][-1]
    assert compared['confirmed'] is True and compared['reason'] == 'CONFIRMED'
    assert compared['first_run_id'] == first.run_id and compared['second_run_id'] == second.run_id


def test_nested_formatter_address_confirms_without_weakening_the_value(tmp_path, projects, facts):
    source = (
        'class Formatter:\n'
        '    def format(self, record):\n'
        '        return "utils.py 114 DEBUG hello"\n'
        'def render(values):\n'
        '    return Formatter()\n')
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(
        tmp_path, source_project(projects, source), facts,
        text='from example.parser import Formatter\n'
             'def test_render():\n'
             '    assert Formatter().format(None).startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    assert failure(first)['assertion_display']['provenance'] == 'pytest_rewrite'
    assert failure(second)['assertion_display']['provenance'] == 'pytest_rewrite'
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert verifier.confirm(contract, candidate, first, second, checks)


def test_generator_expression_address_confirms(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert any("MISSING" in value for value in [out])\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    first_failure, second_failure = failure(first), failure(second)
    import re
    first_addresses = set(re.findall(r'0x[0-9a-fA-F]+', first_failure['crash']['message']))
    second_addresses = set(re.findall(r'0x[0-9a-fA-F]+', second_failure['crash']['message']))
    assert first_addresses and second_addresses
    assert '<generator object ' in first_failure['crash']['message']
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert all(item.classification == CandidateClass.REPRODUCED for item in checks)
    assert verifier.confirm(contract, candidate, first, second, checks)


def test_real_value_change_is_still_rejected(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    changed = rewrite_failure(second,
        message=failure(second)['crash']['message'].replace('utils.py', 'other.py'))
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert not verifier.confirm(contract, candidate, first, changed, checks)
    events, errors = verifier.store.read_events()
    assert not errors
    compared = [event.payload for event in events if event.kind == 'replay.compared'][-1]
    assert compared['confirmed'] is False and compared['reason'] == 'FAILURE_SIGNATURE_MISMATCH'
    assert compared['differences'][0]['nodeid'].endswith('test_render')
    assert 'message' in compared['differences'][0]['fields']


def test_missing_display_proof_keeps_the_historical_conservative_result(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert not verifier.confirm(contract, candidate,
                                rewrite_failure(first, keep_display=False),
                                rewrite_failure(second, keep_display=False), checks)
    events, errors = verifier.store.read_events()
    assert not errors
    compared = [event.payload for event in events if event.kind == 'replay.compared'][-1]
    assert compared['reason'] == 'FAILURE_SIGNATURE_MISMATCH'
    assert all(item['fields'] for item in compared['differences'])


def test_quoted_address_like_business_values_are_not_normalized(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_literal():\n'
        '    render([])\n'
        '    value = "<Tag at 0x1234>"\n'
        '    assert not value\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    first_failure, second_failure = failure(first), failure(second)
    assert first_failure.get('assertion_display') is None
    assert second_failure.get('assertion_display') is None
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert verifier.confirm(contract, candidate, first, second, checks)
    changed = rewrite_failure(second,
        message=second_failure['crash']['message'].replace('0x1234', '0x5678'))
    assert not verifier.confirm(contract, candidate, first, changed, checks)


def test_same_line_plain_assert_does_not_authorize_a_custom_message(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'class Box:\n'
        '    def false(self): return False\n'
        'def test_custom_message():\n'
        '    render([])\n'
        '    o = Box()\n'
        '    assert True; assert o.false(), f"<Tag at {id(o):#x}>"\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    first_failure, second_failure = failure(first), failure(second)
    assert first.observation.probe_complete and second.observation.probe_complete
    assert first_failure.get('assertion_display') is None
    assert second_failure.get('assertion_display') is None
    assert first_failure['crash']['message'] != second_failure['crash']['message']
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert all(item.classification == CandidateClass.REPRODUCED for item in checks)
    assert not verifier.confirm(contract, candidate, first, second, checks)


def test_replay_diagnostic_failure_does_not_change_the_decision(tmp_path, projects, facts, monkeypatch):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    append = verifier.store.append_event

    def fail_replay(kind, refs, payload):
        if kind == 'replay.compared':
            raise OSError('offline diagnostic failure')
        return append(kind, refs, payload)

    monkeypatch.setattr(verifier.store, 'append_event', fail_replay)
    assert verifier.confirm(contract, candidate, first, second, checks)


def test_verdict_and_empty_signature_reasons_are_controlled(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert not verifier.confirm(contract, candidate, first, second, checks[:1])
    events, errors = verifier.store.read_events()
    assert not errors
    assert [event.payload['reason'] for event in events if event.kind == 'replay.compared'][-1] == \
        'VERDICT_MISMATCH'
    assert not verifier.confirm(contract, candidate, first,
                                replace(second, observation=replace(second.observation, tests=())), checks)
    events, errors = verifier.store.read_events()
    assert not errors
    assert [event.payload['reason'] for event in events if event.kind == 'replay.compared'][-1] == \
        'NO_FAILURE_SIGNATURE'


def test_same_test_nodes_in_a_different_report_order_still_confirm(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n'
        'def test_control():\n'
        '    assert True\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)

    def reorder(run):
        details = dict(run.observation.framework_details)
        details['collected_nodeids'] = list(reversed(details['collected_nodeids']))
        details['completed_nodeids'] = list(reversed(details['completed_nodeids']))
        return replace(run, observation=replace(run.observation, framework_details=details))

    reordered_second = reorder(second)
    assert first.observation.framework_details['collected_nodeids'] != \
        reordered_second.observation.framework_details['collected_nodeids']
    assert verifier.confirm(contract, candidate, first, reordered_second, checks)
    shortened = dict(first.observation.framework_details)
    shortened['completed_nodeids'] = shortened['completed_nodeids'][:-1]
    assert not verifier.confirm(contract, candidate,
                                replace(first, observation=replace(
                                    first.observation, framework_details=shortened)),
                                second, checks)


def test_fixed_role_and_test_set_checks_still_block(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    out = render([])\n'
        '    assert out.startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert not verifier.confirm(contract, candidate, first,
                                replace(second, execution_role='fixed'), checks)
    events, errors = verifier.store.read_events()
    assert not errors
    assert [event.payload['reason'] for event in events if event.kind == 'replay.compared'][-1] == \
        'BINDING_OR_INTEGRITY_MISMATCH'
    changed = replace(second, observation=replace(second.observation,
        framework_details={**second.observation.framework_details,
                           'collected_nodeids': ['different::test']}))
    assert not verifier.confirm(contract, candidate, first, changed, checks)
    events, errors = verifier.store.read_events()
    assert not errors
    assert [event.payload['reason'] for event in events if event.kind == 'replay.compared'][-1] == \
        'TEST_SET_MISMATCH'

def test_two_failed_nodes_confirm_after_report_order_changes(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_first():\n'
        '    assert render([]).startswith("DEBUG")\n'
        'def test_second():\n'
        '    assert render([]).startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert sum(phase['when'] == 'call' and phase['outcome'] == 'failed'
               for phase in second.observation.tests) == 2
    assert verifier.confirm(contract, candidate, first, second, checks)

    # Move complete node reports, preserving setup/call/teardown within each node.
    nodes = list(dict.fromkeys(phase['nodeid'] for phase in second.observation.tests))
    phases = tuple(phase for node in reversed(nodes)
                   for phase in second.observation.tests if phase['nodeid'] == node)
    details = dict(second.observation.framework_details)
    for key in ('collected_nodeids', 'completed_nodeids'):
        details[key] = list(reversed(details[key]))
    reordered = replace(second, observation=replace(
        second.observation, tests=phases, framework_details=details))
    assert verifier.confirm(contract, candidate, first, reordered, checks)


def test_failure_signature_multiplicity_still_blocks_confirmation(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = startswith_contract(
        tmp_path, projects, facts,
        'from example.parser import render\n'
        'def test_render():\n'
        '    assert render([]).startswith("DEBUG")\n')
    second = execute(request, snapshot, candidate, runner, facts.context())
    checks = verdicts(contract, candidate, first, second, verifier, facts)
    assert verifier.confirm(contract, candidate, first, second, checks)
    duplicated = replace(second, observation=replace(
        second.observation, tests=(*second.observation.tests, failure(second))))
    assert not verifier.confirm(contract, candidate, first, duplicated, checks)
