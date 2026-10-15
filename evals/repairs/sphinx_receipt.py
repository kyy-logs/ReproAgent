"""Read-only validation of the frozen, explicitly modified Sphinx diagnostic.

This checks the supplied evidence and its bindings; it does not attest a Docker
execution or turn a modified-environment diagnostic into an official score.
Recorded absolute paths are provenance only. All reads use caller-selected roots
and the fixed artifact names produced by ``prepare_rejudge``.
"""
import os
import re
import stat
from pathlib import Path

from evals.swt_bench.io import file_hash, loads, read_json, verify_seal
from reproagent.core.serialization import bytes_hash
from . import sphinx_rejudge
from .pinned_sphinx_script import repair_script
from .sphinx_rejudge import CASE, PROFILE, differential_confirmed, parse_outcomes


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _safe_file(root, name):
    """Refuse links/reparse points in every component before reading a fixed file."""
    root = Path(os.path.abspath(root))
    path = root / name
    _require(path.is_relative_to(root), 'artifact path escapes its root')
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        _require(not stat.S_ISLNK(info.st_mode)
                 and not (getattr(info, 'st_file_attributes', 0)
                          & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)),
                 'linked diagnostic artifact is not allowed')
    _require(stat.S_ISREG(path.lstat().st_mode), 'diagnostic artifact must be a regular file')
    _require(path.resolve().is_relative_to(root.resolve()), 'artifact path escapes its root')
    return path


def _matches_hash(path, value, message):
    _require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
             and file_hash(path) == value, message)


def _ordered_pair(value, message):
    _require(isinstance(value, list) and len(value) == 2
             and all(isinstance(item, dict) for item in value)
             and [item.get('arm') for item in value] == ['pre', 'post'], message)


def _validate_mounts(arm, image_id):
    """Check recorded use of the frozen profile, without following any mount source."""
    mounts, command = arm.get('mounts'), arm.get('create_command')
    expected = {'/opt/miniconda3/envs/testbed/bin/tox': '/profile/tox',
                '/opt/reproagent-repair/constraints.txt': '/profile/constraints.txt',
                '/reproagent-eval.sh': '/' + arm['arm'] + '.sh'}
    _require(isinstance(mounts, dict) and len(mounts) == 3
             and set(mounts.values()) == set(expected), 'complete frozen mounts are required')
    roots, flags = set(), set()
    for source, target in mounts.items():
        _require(isinstance(source, str), 'invalid mount source')
        normalized = source.replace('\\', '/')
        suffix = expected[target]
        _require(',' not in source and '..' not in normalized.split('/')
                 and (normalized.startswith('/') or re.match(r'^[A-Za-z]:/', normalized))
                 and normalized.endswith(suffix), 'unsafe diagnostic mount source')
        roots.add(normalized[:-len(suffix)])
        flags.add(f'type=bind,src={source},dst={target},readonly')
    _require(len(roots) == 1 and bool(next(iter(roots))), 'diagnostic mount roots differ')
    _require(isinstance(command, list) and len(command) == 13
             and command[:3] == ['docker', 'create', '--name']
             and isinstance(command[3], str) and re.fullmatch(r'reproagent-8627-[0-9a-f]{32}', command[3])
             and command[4:10:2] == ['--mount'] * 3 and set(command[5:10:2]) == flags
             and command[-3:] == [image_id, '/bin/bash', '/reproagent-eval.sh'],
             'execution command differs from the frozen mounts or immutable image')


