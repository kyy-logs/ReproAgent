import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = {
    'reproagent/resources/integration/claude/SKILL.md.template',
    'reproagent/resources/integration/codex/SKILL.md.template',
    'reproagent/resources/integration/codex/agents/openai.yaml.template',
    'reproagent/resources/integration/reproagent.ps1.template',
    'reproagent/resources/integration/reproagent.sh.template',
}


def _venv_python(venv: Path) -> Path:
    return venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _isolated_env(home: Path, *, key='fake-wheel-key') -> dict:
    environment = {
        'HOME': str(home), 'USERPROFILE': str(home),
        'CLAUDE_CONFIG_DIR': str(home / '.claude'),
        'CODEX_HOME': str(home / '.codex'),
        'APPDATA': str(home / 'AppData' / 'Roaming'),
        'LOCALAPPDATA': str(home / 'AppData' / 'Local'),
        'PATH': '', 'PYTHONDONTWRITEBYTECODE': '1',
        'TEMP': os.environ.get('TEMP', ''), 'TMP': os.environ.get('TMP', ''),
        'SystemRoot': os.environ.get('SystemRoot', ''),
        'WINDIR': os.environ.get('WINDIR', ''),
        'SystemDrive': os.environ.get('SystemDrive', 'C:'),
        'ProgramData': os.environ.get('ProgramData', ''),
        'COMSPEC': os.environ.get('COMSPEC', ''),
        'PATHEXT': os.environ.get('PATHEXT', ''),
    }
    if key is not None:
        environment['DEEPSEEK_API_KEY'] = key
    return environment


@pytest.fixture(scope='module')
def wheel(tmp_path_factory):
    directory = tmp_path_factory.mktemp('wheel')
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps',
                    '--no-build-isolation', '--wheel-dir', str(directory)],
                   cwd=str(REPO_ROOT), check=True, capture_output=True, text=True)
    wheels = list(directory.glob('*.whl'))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_contains_every_integration_template(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert TEMPLATES <= names


def test_installed_wheel_works_outside_the_source_checkout(wheel, tmp_path):
    venv = tmp_path / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(venv)],
                   check=True, capture_output=True, text=True)
    python = _venv_python(venv)
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-index',
                    str(wheel)], check=True, capture_output=True, text=True)

    environment = _isolated_env(tmp_path / 'home')
    imported = subprocess.run([str(python), '-c',
        'import json,reproagent; print(json.dumps({"file": reproagent.__file__}))'],
        cwd=str(tmp_path), env=environment, capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr
    module_file = Path(json.loads(imported.stdout)['file'])
    assert str(module_file).startswith(str(venv))
    assert not str(module_file).startswith(str(REPO_ROOT))

    model = tmp_path / 'model.json'
    model.write_text(json.dumps({"base_url": "https://api.deepseek.com",
                                 "model": "fake-model",
                                 "api_key_env": "DEEPSEEK_API_KEY"}),
                     encoding='utf-8')
    installed = subprocess.run([str(python), '-m', 'reproagent', 'setup',
                                '--tool-python', str(python),
                                '--model-config', str(model)],
                               cwd=str(tmp_path), env=environment,
                               capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr
    codex_skill = tmp_path / 'home' / '.agents' / 'skills' / 'reproagent' / 'SKILL.md'
    assert codex_skill.is_file()
    assert 'allow_implicit_invocation' not in codex_skill.read_text(encoding='utf-8')

    checked = subprocess.run([str(python), '-m', 'reproagent', 'setup', '--check',
                              '--tool-python', str(python), '--model-config', str(model)],
                             cwd=str(tmp_path), env=environment,
                             capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout.strip().splitlines()[-1])
    assert payload['ready'] is True
    assert 'fake-wheel-key' not in checked.stdout + checked.stderr

    wrapper = (tmp_path / 'home' / '.agents' / 'skills' / 'reproagent'
               / 'scripts' / ('reproagent.ps1' if os.name == 'nt' else 'reproagent.sh'))
    if os.name == 'nt':
        wrapper_result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper),
                                         '-Check'], env=environment, cwd=str(tmp_path),
                                        capture_output=True, text=True)
    else:
        wrapper_result = subprocess.run(['/bin/sh', str(wrapper), 'check'], env=environment,
                                        cwd=str(tmp_path), capture_output=True, text=True)
    assert wrapper_result.returncode == 0, wrapper_result.stderr


def test_wheel_install_without_root_or_config_reports_missing(wheel, tmp_path):
    venv = tmp_path / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(venv)],
                   check=True, capture_output=True, text=True)
    python = _venv_python(venv)
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-index',
                    str(wheel)], check=True, capture_output=True, text=True)
    environment = _isolated_env(tmp_path / 'home', key=None)

    checked = subprocess.run([str(python), '-m', 'reproagent', 'setup', '--check',
                              '--tool-python', str(python)],
                             cwd=str(tmp_path), env=environment,
                             capture_output=True, text=True)

    assert checked.returncode == 1
    payload = json.loads(checked.stdout.strip().splitlines()[-1])
    assert payload['model_config_status'] == 'missing'
    assert payload['credential_source']['reason'] == 'model_config_required'
    assert 'model_config_missing' in payload['warnings']
    assert not (tmp_path / 'home' / '.reproagent').exists()
