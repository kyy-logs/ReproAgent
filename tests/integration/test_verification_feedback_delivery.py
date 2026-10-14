import asyncio
import json
from dataclasses import replace
import httpx
import pytest
from reproagent.app import create_controller
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import BudgetLimits, ModelConfig, ModelRequest, TaskState, IssueDescription
from reproagent.observability import TraceRecorder, write_trace
from tests.unit.test_controller import setup, ScriptedModel
from tests.integration.test_agentscope_backends import CANDIDATE
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply


def test_real_controller_verdict_reaches_next_sdk_wire_and_local_trace(tmp_path, projects, facts):
    request, _, _, ctx = setup(tmp_path, projects, facts)
    script = ScriptedModel(classification='NOT_REPRODUCED')
    wires = []
    domains = []

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            if len(wires) == 1:
                return tool_reply('write_candidate', {'files': [
                    {'path': 'tests/test_repro.py', 'content': CANDIDATE, 'role': 'test'}],
                    'hypothesis': 'empty input'}, call_id='publish')
            return tool_reply('request_information', {'question': '需要进一步确认目标触发'}, call_id='ask')
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        domains.append((kind, payload))
        reply = await script.complete(ModelRequest(tuple(payload['messages']), kind), ctx)
        return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion', 'created': 1,
            'model': 'offline', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': reply.text}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    with TraceRecorder() as recorder:
        result = asyncio.run(controller.run(request, ctx))
        trace = recorder.finish(task_id=result.task_id, status=result.status.value, main_duration=result.duration)
    assert result.status == TaskState.NEEDS_INFORMATION and len(wires) == 2
    contexts = []
    for message in wires[1]['messages']:
        if message['role'] != 'user' or not isinstance(message['content'], str):
            continue
        try:
            data = json.loads(message['content'])
        except ValueError:
            continue
        if 'feedback' in data:
            contexts.append(data)
    feedback = json.loads(contexts[-1]['feedback'])
    run_id = feedback['verdict']['run_ids'][0]
    saved = controller.store.load_record('verdicts', 'verdict-' + run_id)
    assert feedback['verdict']['candidate_id'] == saved.candidate_id
    assert feedback['verdict']['issues'][0]['code'] == saved.issues[0].code == 'TARGET_TRIGGER_UNCONFIRMED'
    assert feedback['verdict']['issues'][0]['origin'] == 'provider_claim'
    assert feedback['verdict']['issues'][0]['evidence_refs']
    assert feedback['verdict']['unmet_checks'] == ['target_triggered']
    assert feedback['decision'] == 'continue' and feedback['instruction']
    assert feedback['projection']['omitted_issues'] == 0
    verdict_request = next(data for kind, data in domains if kind == 'verdict')
    assert [m['role'] for m in verdict_request['messages']] == ['system', 'user']
    # Use actual product writer and read it back, not just an in-memory mark call.
    path = write_trace(request.output_dir, trace)
    exported = json.loads(path.read_text(encoding='utf-8'))
    points = [s for s in exported['spans'] if s['name'] == 'verification.feedback']
    assert points and points[0]['attributes']['diagnostic_codes'] == ['TARGET_TRIGGER_UNCONFIRMED']
    assert points[0]['attributes']['decision'] == 'continue'
    issues = [s for s in exported['spans'] if s['name'] == 'verification.issue']
    assert issues[0]['attributes']['check_source'] == 'provider_claim'
    report = json.loads((request.output_dir / 'artifacts/diagnostic/report.json').read_text(encoding='utf-8'))
    assert report['runs'][0]['verification_issues'][0]['code'] == 'TARGET_TRIGGER_UNCONFIRMED'
    text = (request.output_dir / 'artifacts/diagnostic/report.md').read_text(encoding='utf-8')
    assert 'TARGET_TRIGGER_UNCONFIRMED' in text and '模型审阅意见' in text


def test_outer_phase_capacity_refuses_without_dropping_issue_contract_or_sending_request(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(tool_response_bytes=1024))
    phase = agent_context(env, feedback=json.dumps({'reason': '事实' * 900}, ensure_ascii=False))
    issue_text = 'original issue unchanged'
    from reproagent.core.serialization import bytes_hash
    phase = replace(phase, issue=IssueDescription(issue_text, bytes_hash(issue_text.encode())))

    async def run():
        try:
            with pytest.raises(BudgetStopped) as caught:
                await env.runtime.explore(phase)
            assert caught.value.reason == 'NEEDS_INFORMATION'
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()

    asyncio.run(run())
    assert env.requests == [] and phase.issue.text == issue_text


def test_report_diagnostics_escape_untrusted_text_and_legacy_fallback():
    from reproagent.reporting import render_report
    from tests.unit.test_reporting import report_data
    data = report_data()
    data['runs'][1]['verification_issues'] = [{'code': 'TARGET_TRIGGER_UNCONFIRMED',
        'check': 'target_triggered', 'origin': 'provider_claim', 'message': '<script>bad</script> [click](https://bad)',
        'recovery': 'inspect_evidence', 'evidence_refs': [], 'measurements': {}}]
    text = render_report(data)
    assert 'TARGET_TRIGGER_UNCONFIRMED' in text and '模型审阅意见' in text
    assert '<script>' not in text and '[click](https://bad)' not in text
    old = render_report(report_data())
    assert 'TARGET_TRIGGER_UNCONFIRMED' not in old
