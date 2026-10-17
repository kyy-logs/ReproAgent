"""A document the test wrote and the build never read is not evidence of anything.

The testroot's ``source_suffix`` decides which files Sphinx turns into documents.  A
candidate that writes ``index.rst`` into a testroot configured for ``.txt`` produces no
document at all: the build runs on the original input, the target warning never appears,
and the test's result says nothing about the candidate's document.  These tests pin that
observation down as a fact the program can report, rather than something a reader has to
infer from a passing test.
"""
import json
from types import SimpleNamespace

from reproagent.core.models import TestObservation as Observation


def app(tmp_path, *, suffix=('.txt',), found=('index',)):
    """A Sphinx app whose source dir starts with what the testroot ships."""
    srcdir = tmp_path / 'copy' / 'root'
    srcdir.mkdir(parents=True)
    (srcdir / 'index.txt').write_text('original\n', encoding='utf-8')
    return SimpleNamespace(srcdir=srcdir, outdir=srcdir / '_build',
        config=SimpleNamespace(source_suffix={name: None for name in suffix}),
        env=SimpleNamespace(found_docs=set(found)))


def inputs(tmp_path, *, created=('index.rst',), **kwargs):
    """Observe one test: the baseline is taken before the body writes anything."""
    from reproagent.adapters.languages.python_pytest.probe.reproagent_pytest_probe import (
        sphinx_inputs, srcdir_listing)
    built = app(tmp_path, **kwargs)
    baseline = srcdir_listing(built.srcdir)
    for name in created:
        path = built.srcdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('candidate\n', encoding='utf-8')
    return sphinx_inputs(built, baseline)


def test_a_document_the_testroot_never_reads_is_recorded_not_silently_ignored(tmp_path):
    recorded = inputs(tmp_path)
    assert recorded['testroot'] == 'root'
    assert recorded['source_suffix'] == ['.txt']
    # Only what the test wrote -- the shipped index.txt is not "created".
    assert recorded['created'] == ['index.rst']
    # This is the whole failure: the build read index.txt and never saw index.rst.
    assert recorded['unread'] == ['index.rst']


def test_a_document_the_testroot_does_read_is_not_flagged(tmp_path):
    recorded = inputs(tmp_path, suffix=('.rst', '.txt'), created=('sub.rst',))
    assert recorded['created'] == ['sub.rst']
    assert recorded['unread'] == []


def test_helpers_templates_and_build_output_are_not_documents(tmp_path):
    recorded = inputs(tmp_path, created=('helper.py', '_templates/layout.html', '_build/index.html'))
    assert recorded['unread'] == []


def test_nothing_is_claimed_when_no_build_ran(tmp_path):
    # Without a build there is nothing that could have read anything, so an empty
    # found_docs set must not turn every created file into an accusation.
    assert inputs(tmp_path, found=()) is None


def test_nothing_is_claimed_without_a_baseline(tmp_path):
    # Without the pre-test listing the difference cannot be attributed to this test.
    from reproagent.adapters.languages.python_pytest.probe.reproagent_pytest_probe import sphinx_inputs
    assert sphinx_inputs(app(tmp_path), None) is None


def test_the_collector_carries_the_observation_through_unchanged(tmp_path):
    from reproagent.adapters.languages.python_pytest.collector import read_probe
    observed = {'testroot': 'root', 'source_suffix': ['.txt'], 'created': ['index.rst'], 'unread': ['index.rst']}
    def line(seq, event, payload):
        return json.dumps({'probe_version': 1, 'run_id': 'r', 'seq': seq, 'event': event, 'payload': payload})
    rows = [line(0, 'session_start', {'python': '3.9', 'pytest': '7.0'}),
            line(1, 'collected', {'nodeids': ['t.py::test_x']}),
            line(2, 'test_phase', {'nodeid': 't.py::test_x', 'when': 'setup', 'outcome': 'passed'}),
            line(3, 'test_phase', {'nodeid': 't.py::test_x', 'when': 'call', 'outcome': 'passed', 'sphinx_inputs': observed}),
            line(4, 'test_phase', {'nodeid': 't.py::test_x', 'when': 'teardown', 'outcome': 'passed'}),
            line(5, 'session_finish', {'exitstatus': 0, 'target_origins': {}, 'target_calls': {}})]
    path = tmp_path / 'probe.jsonl'
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    observation = read_probe(path, 'r')
    phase = next(item for item in observation.tests if item['when'] == 'call')
    assert phase['sphinx_inputs'] == observed


