import hashlib
import json
import os
import stat
import time
from pathlib import Path

import pytest

from reproagent.integration import manifest as manifest_module
from reproagent.integration.manifest import (
    IncompleteTransaction,
    InstallBusy,
    ManifestError,
    PlanConflict,
    RollbackIncomplete,
    apply_plan,
    classify_file,
    preflight,
    read_manifest,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _roots(home: Path) -> dict:
    return {
        'claude-code': (home / '.claude' / 'skills' / 'reproagent',),
        'codex': (home / '.agents' / 'skills' / 'reproagent',
                  home / '.codex' / 'skills' / 'reproagent'),
    }


def _plan(root: Path, files: dict[str, bytes], *, action='install', force=False,
          host='codex', layout='standard') -> dict:
    operations = []
    for relative, content in files.items():
        path = root / relative
        operations.append({
            'op': 'create' if action == 'install' else 'remove',
            'host': host,
            'layout': layout,
            'root': str(root),
            'relative_path': relative,
            'path': str(path),
            'expected_sha256': None,
            'desired_sha256': _sha(content) if action == 'install' else None,
            'content': content if action == 'install' else None,
        })
    return {'action': action, 'force': force,
            'hosts': [{'host': host, 'layout': layout, 'root': str(root)}],
            'operations': operations}


def _manifest_path(home: Path) -> Path:
    return home / '.reproagent' / 'integrations.json'


def test_first_install_creates_files_and_manifest(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    plan = _plan(root, {'SKILL.md': b'skill', 'scripts/reproagent.sh': b'#!/bin/sh\n'})

    result = apply_plan(plan, manifest_path=_manifest_path(home),
                        allowed_roots=_roots(home))

    assert result['changed'] is True
    assert (root / 'SKILL.md').read_bytes() == b'skill'
    saved = read_manifest(_manifest_path(home), allowed_roots=_roots(home))
    assert saved['roots'][0]['files'][0]['path'] == 'SKILL.md'
    assert not _manifest_path(home).with_name('integration.lock').exists()
    assert not _manifest_path(home).with_name('integration-transaction.json').exists()


def test_idempotent_reinstall_does_not_rewrite(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    plan = _plan(root, {'SKILL.md': b'skill'})
    apply_plan(plan, manifest_path=_manifest_path(home), allowed_roots=_roots(home))
    file_time = os.stat(root / 'SKILL.md').st_mtime_ns
    manifest_time = os.stat(_manifest_path(home)).st_mtime_ns
    time.sleep(0.02)

    result = apply_plan(plan, manifest_path=_manifest_path(home), allowed_roots=_roots(home))

    assert result['changed'] is False
    assert os.stat(root / 'SKILL.md').st_mtime_ns == file_time
    assert os.stat(_manifest_path(home)).st_mtime_ns == manifest_time


def test_owned_update_replaces_the_file(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'v1'}), manifest_path=manifest_path,
               allowed_roots=_roots(home))

    result = apply_plan(_plan(root, {'SKILL.md': b'v2'}), manifest_path=manifest_path,
                        allowed_roots=_roots(home))

    assert result['changed'] is True
    assert (root / 'SKILL.md').read_bytes() == b'v2'
    saved = read_manifest(manifest_path, allowed_roots=_roots(home))
    assert saved['roots'][0]['files'][0]['sha256'] == _sha(b'v2')


def test_second_host_conflict_prevents_all_writes(tmp_path):
    home = tmp_path / 'home'
    codex_root = home / '.agents' / 'skills' / 'reproagent'
    claude_root = home / '.claude' / 'skills' / 'reproagent'
    (claude_root / 'scripts').mkdir(parents=True)
    (claude_root / 'scripts' / 'reproagent.sh').write_bytes(b'user-owned')
    plan = _plan(codex_root, {'SKILL.md': b'skill'})
    plan['operations'].append({
        'op': 'create', 'host': 'claude-code', 'layout': 'user',
        'root': str(claude_root), 'relative_path': 'scripts/reproagent.sh',
        'path': str(claude_root / 'scripts' / 'reproagent.sh'),
        'expected_sha256': None, 'desired_sha256': _sha(b'ours'),
        'content': b'ours'})

    with pytest.raises(PlanConflict):
        apply_plan(plan, manifest_path=_manifest_path(home), allowed_roots=_roots(home))

    assert not (codex_root / 'SKILL.md').exists()
    assert (claude_root / 'scripts' / 'reproagent.sh').read_bytes() == b'user-owned'


def test_force_backs_up_and_takes_ownership(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_bytes(b'user-owned')
    manifest_path = _manifest_path(home)

    apply_plan(_plan(root, {'SKILL.md': b'first'}, force=True), manifest_path=manifest_path,
               allowed_roots=_roots(home))
    first_backups = sorted(root.glob('SKILL.md.reproagent-backup-*'))
    (root / 'SKILL.md').write_bytes(b'user-owned-2')
    apply_plan(_plan(root, {'SKILL.md': b'second'}, force=True), manifest_path=manifest_path,
               allowed_roots=_roots(home))
    second_backups = sorted(root.glob('SKILL.md.reproagent-backup-*'))

    assert len(first_backups) == 1
    assert len(second_backups) == 2
    assert first_backups[0].read_bytes() == b'user-owned'
    assert second_backups[1].read_bytes() == b'user-owned-2'
    assert first_backups[0] in second_backups
    saved = read_manifest(manifest_path, allowed_roots=_roots(home))
    assert len(saved['roots'][0]['backups']) == 2


def test_identical_unmanaged_file_is_not_owned_or_removed(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_bytes(b'skill')
    manifest_path = _manifest_path(home)

    result = apply_plan(_plan(root, {'SKILL.md': b'skill'}), manifest_path=manifest_path,
                        allowed_roots=_roots(home))

    assert result['changed'] is False
    assert not manifest_path.exists()
    assert (root / 'SKILL.md').read_bytes() == b'skill'
    uninstall = _plan(root, {'SKILL.md': b'skill'}, action='uninstall')
    apply_plan(uninstall, manifest_path=manifest_path, allowed_roots=_roots(home))
    assert (root / 'SKILL.md').read_bytes() == b'skill'


def test_uninstall_preserves_user_modified_files(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'skill',
                            'scripts/reproagent.sh': b'#!/bin/sh\n'}),
               manifest_path=manifest_path, allowed_roots=_roots(home))
    (root / 'SKILL.md').write_bytes(b'user-edit')

    result = apply_plan(_plan(root, {'SKILL.md': b'', 'scripts/reproagent.sh': b''},
                              action='uninstall'),
                        manifest_path=manifest_path, allowed_roots=_roots(home))

    assert (root / 'SKILL.md').read_bytes() == b'user-edit'
    assert not (root / 'scripts' / 'reproagent.sh').exists()
    saved = read_manifest(manifest_path, allowed_roots=_roots(home))
    assert [entry['path'] for entry in saved['roots'][0]['files']] == ['SKILL.md']
    assert result['preserved']


def test_manifest_cannot_authorise_an_outside_root(tmp_path):
    home = tmp_path / 'home'
    manifest_path = _manifest_path(home)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        'schema_version': 1, 'installer_version': '0.1.0',
        'roots': [{'host': 'codex', 'layout': 'standard',
                   'root': str(tmp_path / 'outside'), 'files': []}]}), encoding='utf-8')

    with pytest.raises(ManifestError):
        read_manifest(manifest_path, allowed_roots=_roots(home))


