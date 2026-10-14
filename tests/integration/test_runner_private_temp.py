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
    async def fail(path, parent, context):
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
            assert asyncio.run(real_remove(path, parent, facts.context()))


def test_private_temp_cleanup_refuses_an_unowned_directory(tmp_path, facts):
    from reproagent.runner import _remove_private_temp
    parent = tmp_path / 'owned'; parent.mkdir()
    outside = tmp_path / 'ra-outside'; outside.mkdir()
    marker = outside / 'keep.txt'; marker.write_text('preserve', encoding='utf-8')
    assert not asyncio.run(_remove_private_temp(outside, parent, facts.context()))
    assert marker.read_text(encoding='utf-8') == 'preserve'


@pytest.mark.parametrize('limit', ['cleanup', 'task', 'cancel'])
def test_private_temp_cleanup_stops_between_files(tmp_path, facts, monkeypatch, limit):
    from reproagent.core.models import BudgetLimits
    from reproagent.runner import _remove_private_temp
    parent = tmp_path.resolve()
    root = parent / 'ra-budget'; root.mkdir()
    for number in range(5):
        (root / str(number)).write_text('temporary', encoding='utf-8')
    context = facts.context(limits=BudgetLimits(cleanup_timeout_seconds=100 if limit == 'task' else 0.05))
    now = [0.0]
    context.budget.clock = lambda: now[0]
    context.budget.deadline = 0.05 if limit == 'task' else 100
    real_unlink = Path.unlink
    removed = []
    def unlink(path, *args, **kwargs):
        real_unlink(path, *args, **kwargs)
        removed.append(path)
        if limit == 'cancel':
            context.cancel_event.set()
        else:
            now[0] += 0.06
    monkeypatch.setattr(Path, 'unlink', unlink)
    assert asyncio.run(_remove_private_temp(root, parent, context)) is False
    assert len(removed) == 1
    assert len(list(root.iterdir())) == 4


def test_missing_child_does_not_masquerade_as_complete_cleanup(tmp_path, facts, monkeypatch):
    from reproagent.runner import _remove_private_temp
    root = tmp_path.resolve() / 'ra-missing'; root.mkdir()
    (root / 'first').write_text('temporary', encoding='utf-8')
    (root / 'second').write_text('temporary', encoding='utf-8')
    real_unlink = Path.unlink
    def disappeared(path, *args, **kwargs):
        real_unlink(path, *args, **kwargs)
        raise FileNotFoundError('concurrent disappearance')
    monkeypatch.setattr(Path, 'unlink', disappeared)
    assert asyncio.run(_remove_private_temp(root, tmp_path.resolve(), facts.context())) is False
    assert root.is_dir()


@pytest.mark.skipif(os.name != 'nt', reason='Windows private temp lifecycle')
def test_cancelled_cleanup_cannot_claim_integrity(tmp_path, projects, facts, monkeypatch):
    from tests.integration.test_runner import setup_runner
    _, request, _, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts)
    context = facts.context()
    owned = []
    real_execute = runner.backend.execute
    async def backend(spec, ctx):
        result = await real_execute(spec, ctx)
        owned.append(Path(spec.env_overrides['TMP']))
        ctx.cancel_event.set()
        return result
    async def scenario():
        environment = await runner.prepare(request, snapshot, context)
        monkeypatch.setattr(runner.backend, 'execute', backend)
        return await runner.execute(candidate, snapshot, environment, context)
    try:
        result = asyncio.run(scenario())
        assert not result.raw.cleanup_ok
        assert not result.protection.ok
        assert result.observation.framework_details['temp_cleanup_ok'] is False
    finally:
        import shutil
        for root in owned:
            if root.exists():
                shutil.rmtree(root)
