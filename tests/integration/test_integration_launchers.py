import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from reproagent.integration.credentials import load_binding
from reproagent.integration.render import render_files


FAKE_LAUNCHER = textwrap.dedent('''
    import json
    import os
    import sys
    log = os.environ.get("REPRO_FAKE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "argv": sys.argv[1:],
                "key": os.environ.get("DEEPSEEK_API_KEY"),
                "cwd": os.getcwd(),
            }, ensure_ascii=False) + "\\n")
    raise SystemExit(int(os.environ.get("REPRO_FAKE_EXIT", "0")))
''')


def _fake_env(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / 'fake-py'
    package = root / 'reproagent' / 'integration'
    package.mkdir(parents=True)
    (root / 'reproagent' / '__init__.py').write_text('', encoding='utf-8')
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'launcher.py').write_text(FAKE_LAUNCHER, encoding='utf-8')
    log = tmp_path / 'fake-log.jsonl'
    return root, log


def _binding(tmp_path: Path, tool_python: Path) -> dict:
    model = tmp_path / 'model.json'
    model.write_text(json.dumps({"base_url": "https://api.deepseek.com",
                                 "model": "fake", "api_key_env": "DEEPSEEK_API_KEY"}),
                     encoding='utf-8')
    return load_binding(tool_python=tool_python, model_config=model,
                        repro_root=tmp_path / 'repro-root', env_file=None)


def _env(tmp_path: Path, fake_root: Path, log: Path, exit_code: int = 0) -> dict:
    env = dict(os.environ)
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(fake_root) + (os.pathsep + existing if existing else '')
    env['REPRO_FAKE_LOG'] = str(log)
    env['REPRO_FAKE_EXIT'] = str(exit_code)
    return env


def _records(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]


def _render(tmp_path: Path, *, platform_name: str, tool_python: Path) -> tuple[Path, Path]:
    skill_root = tmp_path / "skill dir's $x;"
    binding = {**_binding(tmp_path, tool_python), 'skill_root': str(skill_root)}
    files = render_files('codex', platform_name=platform_name, binding=binding)
    for relative, content in files.items():
        target = skill_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if platform_name == 'nt':
        return skill_root / 'scripts' / 'reproagent.ps1', skill_root
    script = skill_root / 'scripts' / 'reproagent.sh'
    script.chmod(0o755)
    return script, skill_root


def test_powershell_wrapper_forwards_actions_and_paths(tmp_path):
    if os.name != 'nt':
        pytest.skip('PowerShell wrapper is Windows-only')
    fake_root, log = _fake_env(tmp_path)
    wrapper, skill_root = _render(tmp_path, platform_name='nt', tool_python=Path(sys.executable))
    task = tmp_path / "task's $x; 中文.json"
    model = tmp_path / 'model config.json'
    model.write_text('{}', encoding='utf-8')
    fixed = tmp_path / "fixed repo's"
    env = _env(tmp_path, fake_root, log)

    check = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper), '-Check'],
                           env=env, capture_output=True, text=True)
    inspect = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper),
                              '-Inspect', str(tmp_path / "inspect dir's")],
                             env=env, capture_output=True, text=True)
    run = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper),
                          '-TaskConfig', str(task), '-ModelConfig', str(model),
                          '-FixedRepo', str(fixed), '-FixedPython', 'fixed-python'],
                         env=env, capture_output=True, text=True)

    assert (check.returncode, inspect.returncode, run.returncode) == (0, 0, 0)
    rows = _records(log)
    assert rows[0]['argv'][0] == '--binding-b64'
    assert rows[0]['argv'][2] == 'check'
    assert rows[1]['argv'][2:] == ['inspect', str(tmp_path / "inspect dir's")]
    assert rows[2]['argv'][2:] == [
        'run', str(task), '--model-config', str(model),
        '--fixed-repo', str(fixed), '--fixed-python', 'fixed-python']
    assert not (tmp_path / 'pwned').exists()


