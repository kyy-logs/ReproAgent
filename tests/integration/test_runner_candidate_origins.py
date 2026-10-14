import asyncio
import sys
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateDraft, DraftFile, PythonPytestConfig, TaskRequest
from reproagent.runner import Runner
from reproagent.store import TaskStore
from reproagent.workspace import Workspace


@pytest.mark.parametrize('mode', ['original', 'candidate_only', 'tampered', 'external'])
def test_candidate_under_target_package_is_separate_from_original_evidence(tmp_path, projects, facts, mode):
    repo = projects.plain(tmp_path / 'repo')
    nested = repo / 'example/tests'
    nested.mkdir()
    (nested / '__init__.py').write_text('')
    request = TaskRequest(repo, tmp_path / 'out', repo / 'issue.md', language=PythonPytestConfig(
        python=sys.executable, source_roots=('.',), target_modules=('example',), candidate_parent='example/tests'))
    store = TaskStore(request.output_dir)
    workspace = Workspace(request.output_dir, store)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    candidate = workspace.publish(CandidateDraft((DraftFile('example/tests/test_repro.py',
        b'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n'),),
        snapshot.snapshot_id, 'contract', 1, 'nested candidate'), snapshot)
    runner = Runner(workspace, store)
    normalize = runner.adapter.normalize
    def observation(raw, artifacts):
        result = normalize(raw, artifacts)
        if mode == 'original':
            return result
        name = 'example.tests.test_repro'
        path = result.target_origins[name]
        if mode == 'tampered':
            from pathlib import Path
            Path(path).write_text('changed')
        if mode == 'external':
            path = str(tmp_path / 'outside.py')
        return replace(result, target_origins={name: path}, framework_details=dict(
            result.framework_details, target_calls={name: 1}))
    runner.adapter.normalize = observation
    async def run():
        environment = await runner.prepare(request, snapshot, context)
        return await runner.execute(candidate, snapshot, environment, context)
    result = asyncio.run(run())
    details = result.observation.framework_details
    assert result.raw.stop_reason == 'EXITED'
    assert result.observation.probe_complete
    if mode == 'original':
        assert result.protection.ok
        assert details['source_binding_ok'] is True
        assert 'example.tests.test_repro' not in result.observation.target_origins
        assert 'example.tests.test_repro' not in details['target_calls']
        assert details['candidate_target_origins']['example.tests.test_repro']
        assert details['target_calls']['example.parser'] > 0
    else:
        assert details['source_binding_ok'] is False
        if mode == 'candidate_only':
            assert not result.observation.target_origins
            assert not details['target_calls']
        else:
            assert 'example.tests.test_repro' in result.observation.target_origins
