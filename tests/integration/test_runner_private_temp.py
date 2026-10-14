"""Third-party pytest helpers may strip extended paths before writing children."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from reproagent.core.models import CandidateDraft, DraftFile, PythonPytestConfig, TaskRequest
from reproagent.runner import Runner
from reproagent.store import TaskStore
from reproagent.workspace import Workspace


@pytest.mark.skipif(os.name != 'nt', reason='Windows legacy-path compatibility')
def test_pytester_long_named_file_uses_private_short_temp_and_cleans_it(tmp_path, projects, facts):
    repo = projects.plain(tmp_path / 'repo')
    (repo / 'tests/conftest.py').write_text('pytest_plugins = ["pytester"]\n', encoding='utf-8')
    request = TaskRequest(repo, tmp_path / 'out', repo / 'issue.md', language=PythonPytestConfig(
        target_modules=('example.parser',)))
    store = TaskStore(request.output_dir)
    workspace = Workspace(request.output_dir, store)
    snapshot = workspace.freeze(request, facts.context())
    source = ('from example.parser import parse\n'
        'def test_module_level_skip_error_mentions_allow_module_level(pytester):\n'
        '    file = pytester.makepyfile("def test_inner(): pass")\n'
        '    assert file.is_file()\n'
        '    assert parse([1]) == [1]\n')
    candidate = workspace.publish(CandidateDraft((DraftFile('tests/test_repro.py', source.encode()),),
        snapshot.snapshot_id, 'contract-1', 1, 'exercise pytester file creation'), snapshot)
    runner = Runner(workspace, store)

    async def execute_twice():
        environment = await runner.prepare(request, snapshot, facts.context())
        return [await runner.execute(candidate, snapshot, environment, facts.context()) for _ in range(2)]

    runs = asyncio.run(execute_twice())
    roots = []
    for result in runs:
        assert result.raw.exit_code == 0, result.observation.tests
        assert result.raw.cleanup_ok and result.protection.ok
        assert result.observation.probe_complete
        spec = json.loads((store.root / 'runs' / result.run_id / 'spec.json').read_text(encoding='utf-8'))
        temp = Path(spec['env_overrides']['TMP'])
        assert spec['env_overrides']['TEMP'] == str(temp)
        assert not temp.is_relative_to(store.root)
        assert not temp.exists()
        assert result.observation.framework_details['temp_root'] == str(temp)
        assert result.observation.framework_details['temp_cleanup_ok'] is True
        roots.append(temp)
    assert roots[0] != roots[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows private temp lifecycle')
def test_backend_exception_still_releases_private_temp(tmp_path, projects, facts, monkeypatch):
    from tests.integration.test_runner import setup_runner
    _, request, _, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts)

    async def scenario():
        environment = await runner.prepare(request, snapshot, facts.context())
        seen = []
        async def fail(spec, context):
            seen.append(Path(spec.env_overrides['TMP']))
            assert seen[-1].is_dir()
            raise RuntimeError('controlled backend failure')
        monkeypatch.setattr(runner.backend, 'execute', fail)
        with pytest.raises(RuntimeError, match='controlled backend failure'):
            await runner.execute(candidate, snapshot, environment, facts.context())
        assert len(seen) == 1 and not seen[0].exists()
        assert not seen[0].is_relative_to(runner.store.root)
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != 'nt', reason='Windows private temp lifecycle')
def test_temp_cleanup_failure_invalidates_process_cleanup(tmp_path, projects, facts, monkeypatch):
    import reproagent.runner as module
    from tests.integration.test_runner import setup_runner, execute
    _, request, _, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts)
    owned = []
    real_remove = module._remove_private_temp
    def fail(path, parent):
        owned.append((path, parent))
        return False
    monkeypatch.setattr(module, '_remove_private_temp', fail)
    try:
        result = execute(request, snapshot, candidate, runner, facts.context())
        assert not result.raw.cleanup_ok
        assert result.observation.framework_details['temp_cleanup_ok'] is False
        assert not result.protection.ok
    finally:
        for path, parent in owned:
            assert real_remove(path, parent)


def test_private_temp_cleanup_refuses_an_unowned_directory(tmp_path):
    from reproagent.runner import _remove_private_temp
    parent = tmp_path / 'owned'; parent.mkdir()
    outside = tmp_path / 'ra-outside'; outside.mkdir()
    marker = outside / 'keep.txt'; marker.write_text('preserve', encoding='utf-8')
    assert not _remove_private_temp(outside, parent)
    assert marker.read_text(encoding='utf-8') == 'preserve'
