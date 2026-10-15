"""Validate frozen diagnostic evidence, never trust a declared differential result."""
import re
import shutil
from types import SimpleNamespace

import pytest

from evals.repairs.sphinx_rejudge import prepare_rejudge
from evals.swt_bench.io import file_hash, read_json, write_json
from tests.unit.test_sphinx_rejudge import CASE, inputs


def executed_receipt(tmp_path):
    root, logs = inputs(tmp_path)
    output = tmp_path / 'diagnostic'
    receipt = prepare_rejudge(root, logs, output)
    receipt['image'] = {'requested': 'offline-sphinx', 'id': 'sha256:' + 'e' * 64}
    receipt['timeout_seconds'] = 240
    for arm, exit_code in [('pre', 1), ('post', 0)]:
        outcomes = {node: ('FAILED' if arm == 'pre' and node.endswith('::test_struct') else 'PASSED')
                    for node in receipt['expected_nodes']}
        text = ''.join(f'{status} {node}\n' for node, status in outcomes.items())
        text += f'REPROAGENT_DIAGNOSTIC test_exit={exit_code} rollback=ok\n'
        log = output / f'{arm}.log'; log.write_text(text, encoding='utf-8')
        mounts = {str(output / 'profile/tox'): '/opt/miniconda3/envs/testbed/bin/tox',
                  str(output / 'profile/constraints.txt'): '/opt/reproagent-repair/constraints.txt',
                  str(output / f'{arm}.sh'): '/reproagent-eval.sh'}
        command = ['docker', 'create', '--name', 'reproagent-8627-' + 'f' * 32]
        for source, target in mounts.items():
            command += ['--mount', f'type=bind,src={source},dst={target},readonly']
        command += [receipt['image']['id'], '/bin/bash', '/reproagent-eval.sh']
        receipt['arms'].append({'arm': arm, 'exit_code': exit_code, 'stop_reason': 'EXITED',
            'cleanup_ok': True, 'rollback_ok': True, 'outcomes': outcomes,
            'log': str(log), 'log_sha256': file_hash(log), 'container_id': ('c' if arm == 'pre' else 'd') * 64,
            'create_command': command, 'mounts': mounts})
    receipt['result'] = 'candidate_differential_confirmed'
    path = output / 'receipt.json'; write_json(path, receipt)
    return root, path, receipt


def validate(root, path):
    from evals.repairs.sphinx_receipt import validate_rejudge_receipt
    return validate_rejudge_receipt(root, path)


def test_valid_receipt_is_recomputed_read_only_and_original_dict_returned(tmp_path, monkeypatch):
    root, path, receipt = executed_receipt(tmp_path)
    before = {file: file.read_bytes() for file in tmp_path.rglob('*') if file.is_file()}

    def forbidden(*args, **kwargs):
        raise AssertionError('validation cannot execute or prepare another diagnostic')

    import subprocess
    import evals.repairs.sphinx_rejudge as rejudge
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(rejudge, 'prepare_rejudge', forbidden)
    assert validate(root, path) == receipt
    assert {file: file.read_bytes() for file in tmp_path.rglob('*') if file.is_file()} == before


def test_moved_inputs_and_untrusted_provenance_paths_are_never_followed(tmp_path):
    root, path, receipt = executed_receipt(tmp_path)
    moved = tmp_path / 'relocated'; moved.mkdir()
    moved_root = moved / 'round'; shutil.copytree(root, moved_root)
    moved_output = moved / 'diagnostic'; shutil.copytree(path.parent, moved_output)
    receipt['source_round'] = '/untrusted/never-read-round'
    for script in receipt['scripts']:
        script['source'] = '/untrusted/never-read-harness'
        script['derived_path'] = '/untrusted/never-read-script'
    for arm in receipt['arms']:
        arm['log'] = '/untrusted/never-read-log'
    moved_path = moved_output / 'receipt.json'; write_json(moved_path, receipt)
    shutil.rmtree(root); shutil.rmtree(path.parent)
    assert validate(moved_root, moved_path) == receipt


