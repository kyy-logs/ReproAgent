import asyncio
import os
import shutil
import subprocess

import pytest

from reproagent import experience as exp
from reproagent.core.models import ModelConfig, TaskRequest, FixValidationRequest
from reproagent.paths import directory_path, workspace_path
from reproagent.experience_storage import migrate_experience_library
from tests.unit.test_experience_store import card, library
from tests.integration.test_swt_batch import inputs
from evals.swt_bench.run import run_batch
from evals.schema import EvalResult


def redirect_root(root, target):
    root.rename(root.with_name(root.name + '-old'))
    if os.name == 'nt':
        process = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(root), str(target)], capture_output=True)
        assert process.returncode == 0, process.stderr.decode(errors='replace')
    else:
        root.symlink_to(target, target_is_directory=True)


def test_approved_root_cannot_be_redirected_into_protected_repository(tmp_path):
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    repo, fixed, output = tmp_path / 'repo', tmp_path / 'fixed', tmp_path / 'output'
    repo.mkdir(); fixed.mkdir()
    destination = fixed / 'memory'
    shutil.copytree(root, destination)
    request = TaskRequest(repo, output, tmp_path / 'issue.md', experience_file=root)
    approved = exp.validate_experience_path(root, request, FixValidationRequest(fixed))
    frozen = exp.load_experience_snapshot(approved)
    original = (destination / 'model/experiences.json').read_bytes()
    redirect_root(root, destination)
    assert exp.load_experience_snapshot(approved).state == 'store_error'
    assert exp.append_experience(approved, card('new'), None) == 'store_error'
    assert (destination / 'model/experiences.json').read_bytes() == original
    assert frozen.cards == (card(),)


def test_frozen_round_rejects_identical_body_at_redirected_root(tmp_path, projects):
    catalog, manifest, bindings = inputs(tmp_path, projects)
    bindings['fixture__repo-2'] = {**bindings['fixture__repo-1'], 'instance_id':'fixture__repo-2'}
    root = tmp_path / 'memory'
    exp.append_experience(root, card(), None)
    destination = tmp_path / 'fixed/shared-memory'
    shutil.copytree(root, destination)
    from evals.swt_bench.prepare import source_hash
    for binding in bindings.values(): binding['fixed_source_hash'] = source_hash(tmp_path / 'fixed')
    calls = []
    async def run_one(case, model, output, **kwargs):
        calls.append(case.case_id)
        if len(calls) == 1: redirect_root(root, destination)
        return EvalResult(case.case_id, status='EXHAUSTED', duration=1)
    result = asyncio.run(run_batch(catalog, manifest, bindings, ModelConfig(), tmp_path / 'round', run_one=run_one, experience_file=root))
    assert calls == ['fixture__repo-1']
    assert result['outcomes']['fixture__repo-2']['status'] == 'EXPERIENCE_SOURCE_CHANGED'
    assert result['source_comparison_status'] == 'changed'


def test_legacy_file_parent_redirection_is_rejected_before_lock_write(tmp_path):
    root = tmp_path / 'library-parent'
    source = root / 'old.json'
    exp.append_experience(source, card(), None)
    repo, fixed = tmp_path / 'repo', tmp_path / 'fixed'
    repo.mkdir(); fixed.mkdir()
    destination = fixed / 'memory'
    shutil.copytree(root, destination)
    (destination / 'old.json.lock').unlink()
    approved = exp.validate_experience_path(source, TaskRequest(repo, tmp_path / 'output', tmp_path / 'issue.md'), FixValidationRequest(fixed))
    original = (destination / 'old.json').read_bytes()
    redirect_root(root, destination)
    assert exp.load_experience_snapshot(approved).state == 'store_error'
    assert exp.append_experience(approved, card('new'), None) == 'store_error'
    assert (destination / 'old.json').read_bytes() == original
    assert not (destination / 'old.json.lock').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows MAX_PATH regression')
def test_migration_promotes_generated_staging_child_paths(tmp_path):
    source = tmp_path / 'old.json'
    library(source, card())
    parent = tmp_path / ('x' * (226 - len(str(tmp_path)) - 1))
    directory_path(parent).mkdir()
    target = parent / 'new'
    assert len(str(target)) == 230
    assert migrate_experience_library(source, target)['code'] == 'migrated'
    assert exp.load_experience_snapshot(target).cards == (card(),)
    assert not list(workspace_path(parent).glob('.new.migrate-*'))