def test_powershell_wrapper_tool_python_priority_and_exit_code(tmp_path):
    if os.name != 'nt':
        pytest.skip('PowerShell wrapper is Windows-only')
    fake_root, log = _fake_env(tmp_path)
    missing = tmp_path / 'missing-python.exe'
    wrapper, _ = _render(tmp_path, platform_name='nt', tool_python=missing)
    env = _env(tmp_path, fake_root, log, exit_code=7)

    missing_result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper), '-Check'],
                                    env=env, capture_output=True, text=True)
    pinned_override = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper),
                                      '-Check', '-ToolPython', sys.executable],
                                     env=env, capture_output=True, text=True)
    env_override = dict(env)
    env_override['REPROAGENT_TOOL_PYTHON'] = sys.executable
    env_result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(wrapper), '-Check'],
                                env=env_override, capture_output=True, text=True)

    assert missing_result.returncode == 2
    assert pinned_override.returncode == 7
    assert env_result.returncode == 7
    assert len(_records(log)) == 2


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell commands are Windows-only')
def test_generated_skill_commands_run_with_literal_paths_and_documented_flags(tmp_path):
    fake_root, log = _fake_env(tmp_path)
    _, skill_root = _render(tmp_path, platform_name='nt', tool_python=Path(sys.executable))
    skill = (skill_root / 'SKILL.md').read_text(encoding='utf-8')
    commands = [line for line in skill.splitlines() if line.startswith('powershell.exe ')]
    env = _env(tmp_path, fake_root, log)
    check = subprocess.run(['powershell.exe', '-NoProfile', '-Command', commands[0]],
                           env=env, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    task = tmp_path / 'task.json'
    model = tmp_path / 'override-model.json'
    fixed = tmp_path / 'fixed'
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = commands[2].replace('"<task.json>"', quote(task))
    optional = skill.split('Optional run arguments:', 1)[1].split('## Workflow', 1)[0]
    flags = ('-ModelConfig', '-FixedRepo', '-FixedPython', '-ToolPython')
    for flag, value in zip(flags, (model, fixed, 'fixed-python', sys.executable)):
        assert flag in optional
        command += ' ' + flag + ' ' + quote(value)
    run = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                         env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    rows = _records(log)
    assert rows[0]['argv'][2:] == ['check']
    assert rows[1]['argv'][2:] == ['run', str(task), '--model-config', str(model),
                                 '--fixed-repo', str(fixed), '--fixed-python', 'fixed-python']


def test_posix_wrapper_forwards_actions_and_paths(tmp_path):
    if shutil.which('sh') is None:
        pytest.skip('POSIX shell unavailable')
    if os.name == 'nt':
        pytest.skip('POSIX wrapper is verified on Unix only; Windows sh run is not macOS/Linux evidence')
    fake_root, log = _fake_env(tmp_path)
    wrapper, _ = _render(tmp_path, platform_name='posix', tool_python=Path(sys.executable))
    task = tmp_path / "task's $x; 中文.json"
    model = tmp_path / 'model config.json'
    model.write_text('{}', encoding='utf-8')
    env = _env(tmp_path, fake_root, log)

    check = subprocess.run(['sh', str(wrapper), 'check'], env=env, capture_output=True, text=True)
    run = subprocess.run(['sh', str(wrapper), 'run', str(task), '--model-config', str(model),
                          '--fixed-repo', str(tmp_path / 'fixed')],
                         env=env, capture_output=True, text=True)

    assert (check.returncode, run.returncode) == (0, 0)
    rows = _records(log)
    assert rows[0]['argv'][2] == 'check'
    assert rows[1]['argv'][2:] == ['run', str(task), '--model-config', str(model),
                                   '--fixed-repo', str(tmp_path / 'fixed')]


def test_posix_wrapper_tool_python_priority_and_exit_code(tmp_path):
    if os.name == 'nt' or shutil.which('sh') is None:
        pytest.skip('POSIX wrapper is verified on Unix only')
    fake_root, log = _fake_env(tmp_path)
    missing = tmp_path / 'missing-python'
    wrapper, _ = _render(tmp_path, platform_name='posix', tool_python=missing)
    env = _env(tmp_path, fake_root, log, exit_code=7)

    missing_result = subprocess.run(['sh', str(wrapper), 'check'], env=env,
                                    capture_output=True, text=True)
    explicit = subprocess.run(['sh', str(wrapper), '--tool-python', sys.executable, 'check'],
                              env=env, capture_output=True, text=True)

    assert missing_result.returncode == 2
    assert explicit.returncode == 7
    assert len(_records(log)) == 1
