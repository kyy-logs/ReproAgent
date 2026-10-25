"""Bootstrap orchestration; only slow/external environment operations are faked."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'install.py'


def test_bootstrap_entry_is_available():
    assert SCRIPT.is_file(), 'The one-click installer has not been implemented'


@pytest.fixture
def bootstrap():
    assert SCRIPT.is_file(), 'The one-click installer has not been implemented'
    spec = importlib.util.spec_from_file_location('windows_bootstrap', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / '中文 project & literal % folder'
    (root / 'src/reproagent/integration').mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname="reproagent-local"\n', encoding='utf-8')
    (root / 'src/reproagent/integration/setup.py').write_text('', encoding='utf-8')
    return root


@pytest.fixture
def commands(monkeypatch, bootstrap, checkout):
    calls = []
    state = {'pip_exit': 0, 'setup_exit': 0, 'check_exit': 0,
             'source': str(checkout / '.local/install-env/Lib/site-packages/reproagent/__init__.py'),
             'source_url': checkout.as_uri(),
             'check': {'ready': True, 'run_configured': True,
                       'model_config_status': 'ready', 'hosts': {}},
             'setup': {'action': 'install', 'warnings': [], 'backups': []}}

    def run(argv, **kwargs):
        assert isinstance(argv, list)
        assert not kwargs.get('shell')
        calls.append((argv, kwargs))
        if argv[1:2] == ['-c']:
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                'module_file': state['source'], 'source_url': state['source_url'], 'editable': False,
                'prefix': str(checkout / '.local/install-env')}), '')
        if argv[1:3] == ['-m', 'venv']:
            environment = Path(argv[-1])
            (environment / 'Scripts').mkdir(parents=True, exist_ok=True)
            (environment / 'Scripts/python.exe').touch()
            (environment / 'pyvenv.cfg').write_text('home = test\n')
        if argv[1:3] == ['-m', 'pip']:
            return subprocess.CompletedProcess(argv, state['pip_exit'])
        if argv[1:4] == ['-m', 'reproagent', 'setup']:
            check = '--check' in argv
            return subprocess.CompletedProcess(argv, state['check_exit' if check else 'setup_exit'],
                                               json.dumps(state['check' if check else 'setup']), '')
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    return calls, state


def test_install_uses_private_environment_and_binds_both_hosts(bootstrap, checkout, commands):
    calls, _ = commands
    assert bootstrap.install(checkout, host='all') == 0
    environment_python = str(checkout / '.local/install-env/Scripts/python.exe')
    pip = next(argv for argv, _ in calls if argv[1:3] == ['-m', 'pip'])
    assert pip[0] == environment_python
    assert pip[-1] == str(checkout)
    assert '--editable' not in pip
    assert not any('[dev]' in value for value in pip)
    setup = [argv for argv, _ in calls if argv[1:4] == ['-m', 'reproagent', 'setup']]
    assert len(setup) == 2
    assert setup[0][setup[0].index('--repro-root') + 1] == str(checkout)
    assert setup[0][setup[0].index('--tool-python') + 1] == environment_python
    assert setup[0][setup[0].index('--host') + 1] == 'all'
    assert '--force' not in setup[0]
    assert '--check' in setup[1]
    assert not (checkout / '.venv').exists()


def test_reinstall_reuses_environment_and_cleans_its_lock(bootstrap, checkout, commands):
    calls, _ = commands
    assert bootstrap.install(checkout) == 0
    assert bootstrap.install(checkout) == 0
    assert sum(argv[1:3] == ['-m', 'venv'] for argv, _ in calls) == 1
    assert not (checkout / '.local/install.lock').exists()


def test_pip_failure_stops_before_host_install(bootstrap, checkout, commands, capsys):
    calls, state = commands
    state['pip_exit'] = 7
    assert bootstrap.install(checkout) != 0
    assert not any(argv[1:4] == ['-m', 'reproagent', 'setup'] for argv, _ in calls)
    assert '[OK] ReproAgent installed' not in capsys.readouterr().out
    assert not (checkout / '.local/install.lock').exists()


def test_unowned_environment_is_preserved(bootstrap, checkout, commands):
    calls, _ = commands
    environment = checkout / '.local/install-env'
    environment.mkdir(parents=True)
    marker = environment / 'valuable.txt'
    marker.write_text('leave this alone')
    assert bootstrap.install(checkout) != 0
    assert marker.read_text() == 'leave this alone'
    assert not calls


def test_active_install_lock_is_not_removed(bootstrap, checkout, commands):
    calls, _ = commands
    lock = checkout / '.local/install.lock'
    lock.parent.mkdir(parents=True)
    lock.write_text('another installer')
    assert bootstrap.install(checkout) != 0
    assert lock.read_text() == 'another installer'
    assert not calls


def test_old_python_stops_before_any_install(bootstrap, checkout, commands, monkeypatch):
    calls, _ = commands
    monkeypatch.setattr(bootstrap.sys, 'version_info', (3, 10, 14))
    assert bootstrap.install(checkout) != 0
    assert not calls
    assert not (checkout / '.local').exists()


def test_missing_integration_dependency_stops_before_install(bootstrap, checkout, commands):
    calls, _ = commands
    (checkout / 'src/reproagent/integration/setup.py').unlink()
    assert bootstrap.install(checkout) != 0
    assert not calls


def test_setup_conflict_is_not_reported_as_success(bootstrap, checkout, commands, capsys):
    _, state = commands
    state['setup_exit'] = 2
    state['setup'] = {'code': 'conflict', 'ready': False}
    assert bootstrap.install(checkout) != 0
    output = capsys.readouterr().out
    assert 'conflict' in output
    assert '[OK] ReproAgent installed' not in output


def test_force_and_custom_model_are_forwarded_as_literal_arguments(bootstrap, checkout, commands):
    calls, _ = commands
    model = checkout / 'model & literal.json'
    model.write_text('{}')
    assert bootstrap.install(checkout, host='claude-code', model_config=model, force=True) == 0
    setup = next(argv for argv, _ in calls if argv[1:4] == ['-m', 'reproagent', 'setup'])
    assert '--force' in setup
    assert setup[setup.index('--model-config') + 1] == str(model)
    assert setup[setup.index('--host') + 1] == 'claude-code'


def test_missing_credentials_are_installed_but_not_claimed_ready(bootstrap, checkout, commands, capsys):
    _, state = commands
    state['check_exit'] = 1
    state['check'] = {'ready': False, 'run_configured': False,
                      'model_config_status': 'ready', 'hosts': {}}
    assert bootstrap.install(checkout) == 0
    output = capsys.readouterr().out
    assert '[OK] ReproAgent installed' in output
    assert 'credentials' in output.lower()
    assert '[OK] Ready' not in output


def test_check_error_is_not_hidden_after_setup_succeeds(bootstrap, checkout, commands, capsys):
    _, state = commands
    state['check_exit'] = 2
    state['check'] = {'code': 'invalid_arguments', 'ready': False}
    assert bootstrap.install(checkout) != 0
    assert '[OK] Ready' not in capsys.readouterr().out


def test_wrong_checkout_import_stops_before_binding_hosts(bootstrap, checkout, commands):
    calls, state = commands
    state['source'] = str(checkout.parent / 'unrelated/src/reproagent/__init__.py')
    assert bootstrap.install(checkout) != 0
    assert not any(argv[1:4] == ['-m', 'reproagent', 'setup'] for argv, _ in calls)


def test_different_install_source_stops_before_binding_hosts(bootstrap, checkout, commands):
    calls, state = commands
    state['source_url'] = (checkout.parent / 'unrelated').as_uri()
    assert bootstrap.install(checkout) != 0
    assert not any(argv[1:4] == ['-m', 'reproagent', 'setup'] for argv, _ in calls)


def test_pip_destination_overrides_cannot_escape_private_environment(bootstrap, checkout, commands, monkeypatch):
    calls, _ = commands
    redirects = ('PIP_TARGET', 'PIP_PREFIX', 'PIP_ROOT', 'PIP_USER', 'PIP_PYTHON', 'PYTHONUSERBASE')
    for name in redirects:
        monkeypatch.setenv(name, str(checkout.parent / 'outside-environment'))
    monkeypatch.setenv('PIP_CONFIG_FILE', str(checkout.parent / 'user-pip.ini'))
    monkeypatch.setenv('PIP_INDEX_URL', 'https://mirror.example.invalid/simple')
    assert bootstrap.install(checkout) == 0
    _, options = next((argv, kwargs) for argv, kwargs in calls if argv[1:3] == ['-m', 'pip'])
    assert not any(name in options['env'] for name in redirects)
    assert options['env']['PIP_CONFIG_FILE'] == os.devnull
    assert options['env']['PIP_INDEX_URL'] == 'https://mirror.example.invalid/simple'


def test_parent_python_paths_and_secret_are_not_echoed(bootstrap, checkout, commands, monkeypatch, capsys):
    calls, _ = commands
    monkeypatch.setenv('PYTHONPATH', 'wrong-checkout')
    monkeypatch.setenv('PYTHONHOME', 'wrong-interpreter')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'canary-do-not-echo-54321')
    assert bootstrap.install(checkout) == 0
    for _, kwargs in calls:
        assert 'PYTHONPATH' not in kwargs['env']
        assert 'PYTHONHOME' not in kwargs['env']
        assert kwargs['env']['DEEPSEEK_API_KEY'] == 'canary-do-not-echo-54321'
    output = capsys.readouterr()
    assert 'canary-do-not-echo-54321' not in output.out + output.err


def test_collect_only_without_project_is_rejected(bootstrap, checkout, commands):
    calls, _ = commands
    assert bootstrap.install(checkout, collect_only=True) != 0
    assert not calls


def test_missing_project_is_rejected_before_lock(bootstrap, checkout, commands):
    calls, _ = commands
    assert bootstrap.install(checkout, project=checkout / 'missing') != 0
    assert not calls


def test_project_check_is_called_and_report_written(bootstrap, checkout, commands, monkeypatch, capsys):
    calls, _ = commands
    report = {
        'ready': True,
        'python': {'path': 'x', 'version': '3.12', 'source': 'explicit'},
        'pytest': {'available': True, 'version': '8'},
        'test_dir': 'tests',
        'target_modules': [],
        'collect_only': {'ran': False},
        'errors': [],
        'warnings': [],
    }
    seen = {}
    monkeypatch.setattr(
        bootstrap.project_check, 'check_project',
        lambda *args, **kwargs: (seen.update({'args': args, 'kwargs': kwargs}) or report))
    monkeypatch.setattr(
        bootstrap.project_check, 'summary_lines', lambda result: ['Project: ok'])
    target = checkout / 'target'
    target.mkdir()
    assert bootstrap.install(checkout, project=target) == 0
    assert seen['args'][0] == target.resolve()
    assert (checkout / '.local/project-check.json').is_file()
    assert 'Project: ok' in capsys.readouterr().out