def _validate(round_dir, receipt_path):
    root = Path(os.path.abspath(round_dir))
    supplied = Path(os.path.abspath(receipt_path))
    output = supplied.parent
    receipt = read_json(_safe_file(output, supplied.name))
    _require(isinstance(receipt, dict), 'diagnostic receipt must be an object')
    _require(receipt.get('kind') == 'reproagent-repaired-environment-diagnostic-v1'
             and receipt.get('case_id') == CASE, 'unsupported diagnostic identity')
    _require(receipt.get('result') == 'candidate_differential_confirmed',
             'diagnostic does not declare a confirmed differential')
    _require(isinstance(receipt.get('source_round'), str) and bool(receipt['source_round']),
             'source round provenance is missing')

    data = read_json(_safe_file(root, 'round.json')); verify_seal(data, 'round_hash')
    manifest = data['manifest']; verify_seal(manifest, 'manifest_hash')
    _require(receipt.get('source_round_hash') == data['round_hash'], 'source round binding differs')
    source_path = _safe_file(root, 'official-execution.receipt.json')
    _matches_hash(source_path, receipt.get('source_receipt_sha256'), 'source receipt changed')
    source = read_json(source_path)
    for key in ('run_id', 'model_name', 'predictions_hash', 'harness_commit'):
        _require(source.get(key) == data[key], 'source receipt identity differs')
    _require(source.get('manifest_hash') == manifest['manifest_hash'], 'source manifest binding differs')
    for key in ('run_id', 'model_name'):
        _require(isinstance(data[key], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', data[key])
                 and data[key] not in ('.', '..'), 'unsafe source identity')
    _require(receipt.get('harness_commit') == data['harness_commit'], 'harness binding differs')

    predictions = _safe_file(root, 'predictions.jsonl')
    _matches_hash(predictions, data['predictions_hash'], 'frozen predictions changed')
    _require(receipt.get('predictions_sha256') == data['predictions_hash'], 'predictions binding differs')
    rows = [loads(line) for line in predictions.read_text(encoding='utf-8').splitlines() if line.strip()]
    _require(all(isinstance(row, dict) for row in rows), 'invalid frozen prediction row')
    selected = [row for row in rows if row.get('instance_id') == CASE]
    entries = [row for row in manifest['cases'] if row.get('instance_id') == CASE]
    _require(len(selected) == 1 and len(entries) == 1, 'one frozen case prediction is required')
    patch, base = selected[0].get('model_patch'), entries[0].get('base_commit')
    _require(isinstance(patch, str) and bool(patch), 'frozen candidate patch is empty')
    _require(isinstance(base, str) and re.fullmatch(r'[0-9a-f]{40}', base), 'invalid frozen base commit')
    _require(receipt.get('base_commit') == base
             and receipt.get('prediction_patch_sha256') == bytes_hash(patch.encode('utf-8')),
             'frozen candidate binding differs')
    names = re.findall(r'^\+def (test_\w+)\(', patch, re.M)
    _require(bool(names) and len(names) == len(set(names)), 'unsupported candidate test layout')
    nodes = sorted('tests/test_repro.py::' + name for name in names)
    _require(receipt.get('expected_nodes') == nodes, 'expected nodes differ from the frozen candidate')

    _matches_hash(_safe_file(Path(sphinx_rejudge.__file__).parent, 'sphinx_rejudge.py'),
                  receipt.get('tool_sha256'), 'diagnostic tool changed')
    _matches_hash(_safe_file(Path(__file__).parent, 'pinned_sphinx_script.py'),
                  receipt.get('deriver_sha256'), 'diagnostic deriver changed')
    profile = receipt.get('profile')
    _require(isinstance(profile, dict) and set(profile) == {'tox', 'constraints.txt'},
             'complete dependency profile is required')
    for name in ('tox', 'constraints.txt'):
        actual = _safe_file(output, 'profile/' + name)
        _matches_hash(actual, profile[name], 'frozen dependency profile changed')
        _require(file_hash(actual) == file_hash(_safe_file(PROFILE, name)),
                 'dependency profile differs from the trusted repair')

    image = receipt.get('image')
    _require(isinstance(image, dict) and isinstance(image.get('requested'), str)
             and bool(image['requested'].strip()) and isinstance(image.get('id'), str)
             and re.fullmatch(r'sha256:[0-9a-f]{64}', image['id']), 'immutable image identity is required')
    _require(type(receipt.get('timeout_seconds')) is int and receipt['timeout_seconds'] > 0,
             'positive diagnostic timeout is required')
    _ordered_pair(receipt.get('scripts'), 'ordered pre/post scripts are required')
    _ordered_pair(receipt.get('arms'), 'ordered pre/post arms are required')

    for script, arm in zip(receipt['scripts'], receipt['arms']):
        label = script['arm']
        original = _safe_file(output, f'{label}.original.sh')
        derived = _safe_file(output, f'{label}.sh')
        spec_path = _safe_file(output, f'{label}.exec_spec.json')
        _matches_hash(original, script.get('original_sha256'), 'original script changed')
        _matches_hash(derived, script.get('derived_sha256'), 'derived script changed')
        _matches_hash(spec_path, script.get('spec_sha256'), 'frozen execution spec changed')
        spec = read_json(spec_path)
        patches = spec.get('patch_list')
        _require(spec.get('instance_id') == CASE and spec.get('base_commit') == base
                 and spec.get('test_directives') == ['tests/test_repro.py']
                 and isinstance(patches, list) and len(patches) == (1 if label == 'pre' else 2)
                 and all(isinstance(value, str) and bool(value) for value in patches)
                 and patches[-1] == patch, 'execution spec differs from frozen candidate')
        # The prior post-arm patch is opaque; only its frozen presence/order matters.
        text = original.read_bytes().decode('utf-8')
        applied = re.findall(r"^git apply -v - <<'EOF_114329324912'\n(.*?)\nEOF_114329324912\n",
                             text, re.M | re.S)
        _require(applied == patches, 'original script patch application differs from the frozen spec')
        _require(text.endswith(f'\ntox -epy39 -v -- tests/test_repro.py\ngit checkout {base}\n'
                               'git apply /root/pre_state.patch\n'),
                 'original script test directive differs from the frozen candidate')
        _require(derived.read_bytes() == repair_script(text, base).encode('utf-8'),
                 'derived script does not match the trusted repair')

        log = _safe_file(output, f'{label}.log')
        _matches_hash(log, arm.get('log_sha256'), 'diagnostic log changed')
        log_text = log.read_text(encoding='utf-8', errors='replace')
        outcomes = parse_outcomes(log_text)
        _require(arm.get('outcomes') == outcomes, 'declared outcomes differ from the diagnostic log')
        _require(type(arm.get('exit_code')) is int and arm['exit_code'] == (1 if label == 'pre' else 0),
                 'diagnostic test exit differs')
        markers = re.findall(r'^REPROAGENT_DIAGNOSTIC test_exit=([^\r\n ]+) rollback=([^\r\n ]+)\r?$',
                             log_text, re.M)
        _require(markers == [(str(arm['exit_code']), 'ok')]
                 and len(re.findall(r'^REPROAGENT_DIAGNOSTIC\b', log_text, re.M)) == 1
                 and not re.search(r'^ROLLBACK_FAILED\b', log_text, re.M),
                 'rollback marker is missing or inconsistent')
        _require(arm.get('stop_reason') == 'EXITED' and arm.get('cleanup_ok') is True
                 and arm.get('rollback_ok') is True, 'diagnostic execution did not complete safely')
        _require(isinstance(arm.get('container_id'), str)
                 and re.fullmatch(r'[0-9a-f]{64}', arm['container_id']), 'container identity is missing')
        _validate_mounts(arm, image['id'])

    _require(receipt['arms'][0]['container_id'] != receipt['arms'][1]['container_id'],
             'diagnostic arms must use separate containers')
    _require(differential_confirmed(*receipt['arms'], nodes), 'logs do not confirm the full candidate differential')
    return receipt


def validate_rejudge_receipt(round_dir, receipt_path) -> dict:
    """Return the original receipt only after its fixed artifacts pass revalidation.

    ``source_round``, script ``source``/``derived_path`` and arm ``log`` paths
    remain informational so immutable inputs can move between Linux and Windows.
    No recorded path is opened, no file is written, and no command is executed.
    """
    try:
        return _validate(round_dir, receipt_path)
    except (OSError, UnicodeError, KeyError, TypeError, AttributeError, IndexError) as exc:
        # Never include opaque patch bodies or untrusted path text in errors.
        raise ValueError('diagnostic receipt or frozen artifacts are invalid') from None
