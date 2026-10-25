"""Run the actual Windows batch/PowerShell chain without installing dependencies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows bootstrap entry')


def _fixture(tmp_path):
    root = tmp_path / '中文 folder & literal % path'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / 'install.cmd', root / 'install.cmd')
    shutil.copyfile(ROOT / 'scripts/install-windows.ps1', scripts / 'install-windows.ps1')
    (scripts / 'install.py').write_text(
        'import json,os,pathlib,sys\n'
        'pathlib.Path(os.environ["ENTRY_RECEIPT"]).write_text(json.dumps({\n'
        ' "argv":sys.argv[1:], "python":sys.executable,\n'
        ' "pythonpath":os.environ.get("PYTHONPATH"), "pythonhome":os.environ.get("PYTHONHOME")}), encoding="utf-8")\n'
        'sys.exit(int(os.environ.get("ENTRY_EXIT", "0")))\n', encoding='utf-8')
    receipt = tmp_path / 'receipt.json'
    environment = dict(os.environ, ENTRY_RECEIPT=str(receipt),
                       PYTHONPATH='an-unrelated-project', PYTHONHOME='an-unrelated-python')
    return root, receipt, environment


def _run(root, environment, arguments):
    # /s /c needs an extra pair around the command when its executable is quoted.
    command = ('"' + os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe')
               + '" /d /s /c ""' + str(root / 'install.cmd') + '" -NoPause ' + arguments + '"')
    return subprocess.run(command, cwd=root.parent, env=environment, shell=False,
                          capture_output=True, timeout=45)


@pytest.mark.parametrize('exit_code', [0, 7])
def test_batch_entry_forwards_literal_paths_and_preserves_exit_status(tmp_path, exit_code):
    root, receipt, environment = _fixture(tmp_path)
    environment['ENTRY_EXIT'] = str(exit_code)
    model = tmp_path / '中文 model & parameters.json'
    result = _run(root, environment, f'-Python "{sys.executable}" -Target claude-code -ModelConfig "{model}" -Force')
    assert result.returncode == exit_code, (result.stdout, result.stderr)
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    assert payload['argv'] == ['--host', 'claude-code', '--model-config', str(model), '--force']
    assert Path(payload['python']).resolve() == Path(sys.executable).resolve()
    assert payload['pythonpath'] is None
    assert payload['pythonhome'] is None


def test_missing_python_gives_actionable_error_without_invoking_install(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    result = _run(root, environment, f'-Python "{tmp_path / "not-installed.exe"}"')
    assert result.returncode == 2
    assert b'Python 3.11+' in result.stdout
    assert b'python.org' in result.stdout
    assert not receipt.exists()


def test_python_in_unicode_path_is_found_without_console_codepage_assumptions(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    interpreter_home = tmp_path / '\U00020bb7-python'
    creation_environment = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME'):
        creation_environment.pop(name, None)
    created = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(interpreter_home)],
                             env=creation_environment, capture_output=True, timeout=60)
    assert created.returncode == 0, created.stderr
    python = interpreter_home / 'Scripts/python.exe'
    result = _run(root, environment, f'-Python "{python}"')
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    assert Path(payload['python']).resolve() == python.resolve()


def test_batch_entry_forwards_project_options(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    target = tmp_path / 'target'
    target.mkdir()
    task = tmp_path / 'task.json'
    task.write_text('{}', encoding='utf-8')
    python = sys.executable
    result = _run(
        root, environment,
        f'-Python "{python}" -Project "{target}" -ProjectPython "{python}" '
        f'-TaskConfig "{task}" -CollectOnly -NoPythonInstall')
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    assert payload['argv'] == [
        '--host', 'all',
        '--project', str(target),
        '--project-python', str(python),
        '--task-config', str(task),
        '--collect-only',
    ]


def _sanitized_path(*extra):
    parts = [str(item) for item in extra]
    parts += [r'C:\Windows\System32',
              r'C:\Windows\System32\WindowsPowerShell\v1.0']
    return ';'.join(parts)


def test_no_python_with_no_install_fails_without_host_changes(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    environment = dict(environment)
    environment['PATH'] = _sanitized_path()
    result = _run(root, environment, '-NoPythonInstall')
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert b'Python 3.11+' in result.stdout
    assert not receipt.exists()


def test_fake_winget_path_is_used(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    fake_bin = tmp_path / 'fakebin'
    fake_bin.mkdir()
    winget = fake_bin / 'winget.cmd'
    winget.write_text('@echo off\r\nexit /b 0\r\n', encoding='ascii')
    environment = dict(environment)
    environment['PATH'] = _sanitized_path(fake_bin)
    environment['REPROAGENT_TEST_MODE'] = '1'
    environment['REPROAGENT_TEST_WINGET'] = str(winget)
    environment['REPROAGENT_TEST_PYTHON_AFTER_INSTALL'] = sys.executable
    result = _run(root, environment, '')
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    assert Path(payload['python']).resolve() == Path(sys.executable).resolve()


def test_repo_venv_is_discovered_without_python_on_path(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    source_python = Path(sys.executable).resolve()
    target_scripts = root / '.venv/Scripts'
    target_scripts.mkdir(parents=True)
    shutil.copyfile(source_python, target_scripts / 'python.exe')
    shutil.copyfile(source_python.parent.parent / 'pyvenv.cfg', root / '.venv/pyvenv.cfg')
    environment = dict(environment)
    environment['PATH'] = _sanitized_path()
    result = _run(root, environment, '-NoPythonInstall')
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    assert Path(payload['python']).resolve() == (target_scripts / 'python.exe').resolve()


def test_unsigned_official_installer_is_rejected(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    fake_bin = tmp_path / 'fakebin2'
    fake_bin.mkdir()
    winget = fake_bin / 'winget.cmd'
    winget.write_text('@echo off\r\nexit /b 1\r\n', encoding='ascii')
    unsigned = tmp_path / 'python-installer.exe'
    unsigned.write_bytes(b'not a signed installer')
    environment = dict(environment)
    environment['PATH'] = _sanitized_path(fake_bin)
    environment['REPROAGENT_TEST_WINGET'] = str(winget)
    environment['REPROAGENT_TEST_INSTALLER_PATH'] = str(unsigned)
    result = _run(root, environment, '')
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert b'Python 3.11+' in result.stdout
    assert not receipt.exists()
