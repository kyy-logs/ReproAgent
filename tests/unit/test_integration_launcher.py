import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from reproagent.integration import credentials, launcher
from reproagent.integration.credentials import load_binding
from reproagent.integration.render import encode_binding


def _binding(tmp_path: Path, *, key='DEEPSEEK_API_KEY', env_file=None) -> dict:
    model = tmp_path / 'model.json'
    model.write_text(json.dumps({"base_url": "https://api.deepseek.com",
                                 "model": "fake-model", "api_key_env": key}),
                     encoding='utf-8')
    return load_binding(tool_python=Path(sys.executable), model_config=model,
                        repro_root=tmp_path / 'repro-root', env_file=env_file)


def test_inspect_without_model_or_credential(tmp_path, monkeypatch):
    target = tmp_path / 'task'
    binding = _binding(tmp_path)
    monkeypatch.setattr(credentials, 'discover_source',
                        lambda *args, **kwargs: pytest.fail('inspect must not read credentials'))
    captured = {}

    def run(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return SimpleNamespace(returncode=5)

    monkeypatch.setattr(launcher.subprocess, 'run', run)

    code = launcher.launch('inspect', target, binding, environ={})

    assert code == 5
    assert captured['argv'] == [sys.executable, '-m', 'reproagent', 'inspect', str(target)]
    assert captured['kwargs']['env'] is None
    assert captured['kwargs']['shell'] is False


def test_check_does_not_decrypt_and_reports_readiness(tmp_path, monkeypatch, capsys):
    binding = _binding(tmp_path)
    monkeypatch.setattr(credentials, 'decrypt_dpapi_file',
                        lambda path: pytest.fail('check must not decrypt'))

    code = launcher.launch('check', None, binding,
                           environ={'DEEPSEEK_API_KEY': 'fake-canary'})
    output = capsys.readouterr().out

    assert code == 0
    payload = json.loads(output)
    assert payload['ready'] is True
    assert payload['run_configured'] is True
    assert payload['credential_source']['kind'] == 'environment'
    assert 'fake-canary' not in output


def test_check_missing_configuration_returns_1(tmp_path, capsys):
    binding = load_binding(tool_python=Path(sys.executable), model_config=None,
                           repro_root=None, env_file=None)

    code = launcher.launch('check', None, binding, environ={})
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload['ready'] is False
    assert payload['model_config_status'] == 'missing'


def test_check_invalid_dotenv_returns_2(tmp_path, capsys):
    env_file = tmp_path / 'broken.env'
    env_file.write_text('DEEPSEEK_API_KEY=one\nDEEPSEEK_API_KEY=two\n', encoding='utf-8')
    binding = _binding(tmp_path, env_file=env_file)

    code = launcher.launch('check', None, binding, environ={})
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload['ready'] is False
    assert payload['credential_source']['kind'] == 'invalid_credential_file'


def test_run_uses_exact_cli_argv_and_child_environment(tmp_path, monkeypatch):
    target = tmp_path / "task dir's $x;.json"
    fixed_repo = tmp_path / 'fixed repo'
    binding = _binding(tmp_path)
    parent = {'DEEPSEEK_API_KEY': 'fake-canary', 'PATH': 'keep-me'}
    captured = {}

    def run(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(launcher.subprocess, 'run', run)

    code = launcher.launch('run', target, binding, fixed_repo=fixed_repo,
                           fixed_python='fixed-python', environ=parent)

    assert code == 7
    assert captured['argv'] == [
        sys.executable, '-m', 'reproagent', 'run', '--config', str(target),
        '--model-config', binding['model_config'],
        '--fixed-repo', str(fixed_repo), '--fixed-python', 'fixed-python']
    assert captured['kwargs']['shell'] is False
    assert captured['kwargs']['env']['DEEPSEEK_API_KEY'] == 'fake-canary'
    assert captured['kwargs']['env']['PATH'] == 'keep-me'
    assert parent == {'DEEPSEEK_API_KEY': 'fake-canary', 'PATH': 'keep-me'}


def test_run_without_source_returns_2_without_raw_error(tmp_path, capsys):
    binding = _binding(tmp_path)

    code = launcher.launch('run', tmp_path / 'task.json', binding, environ={})
    output = capsys.readouterr().out

    assert code == 2
    payload = json.loads(output)
    assert payload['ready'] is False
    assert payload['code'] in ('credential_missing', 'credential_load_failed')
    assert 'secret' not in output


def test_run_invalid_credential_file_returns_2(tmp_path, capsys):
    env_file = tmp_path / 'broken.env'
    env_file.write_text('DEEPSEEK_API_KEY=one\nDEEPSEEK_API_KEY=two\n', encoding='utf-8')
    binding = _binding(tmp_path, env_file=env_file)

    code = launcher.launch('run', tmp_path / 'task.json', binding, environ={})
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload['code'] == 'invalid_credential_file'


def test_run_without_model_config_returns_2(tmp_path, capsys):
    binding = load_binding(tool_python=Path(sys.executable), model_config=None,
                           repro_root=None, env_file=None)

    code = launcher.launch('run', tmp_path / 'task.json', binding,
                           environ={'REPROAGENT_API_KEY': 'fake'})
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload['code'] == 'model_config_required'


def test_probe_tool_python_reports_controlled_result(tmp_path):
    import sys

    assert launcher.probe_tool_python(Path(sys.executable))['available'] is True
    missing = launcher.probe_tool_python(tmp_path / 'missing-python')
    assert missing['available'] is False
    assert missing['error'] == 'tool_python_missing'


def test_main_parses_binding_and_runtime_overrides(tmp_path, monkeypatch):
    binding = _binding(tmp_path)
    encoded = encode_binding(binding)
    captured = {}

    def fake_launch(action, target, active, **kwargs):
        captured.update({'action': action, 'target': target, 'binding': active, **kwargs})
        return 3

    monkeypatch.setattr(launcher, 'launch', fake_launch)
    override = tmp_path / 'override.json'
    override.write_text(json.dumps({"base_url": "https://api.deepseek.com",
                                    "model": "other", "api_key_env": "DEEPSEEK_API_KEY"}),
                        encoding='utf-8')

    code = launcher.main(['--binding-b64', encoded, 'run', str(tmp_path / 'task.json'),
                          '--model-config', str(override),
                          '--repro-root', str(tmp_path),
                          '--env-file', str(tmp_path / '.env'),
                          '--fixed-repo', str(tmp_path / 'fixed')])

    assert code == 3
    assert captured['action'] == 'run'
    assert captured['binding']['model_config'] == str(override)
    assert captured['binding']['repro_root'] == str(tmp_path)
    assert captured['binding']['env_file'] == str(tmp_path / '.env')
    assert captured['fixed_repo'] == tmp_path / 'fixed'


def test_main_rejects_bad_binding_with_safe_json(capsys):
    code = launcher.main(['--binding-b64', 'not-base64', 'check'])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['code'] == 'invalid_binding'


def test_main_inspect_ignores_removed_model_and_credential_paths(tmp_path, monkeypatch):
    binding = _binding(tmp_path)
    encoded = encode_binding(binding)
    Path(binding['model_config']).unlink()
    monkeypatch.setattr(launcher, 'load_binding',
                        lambda **kwargs: pytest.fail('inspect must not load model binding'))
    monkeypatch.setattr(launcher, 'discover_source',
                        lambda *args, **kwargs: pytest.fail('inspect must not discover credentials'))
    captured = []

    def run(argv, **kwargs):
        captured.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, 'run', run)
    assert launcher.main(['--binding-b64', encoded, 'inspect', str(tmp_path / 'task')]) == 0
    assert captured == [[sys.executable, '-m', 'reproagent', 'inspect', str(tmp_path / 'task')]]