@pytest.mark.parametrize('fault', [
    'result', 'kind', 'case', 'image_empty', 'image_digest', 'profile_empty', 'profile_hash',
    'round_hash', 'source_receipt_hash', 'predictions_hash', 'patch_hash', 'base', 'harness',
    'tool_hash', 'deriver_hash', 'nodes_empty', 'nodes_extra', 'scripts_empty', 'scripts_order',
    'arms_empty', 'arms_order', 'original_hash', 'derived_hash', 'spec_hash', 'log_hash',
    'declared_outcomes', 'timeout', 'cleanup', 'rollback', 'exit', 'image_command',
    'mounts_empty', 'mount_not_readonly', 'mount_escape', 'same_container',
])
def test_forged_or_incomplete_declarations_are_rejected(tmp_path, fault):
    root, path, receipt = executed_receipt(tmp_path)
    if fault == 'result': receipt['result'] = 'inconclusive'
    elif fault == 'kind': receipt['kind'] = 'official'
    elif fault == 'case': receipt['case_id'] = 'other'
    elif fault == 'image_empty': receipt['image'] = {}
    elif fault == 'image_digest': receipt['image']['id'] = 'offline-sphinx:latest'
    elif fault == 'profile_empty': receipt['profile'] = {}
    elif fault == 'profile_hash': receipt['profile']['tox'] = '0' * 64
    elif fault == 'round_hash': receipt['source_round_hash'] = '0' * 64
    elif fault == 'source_receipt_hash': receipt['source_receipt_sha256'] = '0' * 64
    elif fault == 'predictions_hash': receipt['predictions_sha256'] = '0' * 64
    elif fault == 'patch_hash': receipt['prediction_patch_sha256'] = '0' * 64
    elif fault == 'base': receipt['base_commit'] = '0' * 40
    elif fault == 'harness': receipt['harness_commit'] = '0' * 40
    elif fault == 'tool_hash': receipt['tool_sha256'] = '0' * 64
    elif fault == 'deriver_hash': receipt['deriver_sha256'] = '0' * 64
    elif fault == 'nodes_empty': receipt['expected_nodes'] = []
    elif fault == 'nodes_extra': receipt['expected_nodes'].append('tests/test_repro.py::test_other')
    elif fault == 'scripts_empty': receipt['scripts'] = []
    elif fault == 'scripts_order': receipt['scripts'].reverse()
    elif fault == 'arms_empty': receipt['arms'] = []
    elif fault == 'arms_order': receipt['arms'].reverse()
    elif fault == 'original_hash': receipt['scripts'][0]['original_sha256'] = '0' * 64
    elif fault == 'derived_hash': receipt['scripts'][0]['derived_sha256'] = '0' * 64
    elif fault == 'spec_hash': receipt['scripts'][0]['spec_sha256'] = '0' * 64
    elif fault == 'log_hash': receipt['arms'][0]['log_sha256'] = '0' * 64
    elif fault == 'declared_outcomes': receipt['arms'][0]['outcomes'] = receipt['arms'][1]['outcomes']
    elif fault == 'timeout': receipt['arms'][0]['stop_reason'] = 'COMMAND_TIMEOUT'
    elif fault == 'cleanup': receipt['arms'][0]['cleanup_ok'] = False
    elif fault == 'rollback': receipt['arms'][0]['rollback_ok'] = False
    elif fault == 'exit': receipt['arms'][0]['exit_code'] = 0
    elif fault == 'image_command': receipt['arms'][0]['create_command'][-3] = 'sha256:' + '0' * 64
    elif fault == 'mounts_empty': receipt['arms'][0]['mounts'] = {}
    elif fault == 'mount_not_readonly':
        receipt['arms'][0]['create_command'][5] = receipt['arms'][0]['create_command'][5].removesuffix(',readonly')
    elif fault == 'mount_escape':
        arm = receipt['arms'][0]; source = next(iter(arm['mounts']))
        target = arm['mounts'].pop(source); forged = '/tmp/../profile/tox'
        arm['mounts'][forged] = target
        arm['create_command'][5] = f'type=bind,src={forged},dst={target},readonly'
    elif fault == 'same_container': receipt['arms'][1]['container_id'] = receipt['arms'][0]['container_id']
    write_json(path, receipt)
    with pytest.raises(ValueError):
        validate(root, path)


