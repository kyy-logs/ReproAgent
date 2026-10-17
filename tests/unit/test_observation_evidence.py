"""What a run reached, recorded as facts -- and what it could not observe, as unknown.

A passing test says nothing about why it passed.  The probe therefore records whether a
target function was entered and which warnings the test actually emitted, each with a
monitoring status: a claim of "not reached" is only allowed when the observation was
captured completely.  Anything else stays unknown, because an unmonitored run and a run
that reached nothing look identical from the outside.
"""
import logging
from types import SimpleNamespace


def probe_module():
    from reproagent.adapters.languages.python_pytest.probe import reproagent_pytest_probe as probe
    return probe


def frame(module, filename, name, qualname=None):
    code = {'co_filename': filename, 'co_name': name}
    if qualname is not None:
        code['co_qualname'] = qualname
    return SimpleNamespace(f_globals={'__name__': module}, f_code=SimpleNamespace(**code))


def test_a_target_function_entry_is_recorded_by_name(monkeypatch):
    probe = probe_module()
    monkeypatch.setattr(probe, '_targets', ('example.parser',))
    monkeypatch.setattr(probe, '_functions', {})
    monkeypatch.setattr(probe, '_module_functions', {})
    monkeypatch.setattr(probe, '_function_overflow', False)
    probe.profile(frame('example.parser', 'example/parser.py', 'parse', 'parse'), 'call', None)
    probe.profile(frame('example.parser', 'example/parser.py', 'parse', 'parse'), 'call', None)
    assert probe._functions == {'example.parser.parse': 2}


def test_python39_has_no_qualname_so_the_plain_name_is_recorded(monkeypatch):
    # The dataset pins Python 3.9, where co_qualname does not exist.  The fallback is
    # recorded as what it is -- a plain name -- never presented as a qualified one.
    probe = probe_module()
    monkeypatch.setattr(probe, '_targets', ('example.parser',))
    monkeypatch.setattr(probe, '_functions', {})
    monkeypatch.setattr(probe, '_module_functions', {})
    monkeypatch.setattr(probe, '_function_overflow', False)
    probe.profile(frame('example.parser', 'example/parser.py', 'parse'), 'call', None)
    assert probe._functions == {'example.parser.parse': 1}
    assert getattr(frame('example.parser', 'example/parser.py', 'parse').f_code, 'co_qualname', None) is None


def test_a_function_outside_the_targets_is_not_recorded(monkeypatch):
    probe = probe_module()
    monkeypatch.setattr(probe, '_targets', ('example.parser',))
    monkeypatch.setattr(probe, '_functions', {})
    monkeypatch.setattr(probe, '_module_functions', {})
    monkeypatch.setattr(probe, '_function_overflow', False)
    probe.profile(frame('other.module', 'other.py', 'run', 'run'), 'call', None)
    assert probe._functions == {}


def test_exceeding_the_per_module_cap_is_reported_as_incomplete(monkeypatch):
    # A target is usually a whole package, and its test machinery runs first.  Each module
    # gets its own share, so the module under test is never crowded out by the fixtures.
    probe = probe_module()
    monkeypatch.setattr(probe, '_targets', ('example.parser',))
    monkeypatch.setattr(probe, '_functions', {})
    monkeypatch.setattr(probe, '_module_functions', {})
    monkeypatch.setattr(probe, '_function_overflow', False)
    for index in range(probe.MODULE_FUNCTION_LIMIT + 1):
        probe.profile(frame('example.parser', 'example/parser.py', 'f%d' % index), 'call', None)
    assert len(probe._functions) == probe.MODULE_FUNCTION_LIMIT
    assert probe._function_overflow is True


def test_each_target_module_keeps_its_own_share(monkeypatch):
    probe = probe_module()
    monkeypatch.setattr(probe, '_targets', ('example',))
    monkeypatch.setattr(probe, '_functions', {})
    monkeypatch.setattr(probe, '_module_functions', {})
    monkeypatch.setattr(probe, '_function_overflow', False)
    for index in range(probe.MODULE_FUNCTION_LIMIT + 4):
        probe.profile(frame('example.testing', 'example/testing.py', 'fixture%d' % index), 'call', None)
    probe.profile(frame('example.parser', 'example/parser.py', 'parse', 'parse'), 'call', None)
    assert 'example.parser.parse' in probe._functions