@pytest.mark.parametrize('relative', ['../outside', '/absolute', 'other.txt'])
def test_manifest_cannot_name_an_unapproved_relative_path(tmp_path, relative):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        'schema_version': 1, 'installer_version': '0.1.0',
        'roots': [{'host': 'codex', 'layout': 'standard', 'root': str(root),
                   'files': [{'path': relative, 'sha256': '0' * 64,
                              'absolute': str(root / relative)}]}]}), encoding='utf-8')

    with pytest.raises(ManifestError):
        read_manifest(manifest_path, allowed_roots=_roots(home))


def test_corrupt_or_unknown_manifest_is_rejected(tmp_path):
    home = tmp_path / 'home'
    manifest_path = _manifest_path(home)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ManifestError):
        read_manifest(manifest_path, allowed_roots=_roots(home))
    manifest_path.write_text(json.dumps({'schema_version': 99, 'roots': []}),
                             encoding='utf-8')
    with pytest.raises(ManifestError):
        read_manifest(manifest_path, allowed_roots=_roots(home))


def test_symlinked_parent_is_rejected(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    root.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (root / 'scripts').symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')

    with pytest.raises(ManifestError):
        apply_plan(_plan(root, {'scripts/reproagent.sh': b'#!/bin/sh\n'}),
                   manifest_path=_manifest_path(home), allowed_roots=_roots(home))


def test_file_write_failure_rolls_back_previous_files(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    original = manifest_module._atomic_write_bytes

    def failing(path, data, mode=None, **kwargs):
        if Path(path).name == 'reproagent.sh':
            raise OSError('injected write failure')
        return original(path, data, mode, **kwargs)

    monkeypatch.setattr(manifest_module, '_atomic_write_bytes', failing)

    with pytest.raises(manifest_module.InstallFailed):
        apply_plan(_plan(root, {'SKILL.md': b'skill',
                                'scripts/reproagent.sh': b'#!/bin/sh\n'}),
                   manifest_path=manifest_path, allowed_roots=_roots(home))

    assert not (root / 'SKILL.md').exists()
    assert not manifest_path.exists()
    assert not manifest_path.with_name('integration-transaction.json').exists()


def test_manifest_write_failure_restores_old_manifest(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'v1'}), manifest_path=manifest_path,
               allowed_roots=_roots(home))
    old_manifest = manifest_path.read_bytes()

    monkeypatch.setattr(manifest_module, '_write_manifest',
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(manifest_module.InstallFailed):
        apply_plan(_plan(root, {'SKILL.md': b'v2'}), manifest_path=manifest_path,
                   allowed_roots=_roots(home))

    assert (root / 'SKILL.md').read_bytes() == b'v1'
    assert manifest_path.read_bytes() == old_manifest
    assert not manifest_path.with_name('integration-transaction.json').exists()


def test_rollback_does_not_overwrite_a_concurrent_user_edit(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    original = manifest_module._atomic_write_bytes

    def failing(path, data, mode=None, **kwargs):
        target = Path(path)
        if target.name == 'reproagent.sh':
            (root / 'SKILL.md').write_bytes(b'concurrent-user-edit')
            raise OSError('injected write failure')
        return original(path, data, mode, **kwargs)

    monkeypatch.setattr(manifest_module, '_atomic_write_bytes', failing)

    with pytest.raises(RollbackIncomplete):
        apply_plan(_plan(root, {'SKILL.md': b'skill',
                                'scripts/reproagent.sh': b'#!/bin/sh\n'}),
                   manifest_path=manifest_path, allowed_roots=_roots(home))

    assert (root / 'SKILL.md').read_bytes() == b'concurrent-user-edit'
    assert manifest_path.with_name('integration-transaction.json').exists()


def test_leftover_transaction_blocks_even_force(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.with_name('integration-transaction.json').write_text(
        json.dumps({'transaction_id': 'previous'}), encoding='utf-8')

    with pytest.raises(IncompleteTransaction):
        apply_plan(_plan(root, {'SKILL.md': b'skill'}, force=True),
                   manifest_path=manifest_path, allowed_roots=_roots(home))
    assert not (root / 'SKILL.md').exists()


def test_existing_lock_blocks_apply(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'
    manifest_path = _manifest_path(home)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.with_name('integration.lock').write_text('busy', encoding='utf-8')

    with pytest.raises(InstallBusy):
        apply_plan(_plan(root, {'SKILL.md': b'skill'}),
                   manifest_path=manifest_path, allowed_roots=_roots(home))


def test_classify_file_states(tmp_path):
    path = tmp_path / 'SKILL.md'
    assert classify_file(path, b'x', None) == 'missing'
    path.write_bytes(b'x')
    assert classify_file(path, b'x', None) == 'unmanaged_matching'
    assert classify_file(path, b'x', {'path': 'SKILL.md', 'sha256': _sha(b'x')}) == 'owned_matching'
    assert classify_file(path, b'x', {'path': 'SKILL.md', 'sha256': _sha(b'old')}) == 'conflict'
    path.write_bytes(b'other')
    assert classify_file(path, b'x', None) == 'conflict'


def test_preflight_is_read_only_and_reports_decisions(tmp_path):
    home = tmp_path / 'home'
    root = home / '.agents' / 'skills' / 'reproagent'

    decisions = preflight(_plan(root, {'SKILL.md': b'skill'}),
                          manifest_path=_manifest_path(home), allowed_roots=_roots(home))

    assert decisions[0]['decision'] == 'create'
    assert not home.exists()


@pytest.mark.parametrize('component', ['.agents', '.reproagent'])
def test_existing_ancestor_link_is_rejected_before_writes(tmp_path, monkeypatch, component):
    home = tmp_path / 'home'
    ancestor = home / component
    ancestor.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = manifest_module._is_link_or_reparse
    monkeypatch.setattr(manifest_module, '_is_link_or_reparse',
                        lambda path: Path(path) == ancestor or original(path))
    root = home / '.agents/skills/reproagent'

    with pytest.raises(ManifestError):
        apply_plan(_plan(root, {'SKILL.md': b'skill'}),
                   manifest_path=_manifest_path(home), allowed_roots=_roots(home))

    assert not (root / 'SKILL.md').exists()
    assert not _manifest_path(home).exists()
    assert not list(outside.iterdir())


@pytest.mark.parametrize('action,force', [('install', False), ('install', True),
                                        ('uninstall', False)])
def test_user_edit_between_forward_operations_is_preserved(tmp_path, monkeypatch, action, force):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    manifest = _manifest_path(home)
    original_files = {'SKILL.md': b'old skill', 'scripts/reproagent.sh': b'old script'}
    apply_plan(_plan(root, original_files), manifest_path=manifest, allowed_roots=_roots(home))
    old_manifest = manifest.read_bytes()
    original = manifest_module._write_transaction
    edited = []

    def edit_after_first_write(path, transaction):
        result = original(path, transaction)
        if len(transaction['completed']) == 1 and not edited:
            (root / 'scripts/reproagent.sh').write_bytes(b'user concurrent edit')
            edited.append(True)
        return result

    monkeypatch.setattr(manifest_module, '_write_transaction', edit_after_first_write)
    with pytest.raises(manifest_module.InstallFailed) as error:
        apply_plan(_plan(root, {'SKILL.md': b'new skill',
                               'scripts/reproagent.sh': b'new script'},
                         action=action, force=force),
                   manifest_path=manifest, allowed_roots=_roots(home))

    assert error.value.code == 'concurrent_file_change'
    assert edited
    assert (root / 'SKILL.md').read_bytes() == b'old skill'
    assert (root / 'scripts/reproagent.sh').read_bytes() == b'user concurrent edit'
    assert manifest.read_bytes() == old_manifest
    assert not manifest.with_name('integration-transaction.json').exists()


def test_user_create_between_forward_operations_is_preserved(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    original = manifest_module._write_transaction

    def create_after_first_write(path, transaction):
        result = original(path, transaction)
        if len(transaction['completed']) == 1:
            (root / 'scripts').mkdir(exist_ok=True)
            (root / 'scripts/reproagent.sh').write_bytes(b'user file')
        return result

    monkeypatch.setattr(manifest_module, '_write_transaction', create_after_first_write)
    with pytest.raises(manifest_module.InstallFailed) as error:
        apply_plan(_plan(root, {'SKILL.md': b'skill', 'scripts/reproagent.sh': b'script'}),
                   manifest_path=_manifest_path(home), allowed_roots=_roots(home))
    assert error.value.code == 'concurrent_file_change'
    assert (root / 'scripts/reproagent.sh').read_bytes() == b'user file'
    assert not (root / 'SKILL.md').exists()


@pytest.mark.parametrize('component', ['.agents', '.reproagent'])
def test_real_ancestor_directory_link_is_rejected(tmp_path, component):
    home = tmp_path / 'home'
    home.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = home / component
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        root = home / '.agents/skills/reproagent'
        with pytest.raises(ManifestError):
            apply_plan(_plan(root, {'SKILL.md': b'skill'}),
                       manifest_path=_manifest_path(home), allowed_roots=_roots(home))
        assert not list(outside.iterdir())
        assert not _manifest_path(home).exists()
    finally:
        if os.name == 'nt':
            link.rmdir()  # Remove only the junction; never recursively traverse it.
        else:
            link.unlink()


def test_edit_during_staging_is_preserved(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    path = root / 'SKILL.md'
    manifest = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'old'}), manifest_path=manifest, allowed_roots=_roots(home))
    original = manifest_module._atomic_write_bytes

    def edit_after_staging(target, data, mode=None, **kwargs):
        result = original(target, data, mode, **kwargs)
        if Path(target).parent.name.startswith('.transaction-'):
            path.write_bytes(b'user edit during staging')
        return result

    monkeypatch.setattr(manifest_module, '_atomic_write_bytes', edit_after_staging)
    with pytest.raises(manifest_module.InstallFailed) as error:
        apply_plan(_plan(root, {'SKILL.md': b'new'}), manifest_path=manifest, allowed_roots=_roots(home))
    assert error.value.code == 'concurrent_file_change'
    assert path.read_bytes() == b'user edit during staging'


@pytest.mark.parametrize('invalid', [
    {'tool_python': '/python', 'api_key': 'fake-secret'},
    {'tool_python': 'relative', 'model_config': None, 'repro_root': None, 'env_file': None},
])
def test_manifest_rejects_unsafe_binding_metadata(tmp_path, invalid):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    path = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'old'}), manifest_path=path, allowed_roots=_roots(home))
    data = json.loads(path.read_text())
    data['roots'][0]['binding'] = invalid
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError) as error:
        read_manifest(path, allowed_roots=_roots(home))
    assert error.value.code == 'manifest_binding_invalid'


