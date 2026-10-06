import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


def python_in(root): return root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def command(argv, **kwargs):
    result = subprocess.run([str(arg) for arg in argv], capture_output=True, timeout=90, **kwargs)
    assert result.returncode == 0, result.stderr.decode(errors='replace') + result.stdout.decode(errors='replace')
    return result


def test_installed_wheel_can_run_probe_and_load_prompts_without_source_checkout(tmp_path, projects):
    checkout = Path(__file__).resolve().parents[2]
    wheels = tmp_path / 'wheels'
    command([sys.executable, '-m', 'pip', 'wheel', str(checkout), '--no-deps', '--no-build-isolation', '--wheel-dir', wheels])
    wheel = next(wheels.glob('*.whl'))
    tool = tmp_path / 'tool-env'
    command([sys.executable, '-m', 'venv', tool])
    command([python_in(tool), '-m', 'pip', 'install', '--no-index', '--no-deps', wheel])
    target = checkout / '.tmp/package-envs/target'
    if not python_in(target).exists():
        command([os.environ.get('REPRO_TEST_TARGET_PYTHON', sys.executable), '-m', 'venv', target])
        command([python_in(target), '-m', 'pip', 'install', os.environ.get('REPRO_TEST_PYTEST_VERSION', 'pytest==9.1.1')])
    repo = projects.plain(tmp_path / 'independent-repo')
    script = tmp_path / 'installed_run.py'
    script.write_text('''import asyncio, json, sys
from pathlib import Path
from importlib.resources import files
from reproagent.core.models import TaskRequest, PythonPytestConfig, CandidateDraft, DraftFile, RunContext, BudgetLimits
from reproagent.core.budget import Budget
from reproagent.workspace import Workspace
from reproagent.store import TaskStore
from reproagent.runner import Runner
from reproagent.reporting import render_report
repo, output, target = map(Path, sys.argv[1:])
store = TaskStore(output); workspace = Workspace(output, store)
ctx = RunContext(Budget(BudgetLimits()))
request = TaskRequest(repo, output, repo / 'issue.md', language=PythonPytestConfig(python=str(target), target_modules=('example.parser',)))
snapshot = workspace.freeze(request, ctx)
candidate = workspace.publish(CandidateDraft((DraftFile('tests/test_repro.py', b'from example.parser import parse\\ndef test_empty(): assert parse([]) == []\\n'),), snapshot.snapshot_id, 'c', 1, 'empty'), snapshot)
async def run():
    runner = Runner(workspace, store)
    environment = await runner.prepare(request, snapshot, ctx)
    execution = await runner.execute(candidate, snapshot, environment, ctx)
    return execution
execution = asyncio.run(run())
markdown = render_report({'package_kind':'diagnostic', 'verified':False, 'status':'BLOCKED', 'stop_reason':'missing pytest'})
print(json.dumps({'complete':execution.observation.probe_complete, 'exit_code':execution.raw.exit_code, 'markdown':markdown, 'prompt':files('reproagent').joinpath('prompts/analyze_issue.md').read_text(), 'module':__import__('reproagent').__file__}))
''', encoding='utf-8')
    env = dict(os.environ); env.pop('PYTHONPATH', None)
    result = command([python_in(tool), script, repo, tmp_path / 'results', python_in(target)], cwd=tmp_path, env=env)
    data = json.loads(result.stdout)
    assert data['complete'] and data['exit_code'] == 1
    assert '未确认复现' in data['markdown'] and 'missing pytest' in data['markdown']
    assert Path(data['module']).is_relative_to(tool)
    assert data['prompt'] == (checkout / 'src/reproagent/prompts/analyze_issue.md').read_text()
    missing = command([python_in(target), '-c', "import importlib.util; assert importlib.util.find_spec('reproagent') is None; assert importlib.util.find_spec('httpx') is None"], cwd=tmp_path, env=env)
    spec = json.loads(next((tmp_path / 'results/runs').glob('*/spec.json')).read_text())
    assert str(checkout / 'src') not in json.dumps(spec)
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(next(n for n in archive.namelist() if n.endswith('/METADATA'))).decode()
    assert 'Known limitations' in metadata
