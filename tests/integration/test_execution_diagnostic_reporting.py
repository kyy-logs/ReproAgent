import asyncio
import json

from reproagent.observability import trace_session
from tests.unit.test_controller import setup, publish
from tests.unit.test_controller_execution_recovery import fault_sequence


def test_repeated_timeout_exports_diagnostics_and_trace(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts,
        plan=[publish(), publish(hypothesis='new candidate'), publish()])
    fault_sequence(controller, ['COMMAND_TIMEOUT', 'COMMAND_TIMEOUT'])
    with trace_session() as recorder:
        result = asyncio.run(controller.run(request, ctx))
        trace = recorder.finish(task_id=result.task_id, status=result.status.value, main_duration=result.duration)
    package = request.output_dir / 'artifacts/diagnostic'
    report = json.loads((package / 'report.json').read_text(encoding='utf-8'))
    assert len(report['runs']) == 2
    for run in report['runs']:
        assert run['execution_diagnostic']['code'] == 'COMMAND_TIMEOUT'
        assert run['execution_diagnostic']['protection_checked'] is False
        assert 'execution_exited' in run['unmet_checks']
    text = (package / 'report.md').read_text(encoding='utf-8')
    assert 'setup:passed' in text and '保护检查未执行' in text
    points = [s for s in trace['spans'] if s['name'] == 'execution.diagnosed']
    assert [s['attributes']['budget']['consecutive_command_timeouts'] for s in points] == [1, 2]
    assert points[-1]['attributes']['result_code'] == 'REPEATED_COMMAND_TIMEOUT'
    assert points[-1]['attributes']['run_id']
