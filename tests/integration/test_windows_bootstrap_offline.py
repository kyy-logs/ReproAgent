"""Real package install + setup in a disposable checkout/home, with no network."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows bootstrap environment')


@pytest.fixture
def model_endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            self.send_response(503)
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_offline_install(tmp_path, model_endpoint):
    import pip

    root = tmp_path / 'install-root'
    root.mkdir()
    for name in ('pyproject.toml', 'README.md', 'install.cmd'):
        shutil.copyfile(ROOT / name, root / name)
    for name in ('src', 'scripts'):
        shutil.copytree(ROOT / name, root / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    (root / 'examples').mkdir()
    (root / 'examples/model.deepseek.json').write_text(json.dumps({
        'base_url': model_endpoint[0], 'model': 'fake-not-called',
        'api_key_env': 'DEEPSEEK_API_KEY'}), encoding='utf-8')
    tool_environment = root / '.local/install-env'
    created = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(tool_environment)],
                             capture_output=True, timeout=60)
    assert created.returncode == 0, created.stderr
    # Expose preinstalled test dependencies, not a second copy of ReproAgent.
    dependency_site = str(Path(pip.__file__).resolve().parents[1])
    private_site = str(tool_environment / 'Lib/site-packages')
    bridge = ('import site,sys,os; site.addsitedir(' + repr(dependency_site) + '); '
              'sys.path[:] = [p for p in sys.path '
              'if os.path.normcase(p)==os.path.normcase(' + repr(private_site) + ') '
              'or not os.path.isfile(os.path.join(p,"reproagent","__init__.py"))]\n')
    (tool_environment / 'Lib/site-packages/00-test-dependencies.pth').write_text(bridge, encoding='utf-8')
    (tool_environment / '.reproagent-installer.json').write_text(
        '{"owner":"reproagent-windows-installer-v1"}', encoding='utf-8')
    home = tmp_path / 'user-home'
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                       CLAUDE_CONFIG_DIR=str(home / '.claude'), CODEX_HOME=str(home / '.codex'),
                       DEEPSEEK_API_KEY='offline-canary-never-send-98765',
                       PIP_NO_INDEX='1', PIP_NO_DEPS='1', PIP_NO_BUILD_ISOLATION='0',
                       PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE='1')
    for name in ('PYTHONPATH', 'PYTHONHOME'):
        environment.pop(name, None)
    command = [sys.executable, str(root / 'scripts/install.py')]
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, timeout=150)
    assert result.returncode == 0, result.stdout + result.stderr
    assert b'[OK] Ready' in result.stdout
    launcher_lines = [line for line in result.stdout.decode('utf-8', 'replace').splitlines()
                      if 'Launcher' in line]
    assert any('Launcher check passed: claude-code' in line for line in launcher_lines), launcher_lines
    assert any('Launcher check passed: codex' in line for line in launcher_lines), launcher_lines
    assert b'offline-canary-never-send-98765' not in result.stdout + result.stderr
    entries = [home / '.claude/skills/reproagent/SKILL.md', home / '.agents/skills/reproagent/SKILL.md']
    assert all(path.is_file() for path in entries)
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in home.rglob('*') if path.is_file()}
    second = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, timeout=150)
    assert second.returncode == 0, second.stdout + second.stderr
    assert {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in home.rglob('*') if path.is_file()} == before
    target = tmp_path / 'target-project'
    (target / 'tests').mkdir(parents=True)
    (target / 'tests/test_ok.py').write_text(
        'def test_ok():\n    assert True\n', encoding='utf-8')
    (target / 'issue.md').write_text('bug', encoding='utf-8')
    task = target / 'task.json'
    task.write_text(json.dumps({
        'schema_version': 1, 'repo': str(target),
        'issue_file': str(target / 'issue.md'),
        'output_dir': str(target / 'out'),
        'language': {'python': sys.executable, 'target_modules': [],
                     'source_roots': [], 'candidate_parent': 'tests',
                     'pytest_args': ['-q']},
    }), encoding='utf-8')
    target_before = {str(path): path.read_bytes()
                     for path in target.rglob('*') if path.is_file()}
    checked = subprocess.run(
        [sys.executable, str(root / 'scripts/install.py'), '--project', str(target),
         '--project-python', sys.executable, '--task-config', str(task), '--collect-only'],
        cwd=tmp_path, env=environment, capture_output=True, timeout=150)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    report = json.loads((root / '.local/project-check.json').read_text(encoding='utf-8'))
    assert report['ready'] is True, report
    assert report['collect_only']['collected'] >= 1, report
    assert {str(path): path.read_bytes()
            for path in target.rglob('*') if path.is_file()} == target_before
    python = tool_environment / 'Scripts/python.exe'
    provenance = subprocess.run([str(python), '-c', 'import json,reproagent; print(json.dumps(reproagent.__file__))'],
                                cwd=tmp_path, env=environment, capture_output=True, timeout=30)
    assert provenance.returncode == 0, provenance.stderr
    assert str(tool_environment / 'Lib/site-packages/reproagent').lower() in json.loads(provenance.stdout).lower()
    for path in home.rglob('*'):
        if path.is_file():
            assert b'offline-canary-never-send-98765' not in path.read_bytes()
    assert not (root / '.venv').exists()
    assert not (root / '.local/install.lock').exists()
    assert model_endpoint[1] == [], 'Installing/checking must not call the configured model endpoint'