def test_a_malformed_observation_is_refused(tmp_path):
    from reproagent.adapters.languages.python_pytest.collector import read_probe
    def line(seq, event, payload):
        return json.dumps({'probe_version': 1, 'run_id': 'r', 'seq': seq, 'event': event, 'payload': payload})
    rows = [line(0, 'session_start', {'python': '3.9', 'pytest': '7.0'}),
            line(1, 'collected', {'nodeids': ['t.py::test_x']}),
            line(2, 'test_phase', {'nodeid': 't.py::test_x', 'when': 'call', 'outcome': 'passed',
                                   'sphinx_inputs': {'unread': 'index.rst'}}),
            line(3, 'session_finish', {'exitstatus': 0, 'target_origins': {}, 'target_calls': {}})]
    path = tmp_path / 'probe.jsonl'
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    observation = read_probe(path, 'r')
    assert not observation.probe_complete
    assert any('sphinx' in error for error in observation.framework_details['errors'])


def test_the_framework_check_names_the_input_the_build_never_read():
    from reproagent.adapters.languages.python_pytest.adapter import PythonPytestAdapter
    observation = Observation(collected=True, executed=True, probe_complete=True, tests=(
        {'nodeid': 't.py::test_x', 'when': 'call', 'outcome': 'passed',
         'sphinx_inputs': {'testroot': 'root', 'source_suffix': ['.txt'],
                           'created': ['index.rst'], 'unread': ['index.rst']}},))
    checks = PythonPytestAdapter().check_framework(None, observation)
    assert any('not read' in item and 'index.rst' in item and '.txt' in item for item in checks.invalid), checks.invalid


def test_a_real_failure_is_not_vetoed_by_an_unread_document_elsewhere():
    """One test can be evidence while another's document was never read.

    The unread input explains a passing test, so it vetoes a run with no other evidence.
    It must not throw away a reproduction that another test really produced.
    """
    from reproagent.adapters.languages.python_pytest.adapter import PythonPytestAdapter
    unread = {'testroot': 'root', 'source_suffix': ['.txt'], 'created': ['index.rst'], 'unread': ['index.rst']}
    observation = Observation(collected=True, executed=True, probe_complete=True, tests=(
        {'nodeid': 't.py::test_real', 'when': 'call', 'outcome': 'failed'},
        {'nodeid': 't.py::test_unread', 'when': 'call', 'outcome': 'passed', 'sphinx_inputs': unread}))
    checks = PythonPytestAdapter().check_framework(None, observation)
    assert not any('not read' in item for item in checks.invalid), checks.invalid


def test_a_published_candidate_document_the_testroot_never_reads_is_flagged(tmp_path, monkeypatch):
    """A candidate file under the testroot is input even though it predates the test body.

    Sphinx copies the whole testroot -- the candidate's files included -- when the fixture
    builds the app, so the before/after comparison alone cannot see them.  The runner names
    them, and the build either read the document or it did not.  This is exactly how the
    unread input was published in the real 8474 run: tests/roots/test-root/index.rst.
    """
    from reproagent.adapters.languages.python_pytest.probe import reproagent_pytest_probe as probe
    built = app(tmp_path)                                   # this testroot reads .txt only
    (built.srcdir / 'index.rst').write_text('candidate\n', encoding='utf-8')
    baseline = probe.srcdir_listing(built.srcdir)           # the candidate file is already there
    monkeypatch.setattr(probe, '_candidate_files', ('tests/roots/root/index.rst',))
    recorded = probe.sphinx_inputs(built, baseline)
    assert recorded['created'] == ['index.rst']
    assert recorded['unread'] == ['index.rst']
