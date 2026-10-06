import asyncio
import importlib
import sys
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateDraft, DraftFile, PythonPytestConfig, TaskRequest
from reproagent.store import TaskStore
from reproagent.workspace import Workspace


def setup_runner(tmp_path, projects, facts, style='plain', text=None, parent='tests'):
    repo = getattr(projects, style)(tmp_path / 'repo')
    request = TaskRequest(repo, tmp_path / 'out', repo / 'issue.md', language=PythonPytestConfig(python=sys.executable, source_roots=('src',) if style == 'src_layout' else ('.',), target_modules=('example.parser',), candidate_parent=parent))
    store = TaskStore(request.output_dir)
    workspace = Workspace(request.output_dir, store)
    snapshot = workspace.freeze(request, facts.context())
    candidate = workspace.publish(CandidateDraft((DraftFile(f'{parent}/test_repro.py', (text or 'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n').encode()),), snapshot.snapshot_id, 'contract-1', 1, 'empty input'), snapshot)
    runner = importlib.import_module('reproagent.runner').Runner(workspace, store)
    return repo, request, workspace, snapshot, candidate, runner


def execute(request, snapshot, candidate, runner, context):
    async def go():
        env = await runner.prepare(request, snapshot, context)
        return await runner.execute(candidate, snapshot, env, context)
    return asyncio.run(go())


def test_src_layout_executes_snapshot_not_installed_old_package(tmp_path, projects, facts):
    _, request, workspace, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts, 'src_layout')
    result = execute(request, snapshot, candidate, runner, facts.context())
    assert result.observation.executed and result.observation.probe_complete
    assert result.raw.exit_code == 1
    assert all(__import__('pathlib').Path(p).is_relative_to(workspace.root / 'runs' / result.run_id / 'code') for p in result.observation.target_origins.values())
    assert result.observation.framework_details['target_calls']['example.parser'] > 0


def test_candidate_inherits_nested_conftest(tmp_path, projects, facts):
    repo = projects.plain(tmp_path / 'repo')
    (repo / 'tests/nested').mkdir()
    (repo / 'tests/nested/conftest.py').write_text('import pytest\n@pytest.fixture\ndef values(): return []\n')
    request = TaskRequest(repo, tmp_path / 'out', repo / 'issue.md', language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',), candidate_parent='tests/nested'))
    store = TaskStore(request.output_dir); workspace = Workspace(request.output_dir, store)
    snapshot = workspace.freeze(request, facts.context())
    candidate = workspace.publish(CandidateDraft((DraftFile('tests/nested/test_repro.py', b'from example.parser import parse\ndef test_empty(values): assert parse(values) == []\n'),), snapshot.snapshot_id, 'contract-1', 1, 'empty'), snapshot)
    runner = importlib.import_module('reproagent.runner').Runner(workspace, store)
    result = execute(request, snapshot, candidate, runner, facts.context())
    assert result.observation.executed and result.raw.exit_code == 1
    assert 'IndexError' in str(result.observation.tests)


def test_baseline_assertion_failure_does_not_block_environment(tmp_path, projects, facts):
    _, request, _, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts)
    request = replace(request, language=replace(request.language, baseline_tests=('tests/test_existing.py',)))
    (snapshot.root / 'tests/test_existing.py').write_text('def test_bad(): assert False\n')
    # Re-freeze the user's changed source rather than mutating the trusted snapshot.
    (request.repo / 'tests/test_existing.py').write_text('def test_bad(): assert False\n')
    snapshot = runner.workspace.freeze(request, facts.context())
    env = asyncio.run(runner.prepare(request, snapshot, facts.context()))
    assert env.python == sys.executable


def test_wrong_origin_and_probe_disabled_are_not_accepted(tmp_path, projects, facts):
    adapter = importlib.import_module('reproagent.adapters.languages.python_pytest.adapter').PythonPytestAdapter()
    _, request, _, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts)
    for option in ('--collect-only', '-p', '-n', '--setup-only'):
        bad = replace(request, language=replace(request.language, pytest_args=(option,)))
        with pytest.raises(ValueError): asyncio.run(runner.prepare(bad, snapshot, facts.context()))
    from reproagent.core.models import TestObservation
    checks = adapter.check_framework(candidate, TestObservation(True, True, True, target_origins={'example': str(tmp_path / 'old/example.py')}, framework_details={'source_binding_ok': False}))
    assert checks.invalid


def test_run_hash_is_bound_to_frozen_candidate(tmp_path, projects, facts):
    _, request, _, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts)
    result = execute(request, snapshot, candidate, runner, facts.context())
    assert (result.candidate_id, result.manifest_hash, result.snapshot_id) == (candidate.candidate_id, candidate.manifest_hash, snapshot.snapshot_id)
    assert result.protection.ok
    env = asyncio.run(runner.prepare(request, snapshot, facts.context()))
    with pytest.raises(ValueError): asyncio.run(runner.execute(replace(candidate, manifest_hash='wrong'), snapshot, env, facts.context()))
    with pytest.raises(ValueError): asyncio.run(runner.execute(candidate, replace(snapshot, snapshot_id='wrong'), env, facts.context()))
