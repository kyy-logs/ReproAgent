import json
from dataclasses import asdict

from reproagent.core.models import TaskState
from reproagent.observability import trace_session
from tests.integration.test_experience_lifecycle import run_sdk_task


def test_size_diagnostics_propagate_to_cli_summary_events_and_trace(tmp_path, projects, facts):
    with trace_session() as recorder:
        _, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning='oversize')
        document = recorder.finish(task_id=result.task_id, status=result.status.value,
            main_duration=result.duration, learning=asdict(controller.learning_result))
    assert result.status == TaskState.DONE and len(calls) == 1
    learning = controller.learning_result
    assert learning.code == 'invalid_card_size'
    assert learning.needed_bytes > 4096 and learning.limit_bytes == 4096
    assert learning.exceeded_bytes == learning.needed_bytes - 4096
    events, _ = controller.store.read_events()
    event = next(e.payload for e in events if e.kind == 'experience.learning')
    assert event['needed_bytes'] == learning.needed_bytes
    assert event['limit_bytes'] == 4096
    assert event['exceeded_bytes'] == learning.exceeded_bytes
    assert document['summary']['learning'] == asdict(learning)
    span = next(s for s in document['spans'] if s['name'] == 'learn')
    assert span['attributes']['budget'] == {key:event[key] for key in ('needed_bytes','limit_bytes','exceeded_bytes')}
    assert len(controller.experience_service.snapshot.cards) == 1


def test_non_size_failure_does_not_fabricate_size_numbers(tmp_path, projects, facts):
    _, controller, result, _, _, _ = run_sdk_task(tmp_path, projects, facts, learning='missing_evidence')
    assert result.status == TaskState.DONE
    learning = controller.learning_result
    assert learning.code == 'invalid_missing_fields'
    assert learning.needed_bytes is None and learning.limit_bytes is None and learning.exceeded_bytes is None


def test_cli_prints_numeric_size_diagnostics(tmp_path, projects, monkeypatch, capsys):
    from types import SimpleNamespace
    from reproagent import cli
    from reproagent.core.models import TaskResult
    from reproagent.experience import LearningResult
    repo = projects.plain(tmp_path / 'repo')
    config = tmp_path / 'task.json'
    config.write_text(json.dumps({'schema_version':1, 'repo':str(repo), 'issue_file':str(repo / 'issue.md'),
        'output_dir':str(tmp_path / 'task')}))
    model = tmp_path / 'model.json'
    model.write_text('{"base_url":"https://offline.example/v1","model":"offline"}')
    class Controller:
        learning_result = LearningResult('invalid_card_size', needed_bytes=4300, limit_bytes=4096, exceeded_bytes=204)
        runner = SimpleNamespace(adapter=SimpleNamespace(inspect=lambda *args:None))
        async def run(self, *args): return TaskResult('task', TaskState.DONE, duration=1)
    monkeypatch.setattr(cli, 'create_controller', lambda *args, **kwargs:Controller())
    monkeypatch.setenv('REPROAGENT_API_KEY', 'offline-test')
    assert cli.main(['run','--config',str(config),'--model-config',str(model)]) == 0
    learning = json.loads(capsys.readouterr().out)['learning']
    assert (learning['needed_bytes'], learning['limit_bytes'], learning['exceeded_bytes']) == (4300, 4096, 204)
