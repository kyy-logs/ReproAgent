import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _isolated(tmp_path: Path, *, key: str | None = None) -> tuple[Path, dict]:
    home = tmp_path / 'home'
    environment = {
        'HOME': str(home),
        'USERPROFILE': str(home),
        'CLAUDE_CONFIG_DIR': str(home / '.claude'),
        'CODEX_HOME': str(home / '.codex'),
        'APPDATA': str(home / 'AppData' / 'Roaming'),
        'LOCALAPPDATA': str(home / 'AppData' / 'Local'),
        'PATH': '',
        'PYTHONDONTWRITEBYTECODE': '1',
        'TEMP': os.environ.get('TEMP', ''),
        'TMP': os.environ.get('TMP', ''),
        'SystemRoot': os.environ.get('SystemRoot', ''),
        'WINDIR': os.environ.get('WINDIR', ''),
        'SystemDrive': os.environ.get('SystemDrive', 'C:'),
        'ProgramData': os.environ.get('ProgramData', ''),
        'COMSPEC': os.environ.get('COMSPEC', ''),
        'PATHEXT': os.environ.get('PATHEXT', ''),
    }
    if key is not None:
        environment['DEEPSEEK_API_KEY'] = key
    return home, environment


def _materials(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / 'repro-root'
    root.mkdir(parents=True, exist_ok=True)
    model = tmp_path / 'model.json'
    model.write_text(json.dumps({"base_url": "https://api.deepseek.com",
                                 "model": "fake-model",
                                 "api_key_env": "DEEPSEEK_API_KEY"}),
                     encoding='utf-8')
    return root, model


def _run(tmp_path: Path, environment: dict, *args: str) -> subprocess.CompletedProcess:
    root, model = _materials(tmp_path)
    command = [sys.executable, '-m', 'reproagent', 'setup',
               '--tool-python', sys.executable,
               '--model-config', str(model),
               '--repro-root', str(root), *args]
    return subprocess.run(command, env=environment, cwd=str(REPO_ROOT),
                          capture_output=True, text=True)


def _payload(result: subprocess.CompletedProcess) -> dict:
    return json.loads(result.stdout.strip().splitlines()[-1])


def _snapshot(root: Path) -> dict:
    if not root.exists():
        return {}
    entries = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            entries[relative] = (path.read_bytes(), path.stat().st_mtime_ns)
        else:
            entries[relative] = b'<dir>'
    return entries


def test_cli_installs_both_hosts_and_reinstall_is_idempotent(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')
    first = _run(tmp_path, environment)
    assert first.returncode == 0, first.stderr
    claude = home / '.claude' / 'skills' / 'reproagent'
    codex = home / '.agents' / 'skills' / 'reproagent'
    assert (claude / 'SKILL.md').is_file()
    assert (codex / 'SKILL.md').is_file()
    assert (codex / 'agents' / 'openai.yaml').is_file()
    assert not (home / '.codex' / 'skills' / 'reproagent').exists()
    before = _snapshot(home)

    second = _run(tmp_path, environment)

    assert second.returncode == 0, second.stderr
    assert _snapshot(home) == before
    assert not list(home.rglob('*reproagent-backup-*'))


def test_cli_check_is_zero_write_and_does_not_leak_a_key(tmp_path):
    home, environment = _isolated(tmp_path, key='fake-canary-abc')
    _run(tmp_path, environment)
    before = _snapshot(home)

    checked = _run(tmp_path, environment, '--check')

    assert checked.returncode == 0, checked.stderr
    assert _snapshot(home) == before
    assert 'fake-canary-abc' not in checked.stdout + checked.stderr
    payload = _payload(checked)
    assert payload['ready'] is True
    assert payload['hosts']['codex']['credential_source']['kind'] == 'environment'


def test_cli_dry_run_writes_nothing(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')

    result = _run(tmp_path, environment, '--dry-run')

    assert result.returncode == 0, result.stderr
    assert not home.exists()
    payload = _payload(result)
    assert payload['action'] == 'install'
    assert payload['operations']


def test_cli_dry_run_reports_a_conflict_without_writing(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')
    codex = home / '.agents' / 'skills' / 'reproagent'
    codex.mkdir(parents=True)
    (codex / 'SKILL.md').write_bytes(b'foreign')

    result = _run(tmp_path, environment, '--dry-run')

    assert result.returncode == 0, result.stderr
    payload = _payload(result)
    assert payload['conflicts']
    assert (codex / 'SKILL.md').read_bytes() == b'foreign'
    assert not (home / '.claude').exists()


def test_cli_conflict_blocks_both_hosts_and_force_backs_up(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')
    codex = home / '.agents' / 'skills' / 'reproagent'
    codex.mkdir(parents=True)
    (codex / 'SKILL.md').write_bytes(b'foreign')
    claude = home / '.claude' / 'skills' / 'reproagent'

    rejected = _run(tmp_path, environment)

    assert rejected.returncode == 2
    assert _payload(rejected)['code'] == 'conflict'
    assert (codex / 'SKILL.md').read_bytes() == b'foreign'
    assert not (claude / 'SKILL.md').exists()

    forced = _run(tmp_path, environment, '--force')

    assert forced.returncode == 0, forced.stderr
    backups = list(codex.glob('SKILL.md.reproagent-backup-*'))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b'foreign'
    assert (claude / 'SKILL.md').is_file()


def test_cli_uninstall_preserves_a_user_modified_file(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')
    _run(tmp_path, environment)
    claude = home / '.claude' / 'skills' / 'reproagent'
    codex = home / '.agents' / 'skills' / 'reproagent'
    (claude / 'SKILL.md').write_bytes(b'user-edit')

    removed = _run(tmp_path, environment, '--uninstall')

    assert removed.returncode == 0, removed.stderr
    assert (claude / 'SKILL.md').read_bytes() == b'user-edit'
    assert not (codex / 'SKILL.md').exists()


def test_cli_explicit_legacy_does_not_write_standard_root(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')

    result = _run(tmp_path, environment, '--host', 'codex', '--codex-layout', 'legacy')

    assert result.returncode == 0, result.stderr
    assert (home / '.codex' / 'skills' / 'reproagent' / 'SKILL.md').is_file()
    assert not (home / '.agents' / 'skills' / 'reproagent').exists()


def test_cli_missing_credential_installs_with_warning_and_check_is_not_ready(tmp_path):
    home, environment = _isolated(tmp_path, key=None)

    installed = _run(tmp_path, environment)
    checked = _run(tmp_path, environment, '--check')

    assert installed.returncode == 0, installed.stderr
    assert 'credential_missing' in _payload(installed)['warnings']
    assert checked.returncode == 1
    assert _payload(checked)['ready'] is False


def test_cli_rejects_invalid_option_combinations(tmp_path):
    home, environment = _isolated(tmp_path, key='fake')

    result = _run(tmp_path, environment, '--check', '--uninstall')

    assert result.returncode == 2
    assert not home.exists()
    assert _payload(result)['code'] == 'invalid_arguments'
