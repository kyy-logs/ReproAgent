"""Candidate-owned setup errors can be corrected without accepting unrelated failures.

Model answers are scripted at the external HTTP boundary. This verifies SDK/Controller
routing and evidence preservation, not real-model classification accuracy.
"""
import asyncio
import json

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.models import EvidenceLevel, FixValidationRequest, ModelConfig, ModelRequest, TaskState
from tests.integration.test_agentscope_runtime import tool_reply, write_payload
from tests.unit.test_controller import BUGGY, ScriptedModel, setup


WRONG_OUTPUT = ("from example.parser import parse\n"
                "def test_output(tmp_path):\n"
                "    parse([1])\n"
                "    output = tmp_path / 'html'\n"
                "    output.mkdir()\n"
                "    (output / 'index.html').write_text('HTML built', encoding='utf-8')\n"
                "    (output / 'index.txt').read_text(encoding='utf-8')\n"
                "    assert parse([]) == []\n")


@pytest.mark.parametrize('classification', ['INVALID_CANDIDATE', 'ENVIRONMENT_BLOCKED'])
def test_setup_classification_controls_next_wire_and_completion(tmp_path, projects, facts, classification):
    request, _, _, ctx = setup(tmp_path, projects, facts)
    script = ScriptedModel()
    wires, reviews = [], []
    reason = ("Candidate produced html/index.html but read html/index.txt; "
              "FileNotFoundError occurred before the target assertion. "
              "Check the builder/output format and module reference.")

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            wires.append(payload)
            content = WRONG_OUTPUT if len(wires) == 1 else BUGGY
            return tool_reply('write_candidate', write_payload(content=content),
                              call_id='publish-' + str(len(wires)))
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        reply = await script.complete(ModelRequest(tuple(payload['messages']), kind), ctx)
        result = json.loads(reply.text)
        if kind == 'verdict':
            reviews.append(data)
            if len(reviews) == 1:
                # Same measured exception, different scripted semantic ownership.
                # The program must not auto-classify by exception name.
                assert 'FileNotFoundError' in json.dumps(data)
                result.update(classification=classification, reason=reason,
                              target_triggered=False, failure_matches_issue=False)
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion',
            'created': 1, 'model': 'offline', 'choices': [{'index': 0,
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(result)}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    fixed = FixValidationRequest(projects.fixed(tmp_path / 'fixed'))
    result = asyncio.run(controller.run(request, ctx, fixed))
    saved = [json.loads(p.read_text(encoding='utf-8'))
             for p in (request.output_dir / 'verdicts').glob('*.json')]
    initial = next(v for v in saved if v['classification'] == classification)
    assert initial['reason'] == reason
    assert set(initial['unmet_checks']) == {'target_triggered', 'failure_matches_issue'}
    assert initial['candidate_id'] != result.accepted_candidate_id
    if classification == 'ENVIRONMENT_BLOCKED':
        assert result.status == TaskState.BLOCKED
        assert result.stop_reason == 'ENVIRONMENT_BLOCKED'
        assert result.evidence_level == EvidenceLevel.NONE
        assert not result.accepted_candidate_id
        assert len(wires) == 1
        assert initial['next_action'] == 'stop'
        return
    assert len(wires) == 2
    feedbacks = []
    for message in wires[1]['messages']:
        if message['role'] != 'user' or not isinstance(message['content'], str):
            continue
        try:
            envelope = json.loads(message['content'])
        except ValueError:
            continue
        if envelope.get('feedback'):
            feedbacks.append(json.loads(envelope['feedback']))
    feedback = feedbacks[-1]
    assert feedback['decision'] == 'continue'
    assert feedback['verdict']['next_action'] == 'explore'
    assert reason == feedback['verdict']['reason']
    assert feedback['verdict']['candidate_id'] == initial['candidate_id']
    assert len(feedback['verdict']['run_ids']) == 1
    assert set(feedback['verdict']['unmet_checks']) == {'target_triggered', 'failure_matches_issue'}
    assert result.status == TaskState.DONE
    assert result.evidence_level == EvidenceLevel.DIFFERENTIAL_VALIDATED
    assert result.fix_validation_status == 'passed'
    executions = [json.loads(p.read_text(encoding='utf-8'))
                  for p in (request.output_dir / 'runs').glob('*/execution.json')]
    assert sum(e['execution_role'] == 'original' for e in executions) == 3
    assert sum(e['execution_role'] == 'fixed' for e in executions) == 1


@pytest.mark.parametrize('owner', ['target', 'candidate'])
def test_collection_import_diagnosis_cannot_override_execution_gate(tmp_path, projects, facts, owner):
    from tests.unit.test_controller import publish, ask
    text = BUGGY
    if owner == 'candidate':
        text = 'from example.parser import parse\nimport candidate_missing_dependency_123456\n' + BUGGY
    request, model, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(content=text), ask()])
    if owner == 'target':
        model.classification = 'ENVIRONMENT_BLOCKED'
        (request.repo / 'example/parser.py').write_text(
            'import target_missing_dependency_123456\ndef parse(values): return values\n', encoding='utf-8')
    result = asyncio.run(controller.run(request, ctx))
    executions = [controller.store.load_record('runs', p.parent.name)
                  for p in (request.output_dir / 'runs').glob('*/execution.json')]
    assert len(executions) == 1
    run = executions[0]
    failure = run.observation.framework_details['collection_failures'][0]
    assert 'ModuleNotFoundError' in failure['longrepr']
    assert result.evidence_level == EvidenceLevel.NONE and not result.accepted_candidate_id
    if owner == 'target':
        assert result.status == TaskState.BLOCKED and result.stop_reason == 'ENVIRONMENT_BLOCKED'
        assert len(controller.explorer.contexts) == 1
    else:
        assert result.status == TaskState.NEEDS_INFORMATION
        assert len(controller.explorer.contexts) == 2
    assert model.kinds.count('verdict') == 1


@pytest.mark.parametrize('scenario', ['dynamic_call', 'translated_error'])
def test_target_frame_alone_does_not_prove_missing_environment(tmp_path, projects, facts, scenario):
    from tests.unit.test_controller import publish, ask
    text = BUGGY
    if scenario == 'dynamic_call':
        text = "from example.parser import parse\nparse('candidate_selected_missing_module_123456')\n" + BUGGY
        source = 'def parse(name):\n    return __import__(name)\n'
    else:
        source = ("try:\n    import missing_optional_module_123456\n"
                  "except ModuleNotFoundError as exc:\n    raise ValueError('domain validation') from exc\n"
                  "def parse(values): return values\n")
    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[publish(content=text), ask()])
    (request.repo / 'example/parser.py').write_text(source, encoding='utf-8')
    result = asyncio.run(controller.run(request, ctx))
    run = controller.store.load_record('runs', next((request.output_dir / 'runs').iterdir()).name)
    failure = run.observation.framework_details['collection_failures'][0]
    assert ('ModuleNotFoundError' if scenario == 'dynamic_call' else 'ValueError') in failure['longrepr']
    assert result.status == TaskState.NEEDS_INFORMATION
    assert result.stop_reason == 'MISSING_INFORMATION'
    assert result.evidence_level == EvidenceLevel.NONE and not result.accepted_candidate_id
    assert len(controller.explorer.contexts) == 2
