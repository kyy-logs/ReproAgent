"""Optional completion keeps the real context and cannot consume publication reserves."""
import json

import pytest

from reproagent.core.budget import BudgetStopped
from tests.integration.test_lesson_decision_completion import (
    completed_none, environment, failure, missing_candidate,
)
from tests.integration.test_agentscope_runtime import candidate_ids, events, explore


def test_completion_inherits_current_feedback_without_pending_tool_call(tmp_path, projects, facts):
    marker = 'CURRENT_EXECUTION_FEEDBACK_FOR_LESSON'
    env = environment(tmp_path, projects, facts,
                      [failure(), missing_candidate(), completed_none])
    assert explore(env, feedback=json.dumps({
        'decision': 'continue', 'code': 'INVALID_CANDIDATE', 'diagnostic': marker,
    })).kind == 'candidate'
    assert len(env.wire) == 3
    completion = env.wire[-1]['messages']
    assert marker in json.dumps(completion)
    calls, results = [], []
    for message in completion:
        calls.extend(call['id'] for call in message.get('tool_calls', ()))
        if message['role'] == 'tool':
            results.append(message['tool_call_id'])
    assert calls == results == ['bad-read']
    assert 'publish' not in calls
    assert len(candidate_ids(env)) == 1


def test_completion_does_not_replay_the_one_shot_decision_reminder(tmp_path, projects, facts):
    from reproagent.adapters.agentscope.middleware import LESSON_DECISION_TEXT
    env = environment(tmp_path, projects, facts,
                      [failure(), missing_candidate(), completed_none])
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 3
    assert sum(LESSON_DECISION_TEXT in str(request['messages']) for request in env.wire) == 1


@pytest.mark.parametrize('error', [OSError, ValueError])
def test_unavailable_source_registry_skips_completion_not_candidate(tmp_path, projects, facts, monkeypatch, error):
    env = environment(tmp_path, projects, facts,
                      [failure(), missing_candidate(), completed_none])
    source_lookup = env.session.sources.sources
    reads = [0]

    def unavailable_once():
        reads[0] += 1
        if reads[0] == 1:
            raise error('offline source lookup unavailable')
        return source_lookup()

    monkeypatch.setattr(env.session.sources, 'sources', unavailable_once)
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 2
    assert len(candidate_ids(env)) == 1
    assert [e['code'] for e in events(env, 'experience.draft')] == ['omitted']
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_no_source'


def test_time_reserve_is_rechecked_after_token_estimation(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts,
                      [failure(), missing_candidate(), completed_none])
    budget = env.context.budget
    now = [budget.clock()]
    budget.clock = lambda: now[0]
    budget.deadline = now[0] + 15.5

    async def count_tokens(messages, tools=None, **_kwargs):
        if 'One write_candidate call omitted' in str([m.content for m in messages]):
            now[0] += 11
        return 1

    env.model.count_tokens = count_tokens
    assert explore(env).kind == 'candidate'
    assert len(env.wire) == 2
    assert budget.steps_used == 2
    assert len(candidate_ids(env)) == 1
    assert events(env, 'experience.lesson_completion')[-1]['code'] == 'skipped_time'
    assert not any(e['code'] == 'started' for e in events(env, 'experience.lesson_completion'))


def test_cancellation_after_token_estimation_stops_before_completion(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts,
                      [failure(), missing_candidate(), completed_none])

    async def count_tokens(messages, tools=None, **_kwargs):
        if 'One write_candidate call omitted' in str([m.content for m in messages]):
            env.context.cancel_event.set()
        return 1

    env.model.count_tokens = count_tokens
    with pytest.raises(BudgetStopped) as stopped:
        explore(env)
    assert stopped.value.reason == 'CANCELLED'
    assert len(env.wire) == 2
    assert not any(e['code'] == 'started' for e in events(env, 'experience.lesson_completion'))