@pytest.mark.parametrize('existing', [False, True])
def test_user_save_during_target_fsync_is_preserved(tmp_path, monkeypatch, existing):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    path = root / 'SKILL.md'
    manifest = _manifest_path(home)
    if existing:
        apply_plan(_plan(root, {'SKILL.md': b'old'}), manifest_path=manifest, allowed_roots=_roots(home))
    mkstemp = manifest_module.tempfile.mkstemp
    fsync = manifest_module.os.fsync
    target_descriptors = set()
    edited = []

    def capture(*args, **kwargs):
        descriptor, name = mkstemp(*args, **kwargs)
        if Path(kwargs.get('dir', '')) == root:
            target_descriptors.add(descriptor)
        return descriptor, name

    def save_during_fsync(descriptor):
        fsync(descriptor)
        if descriptor in target_descriptors and not edited:
            path.write_bytes(b'user save during fsync')
            edited.append(True)

    monkeypatch.setattr(manifest_module.tempfile, 'mkstemp', capture)
    monkeypatch.setattr(manifest_module.os, 'fsync', save_during_fsync)
    with pytest.raises(manifest_module.InstallFailed) as error:
        apply_plan(_plan(root, {'SKILL.md': b'new'}), manifest_path=manifest, allowed_roots=_roots(home))
    assert error.value.code == 'concurrent_file_change'
    assert path.read_bytes() == b'user save during fsync'


