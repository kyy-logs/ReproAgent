import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reproagent.adapters.agentscope.experience_hint import HINT_TEXT
from reproagent.adapters.agentscope.middleware import ExplorationMiddleware, LESSON_DECISION_TEXT
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.adapters.agentscope.tools import build_toolkit
from reproagent.experience_drafts import ExperienceDraftSession
from reproagent.experience import ExperienceSnapshot
from reproagent.experience_catalog import ExperienceCatalogView
from tests.unit.test_experience_store import card
from tests.integration.test_agentscope_runtime import (
    SYSTEM_PROMPT,
    environment as runtime_environment,
    explore,
    tool_reply,
    write_payload,
)


def hint_environment(tmp_path, projects, facts):
    env = runtime_environment(
        tmp_path, projects, facts,
        answers=[tool_reply("Read", {"file_path": "absent.py"}, call_id="bad-read"),
                 tool_reply("write_candidate", write_payload(), call_id="publish")])
    session = ExperienceDraftSession(env.store)
    view = ExperienceCatalogView(ExperienceSnapshot('ready', 'hash', (card(),)))
    env.toolkit = build_toolkit(
        env.backend, env.ledger, env.service, env.gate, env.context,
        experience_view=view, experience_mode="draft_review", draft_session=session)
    env.runtime = AgentScopeRuntime(
        env.model, env.toolkit, env.gate, env.context, system_prompt=SYSTEM_PROMPT,
        store=env.store, experience_view=view, draft_session=session)
    env.runtime.browse_gate.satisfy()
    return env


def system_text(request):
    return "\n".join(message["content"] for message in request["messages"]
                     if message["role"] == "system")


