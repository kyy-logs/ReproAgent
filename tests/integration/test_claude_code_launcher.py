import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from reproagent.core.models import TaskResult, TaskState
from reproagent.store import TaskStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.claude/skills/reproagent/scripts/reproagent.ps1'
POWERSHELL = shutil.which('powershell.exe')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell launcher')


def invoke(*args, api_key=None):
    env = dict(os.environ)
    env.pop('DEEPSEEK_API_KEY', None)
    if api_key is not None:
        env['DEEPSEEK_API_KEY'] = api_key
    return subprocess.run([POWERSHELL, '-NoProfile', '-File', str(SCRIPT), *args],
                          capture_output=True, text=True, encoding='utf-8', timeout=30, env=env)


def test_launcher_inspects_unicode_directory_without_credentials_or_mutation(tmp_path):
    folder = tmp_path / '中文 task with spaces'
    TaskStore(folder).save_record('task', 'task', TaskResult('inspect-me', TaskState.EXHAUSTED))
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    result = invoke('-Inspect', str(folder), '-ModelConfig', str(tmp_path / 'missing-model.json'))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['task']['status'] == 'EXHAUSTED'
    assert before == {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def test_launcher_check_reports_readiness_without_model_call():
    result = invoke('-Check')
    assert result.returncode == 0, result.stderr
    ready = json.loads(result.stdout)
    assert ready['cli_available'] is True
    assert Path(ready['tool_python']).is_file()


def test_launcher_preserves_cli_configuration_error_and_does_not_create_output(tmp_path):
    config = tmp_path / 'invalid task.json'
    config.write_text('{"schema_version":1,"repo":"x","repo":"y"}', encoding='utf-8')
    result = invoke('-TaskConfig', str(config), api_key='launcher-test-placeholder')
    assert result.returncode == 2
    assert 'duplicate' in result.stderr.lower()
    assert 'launcher-test-placeholder' not in result.stdout + result.stderr
    assert not (tmp_path / 'output').exists()


def test_launcher_loads_only_host_security_module_for_encrypted_fallback(tmp_path):
    project = tmp_path / 'tool project'
    script = project / '.claude/skills/reproagent/scripts/reproagent.ps1'
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    (project / '.local').mkdir()
    (project / 'examples').mkdir()
    model = project / 'examples/model.deepseek.json'
    model.write_text(json.dumps({'api_key_env': 'DEEPSEEK_API_KEY'}))
    config = project / 'bad task.json'
    config.write_text('{"schema_version":1,"repo":"x","repo":"y"}')
    # A synthetic encrypted secret, unrelated to the user's actual credentials.
    fixture = project / 'invoke.ps1'
    fixture.write_text('''param([string]$Launcher,[string]$Python,[string]$Task,[string]$KeyFile)
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security')
ConvertTo-SecureString 'launcher-fixture-only' -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $KeyFile
Remove-Module Microsoft.PowerShell.Security
$env:PSModulePath = ''
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
& $Launcher -TaskConfig $Task -ToolPython $Python
exit $LASTEXITCODE
''', encoding='utf-8')
    result = subprocess.run([POWERSHELL, '-NoProfile', '-File', str(fixture), str(script),
                             str(ROOT / '.venv/Scripts/python.exe'), str(config), str(project / '.local/deepseek.key')],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 2
    assert 'duplicate' in result.stderr.lower(), result.stderr
    assert 'launcher-fixture-only' not in result.stdout + result.stderr


def fake_cli(tmp_path,monkeypatch):
    """A stand-in CLI that prints the arguments the launcher passed it."""
    fake=tmp_path/'fake-module'/'reproagent'
    fake.mkdir(parents=True)
    (fake/'__init__.py').write_text('',encoding='utf-8')
    (fake/'__main__.py').write_text('import json,sys;print(json.dumps(sys.argv))',encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH',str(fake.parent))


def test_launcher_starts_a_default_run_without_a_backend_choice(tmp_path,monkeypatch):
    """One infrastructure means the launcher selects nothing on its own."""
    fake_cli(tmp_path,monkeypatch)
    result=invoke('-TaskConfig',str(tmp_path/'task.json'),api_key='launcher-test-placeholder')
    assert result.returncode==0,result.stderr
    args=json.loads(result.stdout)
    assert '--model-backend' not in args and '--agent-backend' not in args
    assert 'deprecated' not in result.stderr


def test_launcher_forwards_explicit_legacy_backends_as_deprecated_aliases(tmp_path,monkeypatch):
    fake_cli(tmp_path,monkeypatch)
    result=invoke('-TaskConfig',str(tmp_path/'task.json'),'-ModelBackend','agentscope','-AgentBackend','agentscope',api_key='launcher-test-placeholder')
    assert result.returncode==0,result.stderr
    args=json.loads(result.stdout)
    assert args[args.index('--model-backend')+1]=='agentscope'
    assert args[args.index('--agent-backend')+1]=='agentscope'
    assert 'deprecated' in result.stderr
