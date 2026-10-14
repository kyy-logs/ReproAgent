import json
from pathlib import Path

import pytest

from evals.swt_bench.io import seal, write_json
from reproagent.core.serialization import bytes_hash


CASE = 'sphinx-doc__sphinx-8627'
BASE = 'a' * 40
PATCH = ('diff --git a/tests/test_repro.py b/tests/test_repro.py\n'
    'new file mode 100644\n--- /dev/null\n+++ b/tests/test_repro.py\n@@ -0,0 +1,4 @@\n'
    '+def test_struct():\n+    assert False\n+def test_control():\n+    assert True\n')


def inputs(tmp_path):
    root = tmp_path / 'round'; root.mkdir()
    prediction = (json.dumps({'instance_id': CASE, 'model_patch': PATCH}) + '\n').encode()
    (root / 'predictions.jsonl').write_bytes(prediction)
    manifest = seal({'cases': [{'instance_id': CASE, 'base_commit': BASE}]}, 'manifest_hash')
    data = seal({'run_id': 'source-round', 'model_name': 'offline', 'manifest': manifest,
        'harness_commit': 'b'*40, 'predictions_hash': bytes_hash(prediction)}, 'round_hash')
    write_json(root / 'round.json', data)
    write_json(root / 'official-execution.receipt.json', {'run_id': data['run_id'],
        'model_name': data['model_name'], 'predictions_hash': data['predictions_hash'],
        'harness_commit': data['harness_commit'], 'manifest_hash': manifest['manifest_hash']})
    logs = tmp_path / 'logs'
    for arm in ('pre', 'post'):
        directory = logs / 'source-round' / f'pred_{arm}__offline' / CASE
        directory.mkdir(parents=True)
        patches = [PATCH] if arm == 'pre' else ['gold-patch\n', PATCH]
        write_json(directory / 'exec_spec.json', {'instance_id': CASE, 'base_commit': BASE,
            'patch_list': patches, 'test_directives': ['tests/test_repro.py']})
        text = '#!/bin/bash\nset -uxo pipefail\ncd /testbed\n'
        text += f'git diff HEAD {BASE} >> /root/pre_state.patch\n'
        text += ''.join("git apply -v - <<'EOF_114329324912'\n" + p + '\nEOF_114329324912\n' for p in patches)
        text += f'tox -epy39 -v -- tests/test_repro.py\ngit checkout {BASE}\ngit apply /root/pre_state.patch\n'
        (directory / 'eval.sh').write_bytes(text.encode())
    return root, logs


def test_rejudge_preparation_freezes_inputs_and_derives_without_overwriting_source(tmp_path):
    from evals.repairs.sphinx_rejudge import prepare_rejudge
    root, logs = inputs(tmp_path)
    original = {str(p): p.read_bytes() for p in logs.rglob('eval.sh')}
    receipt = prepare_rejudge(root, logs, tmp_path / 'new')
    assert receipt['kind'] == 'reproagent-repaired-environment-diagnostic-v1'
    assert receipt['source_round'] == str(root.resolve())
    assert receipt['expected_nodes'] == ['tests/test_repro.py::test_control', 'tests/test_repro.py::test_struct']
    assert len(receipt['scripts']) == 2
    assert all('test_status=$?' in Path(arm['derived_path']).read_text() for arm in receipt['scripts'])
    assert {str(p): p.read_bytes() for p in logs.rglob('eval.sh')} == original
    with pytest.raises(FileExistsError):
        prepare_rejudge(root, logs, tmp_path / 'new')


