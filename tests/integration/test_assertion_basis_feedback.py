"""Conflicting approval stays in raw trace, not in the next exploration feedback."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.models import CandidateClass, ModelConfig, ModelRequest, TaskState
from reproagent.observability import trace_session
from tests.integration.test_agentscope_runtime import tool_reply
from tests.integration.test_assertion_basis_gate import BasisModel
from tests.unit.test_controller import BUGGY, ScriptedModel, setup
from tests.unit.test_verifier import prepare


RAW_OPINION = 'RAW_BASIS_OPINION_8731：这是旧消息定位，作为定位检查可接受。'


@pytest.mark.parametrize('basis', ['target_old_behavior', 'unknown'])
def test_next_sdk_request_gets_program_rejection_but_raw_trace_preserves_model_opinion(
        tmp_path, projects, facts, basis):
    request, _, _, context = setup(tmp_path, projects, facts)
    request = replace(request, experience_file=None, learn_experience=False)
    script, wires, reviews, raw_replies = ScriptedModel(), [], [], []

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            if len(wires) == 1:
                return tool_reply('write_candidate', {'files': [{'path': 'tests/test_repro.py',
                    'role': 'test', 'content': BUGGY}], 'hypothesis': '验证目标输入'}, call_id='publish')
            return tool_reply('request_information', {'question': '保留本次拒绝供检查'}, call_id='stop')
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        reply = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        if kind == 'verdict':
            reviews.append(payload)
            value = json.loads(reply.text)
            value['reason'] = RAW_OPINION
            value['assertion_reviews'][0].update(basis=basis, supported=True, reason=RAW_OPINION)
            text = json.dumps(value, ensure_ascii=False)
            raw_replies.append(text)
        else:
            text = reply.text
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': text}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))

    async def run():
        with trace_session() as recorder:
            result = await controller.run(request, context)
            document = recorder.finish(task_id=result.task_id, status=result.status.value,
                                       main_duration=result.duration)
            return result, document

    result, document = asyncio.run(run())
    assert result.status == TaskState.NEEDS_INFORMATION
    assert len(wires) == context.budget.steps_used == 2 and len(reviews) == 1
    frames = []
    for message in wires[1]['messages']:
        if message['role'] != 'user' or not isinstance(message['content'], str):
            continue
        try:
            frame = json.loads(message['content'])
        except ValueError:
            continue
        if isinstance(frame, dict) and frame.get('feedback'):
            frames.append(frame)
    feedback = json.loads(frames[-1]['feedback'])
    assert feedback['verdict']['classification'] == 'INVALID_CANDIDATE'
    issue = feedback['verdict']['issues'][0]
    assert issue['code'] == 'CANDIDATE_ASSERTIONS_UNCONFIRMED'
    assert issue['origin'] == 'provider_claim'
    reason = feedback['verdict']['reason']
    assert 'basis=' + basis in reason and '冲突' in reason
    assert ('删除' in reason if basis == 'target_old_behavior' else '补充' in reason)
    assert '可接受' not in reason and 'RAW_BASIS_OPINION_8731' not in json.dumps(wires[1])
    assert 'RAW_BASIS_OPINION_8731' not in issue['message']
    run_id = feedback['verdict']['run_ids'][0]
    saved = controller.store.load_record('verdicts', 'verdict-' + run_id)
    assert saved.reason == reason
    assert saved.issues[0].evidence_refs and saved.candidate_id == feedback['verdict']['candidate_id']

    # The unchanged response is still diagnostic data, not rewritten to pretend
    # that the model originally said supported=false.
    captured = [json.loads(record['text']) for record in document['contents']
                if record['source'] == 'provider_response' and record.get('text')]
    assert any(record.get('content') == raw_replies[0] for record in captured)
    original = json.loads(raw_replies[0])['assertion_reviews'][0]
    assert original['supported'] is True and original['basis'] == basis and original['reason'] == RAW_OPINION

    supported_schema = json.loads(reviews[0]['messages'][-1]['content'])[
        'response_schema']['properties']['assertion_reviews']['items']['properties']['supported']
    assert 'after a correct fix' in supported_schema['description']
    assert 'after a correct fix' in reviews[0]['messages'][0]['content']


def test_explicit_negative_with_normative_basis_keeps_useful_model_diagnosis(tmp_path, projects, facts):
    detail = 'SOURCE_GAP_5831：来源要求返回错误对象，候选却要求抛出异常。'

    class SpecificNegative(BasisModel):
        async def complete(self, request, context):
            response = await super().complete(request, context)
            value = json.loads(response.text)
            value['assertion_reviews'][0]['reason'] = detail
            return replace(response, text=json.dumps(value, ensure_ascii=False))

    model = SpecificNegative('current_requirement', supported=False)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == CandidateClass.INVALID_CANDIDATE and len(model.calls) == 1
    assert detail in verdict.reason and '冲突' not in verdict.reason