def test_read_manifest_rejects_redirected_installed_root(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    path = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'old'}), manifest_path=path, allowed_roots=_roots(home))
    original = manifest_module._is_link_or_reparse
    monkeypatch.setattr(manifest_module, '_is_link_or_reparse',
                        lambda target: Path(target) == home / '.agents' or original(target))
    with pytest.raises(ManifestError):
        read_manifest(path, allowed_roots=_roots(home))


def test_redirected_control_directory_is_not_read_during_rollback(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    manifest = _manifest_path(home)
    apply_plan(_plan(root, {'SKILL.md': b'old', 'scripts/reproagent.sh': b'old script'}),
               manifest_path=manifest, allowed_roots=_roots(home))
    redirected = []
    is_link = manifest_module._is_link_or_reparse
    write_journal = manifest_module._write_transaction
    read_bytes = Path.read_bytes

    def change_control_path(path, transaction):
        result = write_journal(path, transaction)
        if len(transaction['completed']) == 1:
            redirected.append(True)
        return result

    def guarded_read(path):
        if redirected and path.parent.name.startswith('.transaction-'):
            pytest.fail('rollback must not read a redirected staged file')
        return read_bytes(path)

    monkeypatch.setattr(manifest_module, '_write_transaction', change_control_path)
    monkeypatch.setattr(manifest_module, '_is_link_or_reparse',
                        lambda path: (bool(redirected) and Path(path) == manifest.parent)
                        or is_link(path))
    monkeypatch.setattr(Path, 'read_bytes', guarded_read)
    with pytest.raises(RollbackIncomplete):
        apply_plan(_plan(root, {'SKILL.md': b'new', 'scripts/reproagent.sh': b'new script'}),
                   manifest_path=manifest, allowed_roots=_roots(home))
    assert (root / 'SKILL.md').read_bytes() == b'new'
    assert (root / 'scripts/reproagent.sh').read_bytes() == b'old script'
    assert manifest.with_name('integration-transaction.json').exists()
    assert manifest.with_name('integration.lock').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows rename has exclusive destination semantics')
def test_windows_install_does_not_require_hardlink_permission(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    monkeypatch.setattr(manifest_module.os, 'link',
                        lambda *args, **kwargs: pytest.fail('Windows installs must not need hardlinks'))
    result = apply_plan(_plan(root, {'SKILL.md': b'skill'}),
                        manifest_path=_manifest_path(home), allowed_roots=_roots(home))
    assert result['changed'] is True
    assert (root / 'SKILL.md').read_bytes() == b'skill'


def test_concurrent_create_at_publish_is_not_overwritten(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / '.agents/skills/reproagent'
    path = root / 'SKILL.md'
    operation = 'rename' if os.name == 'nt' else 'link'
    publish = getattr(manifest_module.os, operation)
    edited = []

    def create_then_publish(source, target, *args, **kwargs):
        if Path(target) == path and not edited:
            path.write_bytes(b'user create at publish')
            edited.append(True)
        return publish(source, target, *args, **kwargs)

    monkeypatch.setattr(manifest_module.os, operation, create_then_publish)
    with pytest.raises(manifest_module.InstallFailed) as error:
        apply_plan(_plan(root, {'SKILL.md': b'skill'}),
                   manifest_path=_manifest_path(home), allowed_roots=_roots(home))
    assert error.value.code == 'concurrent_file_change'
    assert path.read_bytes() == b'user create at publish'
