"""Exercise SDK history capture through the controller's real learning hook."""
import asyncio
import json
import re
from dataclasses import replace

import httpx

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest
from reproagent.experience import load_experience_snapshot
from tests.integration.test_agentscope_backends import CANDIDATE, tool_response, text_response
from tests.unit.test_controller import setup, ScriptedModel


def test_sdk_same_message_error_becomes_a_reviewed_stored_card(tmp_path, projects, facts, monkeypatch):
    monkeypatch.setenv('EXPERIENCE_REVIEW_FAKE_KEY', 'offline-fake-value')
    request, _, _, context = setup(tmp_path, projects, facts)
    request = replace(request, experience_file=tmp_path / 'library', experience_mode='draft_review')
    script, reviews, prompts, actions = ScriptedModel(), [], [], []

    async def handle(req):
        payload = json.loads(req.content)
        if payload.get('tools'):
            actions.append(payload)
            if len(actions) == 1:
                return tool_response('Read', {'file_path': 'absent.py'}, 'bad-read')
            notices = [json.loads(match) for message in payload['messages'] if message.get('role') == 'tool'
                for match in re.findall(r'<learning_source>(.*?)</learning_source>', str(message.get('content', '')))]
            source_id = notices[-1]['learning_source_id'] if notices else 'missing-visible-source'
            return tool_response('write_candidate', {
                'files': [{'path': 'tests/test_repro.py', 'content': CANDIDATE, 'role': 'test'}],
                'hypothesis': 'empty input',
                'lesson_status': 'draft',
                'lesson_draft': {'situation': 'when reading an absent path',
                    'observation': 'Read failed for absent.py',
                    'lesson': 'check the path before repeating the read', 'source_ids': [source_id]}
            }, 'draft-call')
        data = json.loads(payload['messages'][-1]['content'])
        if 'evidence' in data and 'task_status' in data:
            reviews.append(data)
            prompts.append(payload['messages'][0]['content'])
            return text_response(json.dumps({'decision': 'accept', 'reason_code': 'SUPPORTED',
                'experience': {'category': 'workflow', 'tags': ['read'], 'name': 'Check path',
                    'description': 'when Read reports an absent path', 'summary': 'Read failed',
                    'detail': 'check the path', 'evidence_ids': data['draft_evidence_ids'][:1]}}))
        kind = 'verdict' if 'available_refs' in data else 'contract'
        response = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        return text_response(response.text)

    model = ModelConfig(base_url='https://offline.example/v1', model='offline',
                        api_key_env='EXPERIENCE_REVIEW_FAKE_KEY', output_limit_field='max_tokens')
    controller = create_controller(request, model, transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status.value == 'DONE'
    assert controller.learning_result.code == 'written'
    assert len(reviews) == 1
    assert 'name <=64 characters' in prompts[0]
    assert 'description <=160 characters' in prompts[0]
    assert 'Prefer name <=32 characters' in prompts[0]
    assert 'description 80-120 characters' in prompts[0]
    assert reviews[0]['draft']['source_ids'][0].startswith('L')
    assert load_experience_snapshot(request.experience_file).cards[0].category == 'workflow'
    captured = controller.experience_service.exploration
    source = next(e for e in captured['tool_errors'] if e['tool_call_id'] == 'bad-read')
    assert source['message_offset'] == captured['draft_origin']['message_offset']
    assert source['block_index'] < captured['draft_origin']['block_index']


def test_sdk_review_receives_unresolved_verdict_with_controlled_protocol_reason(tmp_path, projects, facts, monkeypatch):
    monkeypatch.setenv('EXPERIENCE_REVIEW_FAKE_KEY', 'offline-fake-value')
    request, _, _, context = setup(tmp_path, projects, facts)
    request = replace(request, experience_file=tmp_path / 'library', experience_mode='draft_review')
    script, reviews, actions = ScriptedModel(), [], []

    async def handle(req):
        payload = json.loads(req.content)
        if payload.get('tools'):
            actions.append(payload)
            if len(actions) == 1:
                return tool_response('Read', {'file_path': 'absent.py'}, 'bad-read')
            if len(actions) == 2:
                notices = [json.loads(match) for message in payload['messages'] if message.get('role') == 'tool'
                    for match in re.findall(r'<learning_source>(.*?)</learning_source>', str(message.get('content', '')))]
                return tool_response('write_candidate', {
                    'files': [{'path': 'tests/test_repro.py', 'content': CANDIDATE, 'role': 'test'}],
                    'hypothesis': 'empty input', 'lesson_status': 'draft',
                    'lesson_draft': {'situation': 'when a read fails', 'observation': 'Read failed',
                        'lesson': 'check the path', 'source_ids': [notices[-1]['learning_source_id']]}
                }, 'draft-call')
            return tool_response('request_information', {'question': 'Need independent evidence'}, 'stop')
        data = json.loads(payload['messages'][-1]['content'])
        if 'draft_evidence_ids' in data:
            reviews.append(data)
            return text_response(json.dumps({'decision': 'reject', 'reason_code': 'UNSUPPORTED', 'experience': None}))
        kind = 'verdict' if 'available_refs' in data else 'contract'
        response = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        if kind == 'verdict':
            invalid = json.loads(response.text)
            invalid.update(expected_assertion=False, extra_field='PRIVATE_REJECTED_PROVIDER_TEXT')
            return text_response(json.dumps(invalid))
        return text_response(response.text)

    model = ModelConfig(base_url='https://offline.example/v1', model='offline',
                        api_key_env='EXPERIENCE_REVIEW_FAKE_KEY', output_limit_field='max_tokens')
    controller = create_controller(request, model, transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))
    assert result.status.value == 'NEEDS_INFORMATION'
    assert controller.learning_result.code == 'review_rejected'
    assert len(reviews) == 1
    assert reviews[0]['attempts'][0]['classification'] is None
    attempt = json.loads(next(e['text'] for e in reviews[0]['evidence'] if e['kind'] == 'attempt'))
    assert attempt['confirmed'] is False
    assert attempt['verdict_diagnostics'] == [
        {'code': 'MODEL_PROTOCOL_ERROR', 'origin': 'program_check',
         'protocol_error': 'invalid_negative_response'}]
    assert 'PRIVATE_REJECTED_PROVIDER_TEXT' not in json.dumps(reviews)


