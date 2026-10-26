"""Missing lesson decisions cannot discard an already selected candidate."""
import asyncio
import json
import re

import pytest

from reproagent.adapters.agentscope import middleware as middleware_module
from reproagent.core.models import BudgetLimits
from tests.integration.test_agentscope_experience_drafts import draft_session_environment
from tests.integration.test_agentscope_runtime import (
    candidate_ids, environment as runtime_environment, events, explore, text_reply,
    tool_reply, write_payload,
)


def missing_candidate():
    return tool_reply('write_candidate', write_payload(), call_id='publish')


def failure():
    return tool_reply('Read', {'file_path': 'absent.py'}, call_id='bad-read')


def completed_none(_payload):
    return text_reply(json.dumps({'lesson_status': 'none', 'lesson_draft': None}))


def completed_draft(payload):
    notices = [json.loads(match) for message in payload['messages']
        for match in re.findall(r'<learning_source>(.*?)</learning_source>', str(message.get('content', '')))]
    return text_reply(json.dumps({'lesson_status': 'draft', 'lesson_draft': {
        'situation': 'when Read rejects a relative path in this runtime',
        'observation': 'Read refused absent.py because an absolute path is required',
        'lesson': 'Check the frozen source path before reading; a workaround is unverified',
        'source_ids': [notices[-1]['learning_source_id']],
    }}))


def environment(tmp_path, projects, facts, answers, **kwargs):
    requests, remaining = [], iter(answers)

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        answer = next(remaining)
        return answer(payload) if callable(answer) else answer

    env = draft_session_environment(tmp_path, projects, facts, [], handler=handle, **kwargs)
    env.wire = requests
    return env


def test_wire_requires_an_explicit_decision_but_missing_fields_keep_the_candidate(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, [missing_candidate()])
    result = explore(env)
    assert result.kind == 'candidate'
    assert len(candidate_ids(env)) == 1
    schema = next(t['function']['parameters'] for t in env.wire[0]['tools']
                  if t['function']['name'] == 'write_candidate')
    assert 'lesson_status' in schema['required']
    assert schema['properties']['lesson_status']['enum'] == ['draft', 'none']
    assert env.session.saw_none is False
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_no_source'
    assert len(env.wire) == 1  # No displayed failure source, so no completion request.


@pytest.mark.parametrize('completion,decision', [(completed_none, 'none'), (completed_draft, 'received')])
def test_one_completion_uses_the_exploration_context_and_preserves_candidate(tmp_path, projects, facts, completion, decision):
    env = environment(tmp_path, projects, facts, [failure(), missing_candidate(), completion])
    result = explore(env)
    assert result.kind == 'candidate'
    assert len(candidate_ids(env)) == 1
    assert len(env.wire) == 3
    assert env.context.budget.steps_used == 3
    assert env.wire[-1]['max_tokens'] == 768
    assert not env.wire[-1].get('tools')
    assert 'learning_source_id' in str(env.wire[-1]['messages'])
    assert [e['code'] for e in events(env, 'experience.draft')] == [decision]
    final = events(env, 'experience.lesson_completion')[-1]
    assert final['code'] == 'completed'
    assert final['call_id'] == 'publish'
    assert final['next_call_index'] == 3
    assert final['original_arguments_hash'] != final['effective_arguments_hash']
    # The original wire response omitted the decision; effective SDK history is auditable.
    from agentscope.message import ToolCallBlock
    call = next(b for m in env.runtime.messages for b in m.content
                if isinstance(b, ToolCallBlock) and b.id == 'publish')
    arguments = json.loads(call.input)
    assert arguments['files'] == write_payload()['files']
    assert arguments['hypothesis'] == write_payload()['hypothesis']
    if decision == 'received':
        from reproagent.learning_material import capture_exploration
        captured = capture_exploration(env.runtime.messages, (), draft=env.session.latest)
        assert 'draft_origin' in captured
        assert not captured['capture_issues']
    from agentscope.message import ToolCallBlock, ToolResultBlock
    calls = [block.id for message in env.runtime.messages for block in message.content
             if isinstance(block, ToolCallBlock)]
    results = [block.id for message in env.runtime.messages for block in message.content
               if isinstance(block, ToolResultBlock)]
    assert set(calls) == set(results)


@pytest.mark.parametrize('answer', [
    'not JSON',
    json.dumps({'lesson_status': 'none', 'lesson_draft': None, 'files': []}),
    json.dumps({'lesson_status': 'draft', 'lesson_draft': {'situation': 'x', 'observation': 'x',
        'lesson': 'x', 'source_ids': ['L999999']}}),
])
def test_invalid_completion_keeps_omitted_and_never_mutates_the_candidate(tmp_path, projects, facts, answer):
    env = environment(tmp_path, projects, facts, [failure(), missing_candidate(), text_reply(answer)])
    result = explore(env)
    assert result.kind == 'candidate'
    assert len(candidate_ids(env)) == 1
    assert len(env.wire) == 3
    assert env.session.latest is None and env.session.saw_none is False
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'invalid'


