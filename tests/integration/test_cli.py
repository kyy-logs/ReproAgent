import importlib
import json
from reproagent.core.models import TaskResult, TaskState
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
