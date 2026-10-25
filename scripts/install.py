"""Small stdlib-only bootstrap for install.cmd; setup owns host integration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import project_check


OWNER = 'reproagent-windows-installer-v1'


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'PIP_TARGET',
                 'PIP_PREFIX', 'PIP_ROOT', 'PIP_USER', 'PIP_PYTHON'):
        environment.pop(name, None)
    # Ignore pip configuration files that could redirect the destination. Network
    # settings supplied through environment variables (mirror/proxy/cert) remain.
    environment['PIP_CONFIG_FILE'] = os.devnull
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['PYTHONUTF8'] = '1'
    return environment


def _call(argv: list[str], root: Path, *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=root, env=_environment(), shell=False,
                          capture_output=capture, text=capture,
                          encoding='utf-8' if capture else None,
                          timeout=1200 if not capture else 120)


def _setup(argv: list[str], root: Path, *, check: bool = False) -> dict:
    result = _call(argv + (['--check'] if check else []), root, capture=True)
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise RuntimeError('The setup command returned an unreadable result. See docs/windows-install.md.') from None
    if result.returncode not in ((0, 1) if check else (0,)):
        code = payload.get('code', 'setup_failed')
        if code == 'conflict':
            raise RuntimeError('Host entry conflict: existing files were preserved. Review them first; '
                               'install.cmd -Force backs them up before replacement.')
        raise RuntimeError(f'Setup failed ({code}). See docs/ai-tool-integration.md.')
    if check and (not isinstance(payload.get('ready'), bool)
                  or bool(payload['ready']) != (result.returncode == 0)):
        raise RuntimeError('Setup readiness could not be verified. See docs/ai-tool-integration.md.')
    return payload


def _validate_project_args(project, project_python, task_config, collect_only):
    if collect_only and project is None:
        raise RuntimeError('--collect-only requires --project.')
    if project is None:
        return None, None, None
    project = Path(project).resolve()
    if not project.is_dir():
        raise RuntimeError('The target project directory does not exist.')
    if project_python is not None:
        project_python = Path(project_python).resolve()
        if not project_python.is_file():
            raise RuntimeError('The target project Python does not exist.')
    if task_config is not None:
        task_config = Path(task_config).resolve()
        if not task_config.is_file():
            raise RuntimeError('The task configuration does not exist.')
        try:
            json.loads(task_config.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('The task configuration is not valid JSON.')
    return project, project_python, task_config


def _launcher_check(status, host, root):
    selected = ('claude-code', 'codex') if host == 'all' else (host,)
    for name in selected:
        info = (status.get('hosts') or {}).get(name)
        if not info or not info.get('root'):
            print(f'[ACTION] Launcher check skipped: {name} has no installed root.')
            continue
        script = Path(info['root']) / 'scripts' / 'reproagent.ps1'
        if not script.is_file():
            print(f'[ACTION] Launcher check skipped: {script} is missing.')
            continue
        powershell = shutil.which('powershell.exe')
        if not powershell:
            print('[ACTION] Launcher check skipped: powershell.exe not found.')
            continue
        result = _call([powershell, '-NoProfile', '-File', str(script), '-Check'],
                       root, capture=True)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = {}
        if result.returncode == 0 and payload.get('ready') is True:
            print(f'[OK] Launcher check passed: {name}')
        else:
            print(f'[ACTION] Launcher check failed: {name}')


def _verify_source(python: Path, root: Path) -> None:
    code = ('import importlib.metadata,json,sys,reproagent; '
            'source=json.loads(importlib.metadata.distribution("reproagent-local").read_text("direct_url.json")); '
            'print(json.dumps({"module_file":reproagent.__file__,"prefix":sys.prefix,'
            '"source_url":source.get("url"),'
            '"editable":source.get("dir_info",{}).get("editable",False)}))')
    result = _call([str(python), '-c', code], root, capture=True)
    try:
        payload = json.loads(result.stdout)
        expected = python.parent.parent / 'Lib' / 'site-packages' / 'reproagent' / '__init__.py'
        valid = (result.returncode == 0 and isinstance(payload, dict)
                 and Path(payload['module_file']).resolve() == expected.resolve()
                 and Path(payload['prefix']).resolve() == python.parent.parent.resolve()
                 and isinstance(payload.get('source_url'), str)
                 and unquote(payload['source_url']).casefold() == unquote(root.as_uri()).casefold()
                 and payload.get('editable') is False)
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise RuntimeError('The installed tool does not import from this checkout and private environment. '
                           'Check Python/pip configuration before retrying.')


def _tool_python(root: Path) -> Path:
    environment = root / '.local' / 'install-env'
    marker = environment / '.reproagent-installer.json'
    if environment.exists():
        try:
            owned = (not environment.is_symlink()
                     and json.loads(marker.read_text(encoding='utf-8')) == {'owner': OWNER})
        except (OSError, ValueError):
            owned = False
        if not owned:
            raise RuntimeError('The folder .local/install-env is not owned by this installer; '
                               'it was preserved. Move it aside before retrying.')
    else:
        environment.mkdir()
        with marker.open('x', encoding='utf-8') as stream:
            json.dump({'owner': OWNER}, stream)
    python = environment / 'Scripts' / 'python.exe'
    if not python.is_file() or not (environment / 'pyvenv.cfg').is_file():
        result = _call([sys.executable, '-m', 'venv', str(environment)], root)
        if result.returncode != 0 or not python.is_file():
            raise RuntimeError('Could not create the tool environment. Check disk space and Python venv support, '
                               'then run install.cmd again.')
    return python


def install(root: Path, *, host: str = 'all', model_config: Path | None = None,
            force: bool = False, project: Path | None = None,
            project_python: Path | None = None, task_config: Path | None = None,
            collect_only: bool = False) -> int:
    """Install tool dependencies and host entries without running a model."""
    if sys.version_info < (3, 11):
        print('[ERROR] Python 3.11+ is required. Install Python from https://www.python.org/downloads/windows/ '
              'with the Python launcher or PATH option, then retry.')
        return 2
    root = Path(root).resolve()
    lock = root / '.local' / 'install.lock'
    acquired = False
    try:
        target_project, project_python, task_config = _validate_project_args(
            project, project_python, task_config, collect_only)
        import tomllib
        metadata = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        if (metadata.get('project', {}).get('name') != 'reproagent-local'
                or not (root / 'src/reproagent/integration/setup.py').is_file()):
            raise RuntimeError('This checkout does not include reproagent setup. Use a complete current source checkout.')
        if host not in ('all', 'claude-code', 'codex'):
            raise RuntimeError('Host must be all, claude-code or codex.')
        if model_config is not None:
            model_config = Path(model_config).resolve()
            if not model_config.is_file():
                raise RuntimeError('The selected model configuration file does not exist.')
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock.open('x', encoding='utf-8') as stream:
                acquired = True
                stream.write(str(os.getpid()))
        except FileExistsError:
            raise RuntimeError('Another installation may be running (.local/install.lock). '
                               'Wait for it to finish. After a crash, confirm no installer is running before removing that lock.') from None
        print('[1/4] Python version checked; preparing a private tool environment...', flush=True)
        python = _tool_python(root)
        print('[2/4] Installing ReproAgent and its dependencies (internet required)...', flush=True)
        pip = _call([str(python), '-m', 'pip', '--disable-pip-version-check',
                     'install', '--no-user', str(root)], root)
        if pip.returncode != 0:
            raise RuntimeError('Dependency installation failed. Check the pip output and network connection, '
                               'then run install.cmd again. Host entries have not been changed.')
        _verify_source(python, root)
        print('[3/4] Installing user-level Claude Code / Codex entries...', flush=True)
        command = [str(python), '-m', 'reproagent', 'setup', '--host', host,
                   '--repro-root', str(root), '--tool-python', str(python)]
        if model_config is not None:
            command += ['--model-config', str(model_config)]
        _setup(command + (['--force'] if force else []), root)
        print('[4/4] Checking installation...', flush=True)
        status = _setup(command, root, check=True)
        print('[OK] ReproAgent installed. No model was called.')
        if status['ready']:
            print('[OK] Ready. Restart your Claude Code / Codex session.')
        else:
            print('[ACTION] Installed; configuration is not ready yet.')
            if status.get('model_config_status') != 'ready':
                print('Select a model configuration: install.cmd -ModelConfig C:\\path\\model.json')
            if not status.get('run_configured'):
                print('Check the model configuration and API credentials. See docs/windows-install.md.')
            for name, value in status.get('hosts', {}).items():
                if not value.get('ready'):
                    print(f'  {name}: entry={value.get("entry_status", "unknown")}; '
                          f'launcher_importable={value.get("launcher_importable", False)}')
        print('[check] Launcher binding check...', flush=True)
        _launcher_check(status, host, root)
        if target_project is not None:
            print('[check] Target project environment...', flush=True)
            try:
                result = project_check.check_project(
                    target_project, python=project_python, task_config=task_config,
                    collect_only=collect_only)
            except (OSError, ValueError, RuntimeError) as error:
                print(f'[ACTION] Project check failed: {error}')
            else:
                report = root / '.local' / 'project-check.json'
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
                for line in project_check.summary_lines(result):
                    print(line)
                print(f'[{"OK" if result.get("ready") else "ACTION"}] '
                      f'Project check report: {report}')
        if host in ('all', 'claude-code'):
            print('Claude Code: /reproagent <bug description>')
        if host in ('all', 'codex'):
            print('Codex: $reproagent <bug description>')
        return 0
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        if isinstance(error, RuntimeError):
            message = str(error)
        elif isinstance(error, subprocess.TimeoutExpired):
            message = 'Installation timed out. Check the network and retry install.cmd.'
        else:
            message = 'Could not read or write installation files. Check permissions, disk space and the source checkout.'
        print('[ERROR] ' + message)
        return 2
    finally:
        if acquired:
            lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description='Install ReproAgent and its AI-tool entries; no model calls.')
    parser.add_argument('--host', choices=('all', 'claude-code', 'codex'), default='all')
    parser.add_argument('--model-config', type=Path)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--project', type=Path)
    parser.add_argument('--project-python', type=Path)
    parser.add_argument('--task-config', type=Path)
    parser.add_argument('--collect-only', action='store_true')
    args = parser.parse_args()
    return install(Path(__file__).resolve().parents[1], host=args.host,
                   model_config=args.model_config, force=args.force,
                   project=args.project, project_python=args.project_python,
                   task_config=args.task_config, collect_only=args.collect_only)


if __name__ == '__main__':
    raise SystemExit(main())