def test_step_reserve_skips_completion_and_still_publishes(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, [failure(), missing_candidate()],
                      limits=BudgetLimits(agent_steps=2))
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 2
    assert env.context.budget.steps_used == 2
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_steps'
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']


def test_invalid_completion_is_not_retried_in_the_next_phase(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts,
        [failure(), missing_candidate(), text_reply('bad JSON'), missing_candidate()])
    assert explore(env).kind == 'candidate'
    assert explore(env).kind == 'candidate'
    assert len(candidate_ids(env)) == 2
    assert len(env.wire) == 4
    assert [e['code'] for e in events(env, 'experience.lesson_completion')].count('started') == 1
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_used'


def test_time_margin_skips_completion_and_still_publishes(tmp_path, projects, facts):
    state = {}

    def candidate(_payload):
        state['env'].context.budget.deadline = state['env'].context.budget.clock() + 5
        return missing_candidate()

    env = environment(tmp_path, projects, facts, [failure(), candidate])
    state['env'] = env
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 2
    assert len(candidate_ids(env)) == 1
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_time'
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']


def test_capacity_skips_completion_and_still_publishes(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, [failure(), missing_candidate()])

    async def count_tokens(messages, tools=None, **kwargs):
        text = ''.join(str(getattr(message, 'content', '')) for message in messages)
        return 999999 if 'One write_candidate call omitted' in text else 1

    env.model.count_tokens = count_tokens
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 2
    assert len(candidate_ids(env)) == 1
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_capacity'
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']


def test_completion_timeout_preserves_the_candidate_and_omits(tmp_path, projects, facts, monkeypatch):
    seen = []

    async def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if len(seen) == 1:
            return failure()
        if len(seen) == 2:
            return missing_candidate()
        await asyncio.sleep(1)
        return completed_none(payload)

    env = draft_session_environment(tmp_path, projects, facts, [], handler=handler)
    env.wire = seen
    monkeypatch.setattr(middleware_module, 'COMPLETION_SECONDS', 0.05)
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 3
    assert len(candidate_ids(env)) == 1
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'timeout'
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']


def test_cancellation_during_completion_is_not_swallowed(tmp_path, projects, facts):
    from reproagent.core.budget import BudgetStopped
    state = {}

    async def handler(request):
        payload = json.loads(request.content)
        if len(state.setdefault('seen', [])) == 0:
            state['seen'].append(payload)
            return failure()
        if len(state['seen']) == 1:
            state['seen'].append(payload)
            state['env'].context.cancel_event.set()
            return missing_candidate()
        state['seen'].append(payload)
        return completed_none(payload)

    env = draft_session_environment(tmp_path, projects, facts, [], handler=handler)
    state['env'] = env
    with pytest.raises(BudgetStopped) as error:
        explore(env)
    assert error.value.reason == 'CANCELLED'
    # Cancellation reached the candidate model call before on_acting; the important
    # invariant is that it was not swallowed and no candidate was published.
    assert candidate_ids(env) == []


def test_completion_event_write_failure_cannot_drop_the_candidate(tmp_path, projects, facts, monkeypatch):
    env = environment(tmp_path, projects, facts, [failure(), missing_candidate(), completed_none])
    append = env.store.append_event

    def fail_completion(kind, refs, payload):
        if kind == 'experience.lesson_completion':
            raise OSError('offline event failure')
        return append(kind, refs, payload)

    monkeypatch.setattr(env.store, 'append_event', fail_completion)
    assert explore(env).kind == 'candidate'
    assert len(candidate_ids(env)) == 1
    assert env.session.saw_none is True
    assert [e['code'] for e in events(env, 'experience.draft')] == ['none']


def test_explicit_none_and_existing_draft_do_not_request_completion(tmp_path, projects, facts):
    from tests.integration.test_agentscope_experience_drafts import write_decision
    env = environment(tmp_path, projects, facts, [failure(), write_decision('none')])
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 2
    assert env.session.saw_none is True
    assert not events(env, 'experience.lesson_completion')

    draft = {
        'situation': 'when a read fails', 'observation': 'Read failed',
        'lesson': 'check the path', 'source_ids': ['L7'],
    }

    def candidate_with_draft(payload):
        notices = [json.loads(match) for message in payload['messages']
                   for match in re.findall(r'<learning_source>(.*?)</learning_source>',
                                           str(message.get('content', '')))]
        return tool_reply('write_candidate', {**write_payload(), 'lesson_status': 'draft',
            'lesson_draft': {**draft, 'source_ids': [notices[-1]['learning_source_id']]}},
            call_id='existing-draft')

    env2 = environment(tmp_path / 'draft', projects, facts, [failure(), candidate_with_draft])
    assert explore(env2).kind == 'candidate'
    assert len(env2.wire) == 2
    assert env2.session.latest is not None
    assert not events(env2, 'experience.lesson_completion')
    assert [e['code'] for e in events(env2, 'experience.draft')] == ['received']


def test_legacy_mode_has_no_completion_hook(tmp_path, projects, facts):
    env = runtime_environment(tmp_path, projects, facts, answers=[failure(), missing_candidate()])
    assert explore(env).kind == 'candidate'
    assert len(env.requests) == 2
    assert not events(env, 'experience.lesson_completion')