def test_an_emitted_warning_is_captured_with_its_source_location():
    probe = probe_module()
    handle = probe.start_capture()
    try:
        logging.getLogger().warning('no number is assigned for table: tbl')
    finally:
        records, overflow = probe.stop_capture(handle)
    assert records and records[0]['message'] == 'no number is assigned for table: tbl'
    # The record names the line that emitted it, which is the branch that ran.
    assert records[0]['path'].endswith('test_observation_evidence.py')
    assert records[0]['lineno'] > 0
    assert records[0]['level'] == 'WARNING'
    assert overflow is False


def test_below_warning_is_not_a_diagnostic():
    probe = probe_module()
    handle = probe.start_capture()
    try:
        logging.getLogger().info('just progress')
    finally:
        records, _ = probe.stop_capture(handle)
    assert records == ()


def test_exceeding_the_diagnostic_cap_is_reported_as_incomplete():
    probe = probe_module()
    handle = probe.start_capture()
    try:
        for index in range(probe.DIAGNOSTIC_LIMIT + 1):
            logging.getLogger().warning('warning %d', index)
    finally:
        records, overflow = probe.stop_capture(handle)
    assert len(records) == probe.DIAGNOSTIC_LIMIT
    assert overflow is True


def test_monitoring_names_what_could_not_be_observed(monkeypatch):
    probe = probe_module()
    monkeypatch.setattr(probe, '_targets', ())
    monkeypatch.setattr(probe, '_function_overflow', False)
    assert probe.monitoring_status(inputs='captured', diagnostics_overflow=False) == {
        'inputs': 'captured', 'functions': 'not_configured', 'diagnostics': 'captured'}
    monkeypatch.setattr(probe, '_targets', ('example.parser',))
    monkeypatch.setattr(probe, '_function_overflow', True)
    assert probe.monitoring_status(inputs='incomplete', diagnostics_overflow=True) == {
        'inputs': 'incomplete', 'functions': 'incomplete', 'diagnostics': 'incomplete'}


def probe_stream(tmp_path, payload):
    import json
    def line(seq, event, body):
        return json.dumps({'probe_version': 1, 'run_id': 'r', 'seq': seq, 'event': event, 'payload': body})
    rows = [line(0, 'session_start', {'python': '3.9', 'pytest': '7.0'}),
            line(1, 'collected', {'nodeids': ['t.py::test_x']}),
            line(2, 'test_phase', {'nodeid': 't.py::test_x', 'when': 'setup', 'outcome': 'passed'}),
            line(3, 'test_phase', payload),
            line(4, 'test_phase', {'nodeid': 't.py::test_x', 'when': 'teardown', 'outcome': 'passed'}),
            line(5, 'session_finish', {'exitstatus': 0, 'target_origins': {}, 'target_calls': {}})]
    path = tmp_path / 'probe.jsonl'
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return path


def observed_payload(**overrides):
    payload = {'nodeid': 't.py::test_x', 'when': 'call', 'outcome': 'passed',
        'target_functions': {'example.parser.parse': 2},
        'diagnostics': [{'message': 'no number is assigned for table: t',
                         'path': 'sphinx/domains/std.py', 'lineno': 855, 'level': 'WARNING'}],
        'monitoring': {'inputs': 'captured', 'functions': 'captured', 'diagnostics': 'captured'}}
    payload.update(overrides)
    return payload


def test_the_collector_carries_the_run_observations(tmp_path):
    from reproagent.adapters.languages.python_pytest.collector import read_probe
    observation = read_probe(probe_stream(tmp_path, observed_payload()), 'r')
    call = next(item for item in observation.tests if item['when'] == 'call')
    assert call['target_functions'] == {'example.parser.parse': 2}
    assert call['diagnostics'][0]['path'] == 'sphinx/domains/std.py'
    assert call['monitoring']['diagnostics'] == 'captured'


def test_an_unknown_monitoring_status_is_refused(tmp_path):
    from reproagent.adapters.languages.python_pytest.collector import read_probe
    observation = read_probe(probe_stream(tmp_path, observed_payload(
        monitoring={'inputs': 'captured', 'functions': 'maybe', 'diagnostics': 'captured'})), 'r')
    assert not observation.probe_complete
    assert any('monitoring' in error for error in observation.framework_details['errors'])


def test_the_review_projection_keeps_what_was_reached_and_what_was_unknown():
    from reproagent.core.review_context import _phase
    projected = _phase(observed_payload(), '')
    assert projected['target_functions'] == {'example.parser.parse': 2}
    assert projected['diagnostics'][0]['message'].startswith('no number is assigned')
    assert projected['diagnostics'][0]['lineno'] == 855
    assert projected['monitoring']['functions'] == 'captured'
