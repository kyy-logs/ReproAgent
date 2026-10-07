import importlib
import json

import pytest

from reproagent.core.models import TaskRequest, TaskResult, TaskState
from reproagent.store import TaskStore


def test_duplicate_config_rejected_without_creating_output(tmp_path):
    config = tmp_path / 'task.json'; config.write_text('{"schema_version":1,"repo":"a","repo":"b"}')
    cli = importlib.import_module('reproagent.cli')
    assert cli.main(['run','--config',str(config),'--model-config',str(tmp_path / 'missing')]) == 2
    assert not (tmp_path / 'output').exists()


def test_inspect_unicode_task_is_read_only(tmp_path, capsys):
    root = tmp_path / '中文 任务'; store = TaskStore(root)
    store.save_record('task','task',TaskResult('task',TaskState.EXHAUSTED))
    before = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    cli = importlib.import_module('reproagent.cli')
    assert cli.main(['inspect',str(root)]) == 0
    assert 'EXHAUSTED' in capsys.readouterr().out
    assert before == {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_ctrl_c_exit_code(monkeypatch):
    cli = importlib.import_module('reproagent.cli')
    def interrupted(args): raise KeyboardInterrupt
    monkeypatch.setattr(cli, 'run_command', interrupted)
    assert cli.main(['run','--config','x','--model-config','y']) == 130


def run_cli(tmp_path, monkeypatch, argv):
    """Drive ``main`` with the request and the controller both stubbed out."""
    cli = importlib.import_module('reproagent.cli')
    repo = tmp_path / 'repo'; repo.mkdir(exist_ok=True)
    (repo / 'issue.md').write_text('parse([]) must return an empty list.\n', encoding='utf-8')
    request = TaskRequest(repo, tmp_path / 'output', repo / 'issue.md')
    model_config = tmp_path / 'model.json'
    model_config.write_text(json.dumps({'base_url':'https://offline.example/v1','model':'offline',
                                        'api_key_env':'REPROAGENT_CLI_TEST_KEY'}), encoding='utf-8')
    captured = {}
    class Adapter:
        def inspect(self, project, language): captured['inspected'] = True
    class Controller:
        runner = type('Runner', (), {'adapter': Adapter()})()
        async def run(self, request, context, fixed=None):
            return TaskResult('task', TaskState.DONE)
    def factory(request, configuration, **kwargs):
        captured.update(kwargs); captured['model'] = configuration.model
        return Controller()
    monkeypatch.setattr(cli, 'load_request', lambda path: request)
    monkeypatch.setattr(cli, 'create_controller', factory)
    monkeypatch.setenv('REPROAGENT_CLI_TEST_KEY', 'placeholder')
    code = cli.main(['run','--config',str(model_config),'--model-config',str(model_config), *argv])
    return code, captured


def test_cli_selects_no_backend_and_only_deprecates_the_old_flags(tmp_path, monkeypatch, capsys):
    code, captured = run_cli(tmp_path, monkeypatch, [])
    assert code == 0 and captured['inspected']
    # A default run chooses nothing: there is one infrastructure to build.
    assert captured['model_backend'] is None and captured['agent_backend'] is None
    assert 'deprecated' not in capsys.readouterr().err
    code, captured = run_cli(tmp_path, monkeypatch, ['--model-backend','native','--agent-backend','agentscope'])
    assert code == 0
    # The old flags are still accepted, forwarded as the deprecated aliases they now are.
    assert captured['model_backend'] == 'native' and captured['agent_backend'] == 'agentscope'
    assert 'deprecated' in capsys.readouterr().err


def test_cli_run_help_marks_the_backend_flags_deprecated(capsys):
    cli = importlib.import_module('reproagent.cli')
    with pytest.raises(SystemExit) as exit_code:
        cli.main(['run','--help'])
    assert exit_code.value.code == 0
    text = capsys.readouterr().out
    assert '--model-backend' in text and '--agent-backend' in text and 'deprecated' in text


def test_cli_rejects_an_unknown_backend_value(tmp_path, monkeypatch):
    cli = importlib.import_module('reproagent.cli')
    with pytest.raises(SystemExit) as exit_code:
        cli.main(['run','--config','x','--model-config','y','--model-backend','nonsense'])
    assert exit_code.value.code == 2