@pytest.mark.parametrize('fault', [
    'predictions', 'source_receipt', 'round_seal', 'manifest_seal', 'candidate_patch', 'spec_base',
    'spec_instance', 'spec_directives', 'profile_bytes', 'derived_bytes', 'original_bytes',
    'missing_node', 'skipped_node', 'conflicting_node', 'missing_rollback', 'wrong_rollback_exit',
    'duplicate_rollback', 'rollback_failure', 'script_command', 'script_patch_order',
])
def test_files_are_revalidated_even_when_their_receipt_hash_is_updated(tmp_path, fault):
    root, path, receipt = executed_receipt(tmp_path)
    output = path.parent
    if fault == 'predictions': (root / 'predictions.jsonl').write_text('{}\n', encoding='utf-8')
    elif fault == 'source_receipt':
        source = root / 'official-execution.receipt.json'; value = read_json(source)
        value['run_id'] = 'forged'; write_json(source, value)
        receipt['source_receipt_sha256'] = file_hash(source)
    elif fault in ('round_seal', 'manifest_seal'):
        value = read_json(root / 'round.json')
        if fault == 'round_seal': value['run_id'] = 'forged'
        else: value['manifest']['cases'][0]['base_commit'] = '0' * 40
        write_json(root / 'round.json', value)
    elif fault.startswith('spec_') or fault == 'candidate_patch':
        spec_path = output / 'post.exec_spec.json'; spec = read_json(spec_path)
        if fault == 'candidate_patch': spec['patch_list'][-1] += '\n'
        elif fault == 'spec_base': spec['base_commit'] = '0' * 40
        elif fault == 'spec_instance': spec['instance_id'] = 'other'
        else: spec['test_directives'] = ['tests/other.py']
        write_json(spec_path, spec); receipt['scripts'][1]['spec_sha256'] = file_hash(spec_path)
    elif fault == 'profile_bytes':
        profile = output / 'profile/tox'; profile.write_bytes(b'#!/bin/sh\nexit 0\n')
        receipt['profile']['tox'] = file_hash(profile)
    elif fault in ('script_command', 'script_patch_order'):
        from evals.repairs.pinned_sphinx_script import repair_script
        original = output / 'post.original.sh'; text = original.read_text(encoding='utf-8')
        if fault == 'script_command':
            text = text.replace('tox -epy39 -v -- tests/test_repro.py', 'tox -epy39 -v -- tests/other.py')
        else:
            blocks = re.findall(r"git apply -v - <<'EOF_114329324912'\n.*?\nEOF_114329324912\n", text, re.S)
            assert len(blocks) == 2
            text = text.replace(''.join(blocks), ''.join(reversed(blocks)))
        original.write_bytes(text.encode())
        (output / 'post.sh').write_bytes(repair_script(text, receipt['base_commit']).encode())
        receipt['scripts'][1]['original_sha256'] = file_hash(original)
        receipt['scripts'][1]['derived_sha256'] = file_hash(output / 'post.sh')
    elif fault in ('derived_bytes', 'original_bytes'):
        name = 'pre.sh' if fault == 'derived_bytes' else 'pre.original.sh'
        target = output / name; target.write_bytes(target.read_bytes() + b'echo forged\n')
        key = 'derived_sha256' if fault == 'derived_bytes' else 'original_sha256'
        receipt['scripts'][0][key] = file_hash(target)
    else:
        log = output / 'pre.log'; text = log.read_text(encoding='utf-8')
        if fault == 'missing_node': text = '\n'.join(line for line in text.splitlines() if '::test_struct' not in line) + '\n'
        elif fault == 'skipped_node': text = text.replace('FAILED ', 'SKIPPED ')
        elif fault == 'conflicting_node': text += 'PASSED tests/test_repro.py::test_struct\n'
        elif fault == 'missing_rollback': text = text.split('REPROAGENT_DIAGNOSTIC')[0]
        elif fault == 'wrong_rollback_exit': text = text.replace('test_exit=1', 'test_exit=0')
        elif fault == 'rollback_failure': text += 'ROLLBACK_FAILED\n'
        else: text += 'REPROAGENT_DIAGNOSTIC test_exit=1 rollback=ok\n'
        log.write_text(text, encoding='utf-8'); receipt['arms'][0]['log_sha256'] = file_hash(log)
        from evals.repairs.sphinx_rejudge import parse_outcomes
        receipt['arms'][0]['outcomes'] = parse_outcomes(text)
    write_json(path, receipt)
    with pytest.raises(ValueError):
        validate(root, path)


def test_reparse_point_is_refused_before_artifact_bytes_are_opened(tmp_path, monkeypatch):
    from pathlib import Path
    root, path, _ = executed_receipt(tmp_path)
    target = path.parent / 'pre.log'
    original_stat, original_read = Path.lstat, Path.read_bytes

    def reparse(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if self == target else info

    def read(self):
        if self == target:
            raise AssertionError('reparse artifact was followed')
        return original_read(self)

    monkeypatch.setattr(Path, 'lstat', reparse)
    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(ValueError, match='linked'):
        validate(root, path)


@pytest.mark.parametrize('relative', ['pre.log', 'pre.original.sh', 'profile/tox', 'receipt.json'])
def test_symlink_artifacts_are_rejected_before_the_external_target_is_read(tmp_path, relative):
    root, path, _ = executed_receipt(tmp_path)
    target = path.parent / relative
    outside = tmp_path / 'outside'; outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip('this host does not allow creating symlinks')
    with pytest.raises(ValueError):
        validate(root, path)