def test_hint_is_visible_once_after_a_real_tool_failure(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    result = explore(env)
    assert result.kind == "candidate"
    assert HINT_TEXT not in system_text(env.requests[0])
    assert HINT_TEXT in system_text(env.requests[1])
    hint_state = env.runtime.middleware.hint_state
    assert hint_state.delivered
    assert not hint_state.omitted
    assert sum(HINT_TEXT in system_text(request) for request in env.requests) == 1


def test_capacity_failure_drops_only_the_hint(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.model.context_size = 200

    def text_of(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(text_of(item) for item in value)
        return str(getattr(value, "text", ""))

    async def count_tokens(*args, **kwargs):
        messages = kwargs.get("messages", args[0] if args else [])
        joined = "\n".join(text_of(message.get("content", "") if isinstance(message, dict)
                                   else getattr(message, "content", "")) for message in messages)
        return 300 if HINT_TEXT in joined else 150

    env.runtime._agent.model.count_tokens = count_tokens
    result = explore(env)
    assert result.kind == "candidate"
    assert all(HINT_TEXT not in system_text(request) for request in env.requests)
    assert env.runtime.middleware.hint_state.omitted
    assert not env.runtime.middleware.hint_state.delivered


def test_disabled_hint_never_reaches_the_wire(tmp_path, projects, facts):
    env = runtime_environment(
        tmp_path, projects, facts,
        answers=[tool_reply("Read", {"file_path": "absent.py"}, call_id="bad-read"),
                 tool_reply("write_candidate", write_payload(), call_id="publish")])
    result = explore(env)
    assert result.kind == "candidate"
    assert all(HINT_TEXT not in system_text(request) for request in env.requests)


@pytest.mark.parametrize(('steps', 'expected'), [(6, False), (7, True)])
def test_hint_uses_actual_pre_call_budget(tmp_path, projects, facts, steps, expected):
    env = hint_environment(tmp_path, projects, facts)
    env.context.budget.limits = replace(env.context.budget.limits, agent_steps=steps)
    assert explore(env).kind == 'candidate'
    assert (HINT_TEXT in system_text(env.requests[1])) is expected
    recorded, errors = env.store.read_events()
    assert not errors
    hints = [e.payload for e in recorded if e.kind == 'experience.hint']
    assert len(hints) == 1
    assert hints[0]['code'] == ('delivered' if expected else 'insufficient_steps')
    assert hints[0]['trigger']['tool'] == 'Read'
    assert hints[0]['trigger']['call_id'] == 'bad-read'
    assert hints[0]['next_call_index'] == 2


def test_hint_allows_catalog_and_detail_before_publishing_reserve(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.context.budget.limits = replace(env.context.budget.limits, agent_steps=7)
    entry = card()
    env.answers[1:] = [
        tool_reply('list_experiences', {'category': entry.category}, call_id='lookup'),
        tool_reply('read_experience', {'id': entry.id}, call_id='detail'),
        tool_reply('write_candidate', write_payload(), call_id='publish')]
    assert explore(env).kind == 'candidate'
    assert HINT_TEXT in system_text(env.requests[1])
    assert env.runtime.experience_view.read_ids == (entry.id,)


def test_hint_delivery_and_capacity_omission_are_observed(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.model.context_size = 200
    async def count_tokens(*args, **kwargs):
        messages = kwargs.get('messages', [])
        return 300 if any(HINT_TEXT in str(getattr(m, 'content', '')) for m in messages) else 150
    env.runtime._agent.model.count_tokens = count_tokens
    assert explore(env).kind == 'candidate'
    recorded, errors = env.store.read_events()
    assert not errors
    hints = [e.payload for e in recorded if e.kind == 'experience.hint']
    assert len(hints) == 1 and hints[0]['code'] == 'context_capacity'
    assert all(HINT_TEXT not in system_text(r) for r in env.requests)


@pytest.mark.parametrize('tool', ['list_experiences', 'read_experience'])
@pytest.mark.parametrize('behavior', ['deny', 'ask'])
def test_hint_does_not_invite_a_reader_with_an_sdk_restriction(tmp_path, projects, facts, tool, behavior):
    from agentscope.permission import PermissionRule, PermissionBehavior
    env = hint_environment(tmp_path, projects, facts)
    context = env.runtime._agent.state.permission_context
    getattr(context, f'{behavior}_rules')[tool] = [PermissionRule(
        tool_name=tool, rule_content=None, behavior=PermissionBehavior(behavior), source='test')]
    assert explore(env).kind == 'candidate'
    assert all(HINT_TEXT not in system_text(r) for r in env.requests)
    recorded, errors = env.store.read_events()
    assert not errors
    hints = [e.payload for e in recorded if e.kind == 'experience.hint']
    assert len(hints) == 1 and hints[0]['code'] == 'permission_unavailable'


@pytest.mark.parametrize(('field', 'value', 'code'), [
    ('catalog_calls', 12, 'catalog_limit'), ('catalog_bytes', 16384, 'catalog_budget'),
])
def test_hint_suppression_uses_real_catalog_quotas(tmp_path, projects, facts, field, value, code):
    env = hint_environment(tmp_path, projects, facts)
    setattr(env.runtime.experience_view, field, value)
    assert explore(env).kind == 'candidate'
    assert all(HINT_TEXT not in system_text(r) for r in env.requests)
    recorded, errors = env.store.read_events()
    assert not errors
    hints = [e.payload for e in recorded if e.kind == 'experience.hint']
    assert len(hints) == 1 and hints[0]['code'] == code


def test_hint_event_write_failure_cannot_change_the_candidate(tmp_path, projects, facts, monkeypatch):
    env = hint_environment(tmp_path, projects, facts)
    append = env.store.append_event
    def fail_hint(kind, refs, payload):
        if kind == 'experience.hint':
            raise OSError('offline diagnostic failure')
        return append(kind, refs, payload)
    monkeypatch.setattr(env.store, 'append_event', fail_hint)
    assert explore(env).kind == 'candidate'
    assert HINT_TEXT in system_text(env.requests[1])


def test_decision_reminder_follows_a_continue_feedback_once(tmp_path, projects, facts):
    env = runtime_environment(tmp_path, projects, facts, answers=[])
    middleware = ExplorationMiddleware(
        env.context, env.gate, env.store,
        draft_session=ExperienceDraftSession(env.store))
    middleware.phase_context = SimpleNamespace(feedback="")
    assert middleware._lesson_decision_message() is None
    middleware.phase_context = SimpleNamespace(feedback='{"decision":"continue"}')
    message = middleware._lesson_decision_message()
    assert message is not None and message.name == "lesson-decision"
    # SDK 2.0.9 stores system content as text blocks, so read the rendered text.
    assert "上次操作未通过" in message.get_text_content()
    # Preparation is stable until delivery or an explicit drop; it is not a second copy.
    assert middleware._lesson_decision_message() is message
    middleware._deliver_lesson_decision()
    assert middleware._lesson_decision_message() is None
    events, errors = env.store.read_events()
    assert not errors
    decisions = [event.payload for event in events if event.kind == 'experience.lesson_decision']
    assert [item['code'] for item in decisions] == ['prepared', 'delivered']
    assert {item['next_call_index'] for item in decisions} == {1}
    assert decisions[0]['reason'] == 'verification_continue'
    assert decisions[1]['reason'] == 'model_returned'


def test_decision_reminder_is_ephemeral_and_delivered_once(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.answers[:] = [
        tool_reply("Read", {"file_path": "absent.py"}, call_id="bad-read"),
        tool_reply("Read", {"file_path": "absent.py"}, call_id="second-read"),
        tool_reply("write_candidate", write_payload(), call_id="publish")]
    assert explore(env, feedback='{"decision":"continue"}').kind == "candidate"
    assert LESSON_DECISION_TEXT in system_text(env.requests[0])
    assert sum(LESSON_DECISION_TEXT in system_text(request) for request in env.requests) == 1
    assert not any(getattr(message, "name", "") == "lesson-decision" for message in env.runtime.messages)


def test_decision_reminder_follows_a_controlled_tool_error_only(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    assert explore(env).kind == "candidate"
    assert LESSON_DECISION_TEXT not in system_text(env.requests[0])
    assert LESSON_DECISION_TEXT in system_text(env.requests[1])


def test_decision_reminder_from_a_tool_error_is_delivered_once_and_not_persisted(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.answers[:] = [
        tool_reply("Read", {"file_path": "absent.py"}, call_id="bad-read"),
        tool_reply("Read", {"file_path": "absent.py"}, call_id="second-read"),
        tool_reply("write_candidate", write_payload(), call_id="publish")]
    assert explore(env).kind == "candidate"
    assert sum(LESSON_DECISION_TEXT in system_text(request) for request in env.requests) == 1
    assert LESSON_DECISION_TEXT in system_text(env.requests[1])
    assert not any(getattr(message, "name", "") == "lesson-decision" for message in env.runtime.messages)


def test_decision_reminder_follows_a_permission_denial_only(tmp_path, projects, facts):
    from agentscope.permission import PermissionBehavior, PermissionRule
    env = hint_environment(tmp_path, projects, facts)
    context = env.runtime._agent.state.permission_context
    context.deny_rules["Read"] = [PermissionRule(
        tool_name="Read", rule_content=None, behavior=PermissionBehavior.DENY, source="test")]
    assert explore(env).kind == "candidate"
    assert LESSON_DECISION_TEXT not in system_text(env.requests[0])
    assert LESSON_DECISION_TEXT in system_text(env.requests[1])


def test_decision_reminder_follows_a_protocol_rejection(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.answers[:] = [
        tool_reply("NotATool", {}, call_id="bad-protocol"),
        tool_reply("write_candidate", write_payload(), call_id="publish")]
    assert explore(env).kind == "candidate"
    assert LESSON_DECISION_TEXT not in system_text(env.requests[0])
    assert LESSON_DECISION_TEXT in system_text(env.requests[1])


@pytest.mark.parametrize("feedback", [
    '{"decision":"stop"}', '{"decision":"something-else"}', "arbitrary feedback text"])
def test_decision_reminder_requires_a_controlled_failure(tmp_path, projects, facts, feedback):
    env = runtime_environment(tmp_path, projects, facts, answers=[])
    middleware = ExplorationMiddleware(
        env.context, env.gate, env.store,
        draft_session=ExperienceDraftSession(env.store))
    middleware.phase_context = SimpleNamespace(feedback=feedback)
    assert middleware._lesson_decision_message() is None


def test_capacity_failure_drops_the_decision_reminder_without_stopping_the_task(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.model.context_size = 200

    async def count_tokens(*args, **kwargs):
        messages = kwargs.get("messages", args[0] if args else [])
        text = "\n".join(str(getattr(message, "content", "")) for message in messages)
        return 300 if LESSON_DECISION_TEXT in text else 150

    env.runtime._agent.model.count_tokens = count_tokens
    assert explore(env, feedback='{"decision":"continue"}').kind == "candidate"
    assert all(LESSON_DECISION_TEXT not in system_text(request) for request in env.requests)
    assert not any(getattr(message, "name", "") == "lesson-decision" for message in env.runtime.messages)


def test_a_failed_model_call_records_uncertain_delivery_without_repreparing(tmp_path, projects, facts):
    env = runtime_environment(tmp_path, projects, facts, answers=[])
    middleware = ExplorationMiddleware(
        env.context, env.gate, env.store,
        draft_session=ExperienceDraftSession(env.store))
    middleware.phase_context = SimpleNamespace(feedback='{"decision":"continue"}')
    assert middleware._lesson_decision_message() is not None
    middleware._unconfirm_lesson_decision()
    assert middleware._lesson_reminded is False
    assert middleware._lesson_uncertain is True
    assert middleware._lesson_decision_message() is None
    events, errors = env.store.read_events()
    assert not errors
    decisions = [event.payload for event in events if event.kind == 'experience.lesson_decision']
    assert [(item['code'], item['reason'], item['next_call_index']) for item in decisions] == [
        ('prepared', 'verification_continue', 1),
        ('delivery_unconfirmed', 'model_call_failed', 1)]


def test_capacity_drop_allows_the_reminder_to_be_delivered_on_the_next_call(tmp_path, projects, facts):
    env = hint_environment(tmp_path, projects, facts)
    env.answers[:] = [
        tool_reply("Read", {"file_path": "absent.py"}, call_id="bad-read"),
        tool_reply("Read", {"file_path": "absent.py"}, call_id="second-read"),
        tool_reply("write_candidate", write_payload(), call_id="publish")]
    env.model.context_size = 200
    dropped_once = [False]

    def text_of(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(text_of(item) for item in value)
        return str(getattr(value, "text", ""))

    async def count_tokens(*args, **kwargs):
        messages = kwargs.get("messages", args[0] if args else [])
        joined = "\n".join(text_of(message.get("content", "") if isinstance(message, dict)
                                   else getattr(message, "content", "")) for message in messages)
        if LESSON_DECISION_TEXT in joined and not dropped_once[0]:
            dropped_once[0] = True
            return 300
        return 150

    env.runtime._agent.model.count_tokens = count_tokens
    assert explore(env).kind == "candidate"
    assert LESSON_DECISION_TEXT not in system_text(env.requests[1])
    assert LESSON_DECISION_TEXT in system_text(env.requests[2])
    events, errors = env.store.read_events()
    assert not errors
    decisions = [event.payload for event in events if event.kind == 'experience.lesson_decision']
    assert [(item['code'], item['next_call_index']) for item in decisions] == [
        ('prepared', 2), ('not_delivered', 2), ('prepared', 3), ('delivered', 3)]
    assert decisions[1]['reason'] == 'context_capacity'
    assert decisions[3]['reason'] == 'model_returned'


def test_budget_stop_before_the_reminder_reaches_the_wire_is_not_delivered(tmp_path, projects, facts):
    from reproagent.core.budget import BudgetStopped
    env = hint_environment(tmp_path, projects, facts)

    def text_of(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(text_of(item) for item in value)
        return str(getattr(value, "text", ""))

    async def count_tokens(*args, **kwargs):
        messages = kwargs.get("messages", args[0] if args else [])
        joined = "\n".join(text_of(message.get("content", "") if isinstance(message, dict)
                                   else getattr(message, "content", "")) for message in messages)
        if LESSON_DECISION_TEXT in joined:
            env.context.budget.deadline = 0
        return 150

    env.runtime._agent.model.count_tokens = count_tokens
    with pytest.raises(BudgetStopped) as error:
        explore(env)
    assert error.value.reason == 'EXHAUSTED'
    events, errors = env.store.read_events()
    assert not errors
    decisions = [event.payload for event in events if event.kind == 'experience.lesson_decision']
    assert [(item['code'], item['next_call_index']) for item in decisions] == [
        ('prepared', 2), ('not_delivered', 2)]
    assert decisions[1]['reason'] == 'budget_stopped'
    assert decisions[1]['stop_reason'] == 'EXHAUSTED'
    assert all(LESSON_DECISION_TEXT not in system_text(request) for request in env.requests)


def test_deadline_during_hint_capacity_check_keeps_the_real_stop_reason(tmp_path, projects, facts):
    from reproagent.core.budget import BudgetStopped
    env = hint_environment(tmp_path, projects, facts)
    async def count_tokens(*args, **kwargs):
        if any(HINT_TEXT in str(getattr(m, 'content', '')) for m in kwargs.get('messages', [])):
            env.context.budget.deadline = 0
        return 150
    env.runtime._agent.model.count_tokens = count_tokens
    with pytest.raises(BudgetStopped) as error:
        explore(env)
    assert error.value.reason == 'EXHAUSTED'
    recorded, errors = env.store.read_events()
    assert not errors
    hints = [e.payload for e in recorded if e.kind == 'experience.hint']
    assert len(hints) == 1 and hints[0]['code'] == 'budget_stopped'
    assert hints[0]['stop_reason'] == 'EXHAUSTED'
    assert len(env.requests) == 1