def test_completion_draft_enters_review_and_writes_readable_index(tmp_path, projects, facts, monkeypatch):
    monkeypatch.setenv('EXPERIENCE_REVIEW_FAKE_KEY', 'offline-fake-value')
    request, _, _, context = setup(tmp_path, projects, facts)
    library = tmp_path / 'completion-library'
    request = replace(request, experience_file=library, experience_mode='draft_review')
    script, actions, reviews = ScriptedModel(), [], []

    async def handle(req):
        payload = json.loads(req.content)
        if payload.get('tools'):
            actions.append(payload)
            if len(actions) == 1:
                return tool_response('Read', {'file_path': 'absent.py'}, 'bad-read')
            return tool_response('write_candidate', {
                'files': [{'path': 'tests/test_repro.py', 'content': CANDIDATE, 'role': 'test'}],
                'hypothesis': 'empty input',
            }, 'publish')
        data = json.loads(payload['messages'][-1]['content'])
        if 'write_candidate' in data and 'learning_sources' in data:
            notices = [json.loads(match) for message in payload['messages']
                       for match in re.findall(r'<learning_source>(.*?)</learning_source>',
                                               str(message.get('content', '')))]
            source_id = notices[-1]['learning_source_id']
            return text_response(json.dumps({
                'lesson_status': 'draft',
                'lesson_draft': {'situation': 'when Read rejects a relative path',
                    'observation': 'Read failed for absent.py',
                    'lesson': 'check the frozen source path before reading',
                    'source_ids': [source_id]}}))
        if 'draft_evidence_ids' in data:
            reviews.append(data)
            return text_response(json.dumps({
                'decision': 'accept', 'reason_code': 'SUPPORTED',
                'experience': {'category': 'workflow', 'tags': ['read'],
                    'name': 'Check the frozen path',
                    'description': 'when Read rejects a relative path before the target runs',
                    'summary': 'Read failed for an absent relative path',
                    'detail': 'check the frozen source path before repeating the read',
                    'evidence_ids': data['draft_evidence_ids'][:1]}}))
        kind = 'verdict' if 'available_refs' in data else 'contract'
        response = await script.complete(ModelRequest(tuple(payload['messages']), kind), context)
        return text_response(response.text)

    model = ModelConfig(base_url='https://offline.example/v1', model='offline',
                        api_key_env='EXPERIENCE_REVIEW_FAKE_KEY', output_limit_field='max_tokens')
    controller = create_controller(request, model, transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context))

    assert result.status.value == 'DONE'
    assert controller.learning_result.code == 'written'
    assert len(actions) == 2 and len(reviews) == 1
    recorded, errors = controller.store.read_events()
    assert not errors
    assert any(event.kind == 'experience.lesson_completion'
               and event.payload['code'] == 'completed' for event in recorded)
    snapshot = load_experience_snapshot(library)
    assert snapshot.state == 'ready' and snapshot.cards
    from reproagent.experience_catalog import ExperienceCatalogView
    view = ExperienceCatalogView(snapshot)
    page = view.list('workflow')
    assert page['code'] == 'ready' and page['items']
    card_id = page['items'][0]['id']
    assert view.read(card_id)['name'] == 'Check the frozen path'
