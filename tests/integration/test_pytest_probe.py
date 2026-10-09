import asyncio
import importlib
import json
import sys
from pathlib import Path

from reproagent.adapters.runtimes.local import LocalBackend
from reproagent.core.budget import Budget
from reproagent.core.models import BudgetLimits, ExecutionSpec, RunContext


def test_a_frame_the_compiler_named_never_ends_collection(monkeypatch):
    """The tracer must survive the frames whose co_filename is a label, not a path.

    attrs generates ``_pytest.fixtures.FixtureFunctionMarker.__init__``, and the compiler
    names that frame ``'<attrs generated init ...>'``.  On Python 3.9 -- the version the
    dataset pins for every instance -- ``Path.resolve()`` raises OSError for such a name,
    and the tracer's exception ended collection, so every candidate that defined a fixture
    was ruled INVALID_CANDIDATE without its test ever running.
    """
    from types import SimpleNamespace
    probe = importlib.import_module('reproagent.adapters.languages.python_pytest.probe.reproagent_pytest_probe')
    monkeypatch.setattr(probe, '_targets', ('_pytest',))
    monkeypatch.setattr(probe, '_origins', {})
    monkeypatch.setattr(probe, '_calls', {})
    def unresolvable(self, **kwargs):
        raise OSError(123, 'The filename, directory name, or volume label syntax is incorrect')
    monkeypatch.setattr(Path, 'resolve', unresolvable)
    frame = SimpleNamespace(f_globals={'__name__': '_pytest.fixtures'},
                            f_code=SimpleNamespace(co_filename='<attrs generated init _pytest.fixtures.X>'))
    probe.profile(frame, 'call', None)                       # must not raise
    assert probe._origins == {}                              # nothing here to vouch for
    assert probe._calls == {'_pytest.fixtures': 1}           # the call still counts


def run_probe(root, text, modules=()):
    collector = importlib.import_module('reproagent.adapters.languages.python_pytest.collector')
    probe = Path(collector.__file__).parent / 'probe'
    (root / 'test_case.py').write_text(text, encoding='utf-8')
    path = root / 'probe.jsonl'
    spec = ExecutionSpec('probe', (sys.executable, '-m', 'pytest', '-q', '-p', 'reproagent_pytest_probe', 'test_case.py'), root,
        env_overrides={'PYTHONPATH': str(probe), 'REPROAGENT_RUN_ID': 'run-1', 'REPROAGENT_PROBE_PATH': str(path), 'REPROAGENT_TARGET_MODULES': json.dumps(modules)}, probe_path=path)
    raw = asyncio.run(LocalBackend().execute(spec, RunContext(Budget(BudgetLimits()))))
    assert raw.cleanup_ok
    return collector.read_probe(path, 'run-1'), path


def test_probe_records_setup_call_teardown_skip_and_xfail(tmp_path):
    observation, path = run_probe(tmp_path, '''import pytest
def test_ok(): assert True
@pytest.mark.skip(reason="skip")
def test_skip(): pass
@pytest.mark.xfail(reason="known")
def test_xfail(): assert False
''')
    events = [json.loads(line) for line in path.read_text().splitlines()]
    phases = [e for e in events if e['event'] == 'test_phase']
    assert {e['payload']['when'] for e in phases} == {'setup', 'call', 'teardown'}
    assert observation.probe_complete and observation.executed
    assert any(t.get('wasxfail') for t in observation.tests)
    assert any(t['outcome'] == 'skipped' for t in observation.tests)


def test_import_bug_is_observed_without_probe_importing_target(tmp_path):
    marker = tmp_path / 'imported'
    (tmp_path / 'broken.py').write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise ValueError('import bug')\n")
    observation, _ = run_probe(tmp_path, 'def test_no_import(): assert True\n', ('broken',))
    assert not marker.exists()
    observation, _ = run_probe(tmp_path, 'def test_import():\n    import broken\n', ('broken',))
    assert marker.exists() and observation.probe_complete
    assert any('ValueError' in t.get('longrepr', '') for t in observation.tests)
    assert 'broken' in observation.target_origins


def test_incomplete_probe_never_becomes_complete(tmp_path):
    collector = importlib.import_module('reproagent.adapters.languages.python_pytest.collector')
    _, path = run_probe(tmp_path, 'def test_ok(): assert True\n')
    lines = path.read_text().splitlines()
    path.write_text('\n'.join(lines[:-1]) + '\n')
    assert not collector.read_probe(path, 'run-1').probe_complete
    assert not collector.read_probe(path, 'different').probe_complete
    path.write_text('\n'.join(lines[:2]) + '\n{')
    assert not collector.read_probe(path, 'run-1').probe_complete
