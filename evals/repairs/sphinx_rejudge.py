"""Explicit Linux/Docker re-judge for the frozen 8627 candidate; not an official score.

Reuses the pinned script derivative and dependency profile. Original rounds are read-only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from evals.swt_bench.io import file_hash, loads, read_json, verify_seal, write_json
from reproagent.core.serialization import bytes_hash
from .pinned_sphinx_script import repair_script

CASE = 'sphinx-doc__sphinx-8627'
PROFILE = Path(__file__).with_name('sphinx-legacy')
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def prepare_rejudge(round_dir, logs_dir, output):
    root, logs, output = (Path(p).resolve() for p in (round_dir, logs_dir, output))
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (root, logs, PROFILE.resolve())):
        raise ValueError('diagnostic output overlaps inputs')
    data = read_json(root / 'round.json'); verify_seal(data, 'round_hash')
    manifest = data['manifest']; verify_seal(manifest, 'manifest_hash')
    source_receipt = read_json(root / 'official-execution.receipt.json')
    for key in ('run_id', 'model_name', 'predictions_hash', 'harness_commit'):
        if source_receipt.get(key) != data[key]:
            raise ValueError('source receipt identity mismatch')
    if source_receipt.get('manifest_hash') != manifest['manifest_hash']:
        raise ValueError('source receipt manifest mismatch')
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', data[key]) or data[key] in ('.', '..') for key in ('run_id', 'model_name')):
        raise ValueError('unsafe source identity')
    if file_hash(root / 'predictions.jsonl') != data['predictions_hash']:
        raise ValueError('frozen predictions changed')
    rows = [loads(line) for line in (root / 'predictions.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    selected = [row for row in rows if row.get('instance_id') == CASE]
    entries = [row for row in manifest['cases'] if row['instance_id'] == CASE]
    if len(selected) != 1 or len(entries) != 1 or not selected[0].get('model_patch'):
        raise ValueError('one nonempty frozen 8627 prediction required')
    patch, base = selected[0]['model_patch'], entries[0]['base_commit']
    names = re.findall(r'^\+def (test_\w+)\(', patch, re.M)
    if not names or len(names) != len(set(names)):
        raise ValueError('unsupported candidate test layout')
    scripts = []
    for arm in ('pre', 'post'):
        source = logs / data['run_id'] / f'pred_{arm}__{data["model_name"]}' / CASE
        spec = read_json(source / 'exec_spec.json')
        patches = spec.get('patch_list', [])
        if (spec.get('instance_id') != CASE or spec.get('base_commit') != base
                or spec.get('test_directives') != ['tests/test_repro.py']
                or len(patches) != (1 if arm == 'pre' else 2) or patches[-1] != patch):
            raise ValueError('execution spec differs from frozen candidate')
        original = (source / 'eval.sh').read_bytes()
        text = original.decode('utf-8')
        if any(p not in text for p in patches):
            raise ValueError('script does not contain frozen patches')
        derived = repair_script(text, base).encode('utf-8')
        scripts.append((arm, source, original, derived))
    output.mkdir(parents=True, exist_ok=False)
    profile = output / 'profile'; profile.mkdir()
    for name in ('tox', 'constraints.txt'):
        shutil.copyfile(PROFILE / name, profile / name)
    (profile / 'tox').chmod(0o755)
    receipt = {'kind': 'reproagent-repaired-environment-diagnostic-v1', 'case_id': CASE,
        'source_round': str(root), 'source_round_hash': data['round_hash'],
        'source_receipt_sha256': file_hash(root / 'official-execution.receipt.json'),
        'harness_commit': data['harness_commit'], 'base_commit': base,
        'predictions_sha256': data['predictions_hash'],
        'prediction_patch_sha256': bytes_hash(patch.encode('utf-8')),
        'tool_sha256': file_hash(Path(__file__)),
        'deriver_sha256': file_hash(Path(__file__).with_name('pinned_sphinx_script.py')),
        'profile': {name: file_hash(profile / name) for name in ('tox', 'constraints.txt')},
        'expected_nodes': sorted('tests/test_repro.py::' + name for name in names),
        'scripts': [], 'arms': [], 'result': 'not_run'}
    for arm, source, original, derived in scripts:
        original_path = output / f'{arm}.original.sh'
        derived_path = output / f'{arm}.sh'
        original_path.write_bytes(original); derived_path.write_bytes(derived)
        shutil.copyfile(source / 'exec_spec.json', output / f'{arm}.exec_spec.json')
        receipt['scripts'].append({'arm': arm, 'source': str(source),
            'original_sha256': file_hash(original_path), 'derived_sha256': file_hash(derived_path),
            'spec_sha256': file_hash(output / f'{arm}.exec_spec.json'), 'derived_path': str(derived_path)})
    write_json(output / 'receipt.json', receipt)
    return receipt


def parse_outcomes(text):
    outcomes = {}
    for status, node in re.findall(r'^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\S+)', ANSI.sub('', text), re.M):
        if node in outcomes and outcomes[node] != status:
            outcomes[node] = 'CONFLICTING'
        else:
            outcomes[node] = status
    return outcomes


def differential_confirmed(pre, post, nodes):
    complete = lambda arm: (arm.get('stop_reason') == 'EXITED' and arm.get('cleanup_ok') is True
        and arm.get('rollback_ok') is True and set(arm.get('outcomes', {})) == set(nodes))
    return bool(nodes) and complete(pre) and complete(post) and pre.get('exit_code') == 1 and post.get('exit_code') == 0 \
        and set(pre['outcomes'].values()) <= {'PASSED', 'FAILED'} and 'FAILED' in pre['outcomes'].values() \
        and set(post['outcomes'].values()) == {'PASSED'}


def execute_arm(output, script, image_id, timeout):
    name = 'reproagent-8627-' + uuid.uuid4().hex
    mounts = {output / 'profile/tox': '/opt/miniconda3/envs/testbed/bin/tox',
        output / 'profile/constraints.txt': '/opt/reproagent-repair/constraints.txt',
        Path(script['derived_path']): '/reproagent-eval.sh'}
    if any(',' in str(path) for path in mounts):
        raise ValueError('Docker mount paths cannot contain commas')
    args = ['docker', 'create', '--name', name]
    for source, target in mounts.items():
        args += ['--mount', f'type=bind,src={source},dst={target},readonly']
    args += [image_id, '/bin/bash', '/reproagent-eval.sh']
    record = {'arm': script['arm'], 'create_command': args, 'mounts': {str(k): v for k, v in mounts.items()},
        'container_id': None, 'exit_code': None, 'stop_reason': 'DOCKER_CONTROL_ERROR', 'cleanup_ok': False}
    log = output / f'{script["arm"]}.log'
    try:
        created = subprocess.run(args, capture_output=True, check=True, timeout=30)
        container = created.stdout.decode().strip()
        if not re.fullmatch(r'[0-9a-f]{64}', container):
            raise ValueError('invalid container identity')
        record['container_id'] = container
        with log.open('wb') as stream:
            process = subprocess.run(['docker', 'start', '--attach', container], stdout=stream,
                stderr=subprocess.STDOUT, timeout=timeout)
        inspected = subprocess.run(['docker', 'inspect', container, '--format', '{{json .State}}'],
            capture_output=True, check=True, timeout=30)
        state = loads(inspected.stdout.decode())
        if state.get('Running') is False and state.get('Status') == 'exited':
            record['exit_code'] = state['ExitCode']
            if process.returncode in (0, state['ExitCode']): record['stop_reason'] = 'EXITED'
    except subprocess.TimeoutExpired:
        record['stop_reason'] = 'COMMAND_TIMEOUT'
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    finally:
        # Only remove the name freshly generated by this invocation. No host source is writable.
        try:
            removed = subprocess.run(['docker', 'rm', '--force', record['container_id'] or name],
                capture_output=True, timeout=30)
            record['cleanup_ok'] = removed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
    record.update(log=str(log), log_sha256=file_hash(log) if log.exists() else None,
        outcomes=parse_outcomes(text), rollback_ok=bool(re.search(
            r'^REPROAGENT_DIAGNOSTIC test_exit=' + str(record['exit_code']) + r' rollback=ok$', text, re.M)))
    return record


def run_rejudge(round_dir, logs_dir, image, output, *, timeout=240):
    if os.name == 'nt':
        raise ValueError('run this explicit diagnostic in Linux/WSL with Docker')
    inspected = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'],
        capture_output=True, check=True, timeout=30)
    image_id = inspected.stdout.decode().strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('local immutable image identity required')
    receipt = prepare_rejudge(round_dir, logs_dir, output)
    output = Path(output).resolve()
    receipt['image'] = {'requested': image, 'id': image_id}
    receipt['timeout_seconds'] = timeout
    for script in receipt['scripts']:
        record = execute_arm(output, script, image_id, timeout)
        receipt['arms'].append(record)
        write_json(output / 'receipt.json', receipt)
        if record['stop_reason'] != 'EXITED' or not record['cleanup_ok'] or not record['rollback_ok']:
            break
    confirmed = len(receipt['arms']) == 2 and differential_confirmed(*receipt['arms'], receipt['expected_nodes'])
    receipt['result'] = 'candidate_differential_confirmed' if confirmed else 'inconclusive'
    write_json(output / 'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('round', 'logs', 'image', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    result = run_rejudge(args.round, args.logs, args.image, args.output)
    print(json.dumps({'result': result['result'], 'receipt': str(Path(args.output).resolve() / 'receipt.json')}))
    return 0 if result['result'] == 'candidate_differential_confirmed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