@pytest.mark.parametrize('fault', ['predictions', 'patch', 'base'])
def test_mismatched_frozen_input_is_refused_before_execution(tmp_path, fault):
    from evals.repairs.sphinx_rejudge import prepare_rejudge
    root, logs = inputs(tmp_path)
    if fault == 'predictions':
        (root / 'predictions.jsonl').write_bytes(b'{}\n')
    else:
        p = logs / 'source-round' / 'pred_post__offline' / CASE / 'exec_spec.json'
        spec = json.loads(p.read_text())
        if fault == 'patch': spec['patch_list'][-1] += 'changed'
        else: spec['base_commit'] = 'c'*40
        write_json(p, spec)
    with pytest.raises(ValueError):
        prepare_rejudge(root, logs, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()


def test_differential_requires_complete_nodes_test_exit_and_rollback():
    from evals.repairs.sphinx_rejudge import differential_confirmed, parse_outcomes
    nodes = ['tests/test_repro.py::test_control', 'tests/test_repro.py::test_struct']
    pre = {'exit_code': 1, 'cleanup_ok': True, 'rollback_ok': True, 'stop_reason': 'EXITED',
        'outcomes': parse_outcomes('PASSED '+nodes[0]+'\nFAILED '+nodes[1]+' - AssertionError\n')}
    post = {**pre, 'exit_code': 0, 'outcomes': {n: 'PASSED' for n in nodes}}
    assert differential_confirmed(pre, post, nodes)
    for changed in ({'exit_code': 128}, {'cleanup_ok': False}, {'rollback_ok': False},
                    {'outcomes': {nodes[0]: 'PASSED'}}, {'outcomes': {n: 'SKIPPED' for n in nodes}},
                    {'stop_reason': 'COMMAND_TIMEOUT'}):
        assert not differential_confirmed(pre, {**post, **changed}, nodes)
    assert not differential_confirmed({**pre, 'outcomes': post['outcomes']}, post, nodes)


@pytest.mark.parametrize('client_exit,remove_exit,expected_stop', [
    (0, 0, 'EXITED'), (0, 1, 'EXITED'), (125, 0, 'DOCKER_CONTROL_ERROR')])
def test_arm_uses_inspected_container_exit_and_separate_cleanup(tmp_path, monkeypatch, client_exit, remove_exit, expected_stop):
    import subprocess
    from evals.repairs import sphinx_rejudge as module
    commands = []
    container = 'd'*64
    script = {'arm': 'pre', 'derived_path': str(tmp_path/'pre.sh')}
    def control(args, **kwargs):
        commands.append(args)
        verb = args[1]
        if verb == 'create': return subprocess.CompletedProcess(args, 0, (container+'\n').encode())
        if verb == 'start':
            kwargs['stdout'].write(b'FAILED tests/test_repro.py::test_struct - AssertionError\nREPROAGENT_DIAGNOSTIC test_exit=1 rollback=ok\n')
            return subprocess.CompletedProcess(args, client_exit)
        if verb == 'inspect': return subprocess.CompletedProcess(args, 0, b'{"Running":false,"Status":"exited","ExitCode":1}')
        if verb == 'rm': return subprocess.CompletedProcess(args, remove_exit)
        raise AssertionError(args)
    monkeypatch.setattr(module.subprocess, 'run', control)
    result = module.execute_arm(tmp_path, script, 'sha256:'+'e'*64, 10)
    assert result['exit_code'] == 1  # docker-start's zero is not the test's exit.
    assert result['stop_reason'] == expected_stop
    assert result['cleanup_ok'] is (remove_exit == 0)
    assert result['rollback_ok'] is True
    assert commands[-1] == ['docker', 'rm', '--force', container]
    assert all(value.endswith(',readonly') for command in commands if command[1] == 'create'
               for index, value in enumerate(command) if index and command[index-1] == '--mount')


def test_arm_timeout_still_removes_its_own_container(tmp_path, monkeypatch):
    import subprocess
    from evals.repairs import sphinx_rejudge as module
    removed = []
    def control(args, **kwargs):
        if args[1] == 'create': return subprocess.CompletedProcess(args, 0, ('d'*64).encode())
        if args[1] == 'start': raise subprocess.TimeoutExpired(args, 10)
        if args[1] == 'rm':
            removed.append(args[-1]); return subprocess.CompletedProcess(args, 0)
        raise AssertionError(args)
    monkeypatch.setattr(module.subprocess, 'run', control)
    result = module.execute_arm(tmp_path, {'arm':'pre', 'derived_path':str(tmp_path/'pre.sh')}, 'sha256:'+'e'*64, 10)
    assert result['stop_reason'] == 'COMMAND_TIMEOUT' and result['exit_code'] is None
    assert result['cleanup_ok'] is True and result['rollback_ok'] is False
    assert removed == ['d'*64]
