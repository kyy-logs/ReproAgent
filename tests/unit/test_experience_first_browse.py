import pytest

from reproagent.adapters.agentscope.experience_browse import ExperienceBrowseGate


def test_first_browse_is_task_scoped_and_refusals_are_bounded():
    gate = ExperienceBrowseGate(True)
    assert gate.state == 'pending'
    assert gate.reject() is True
    assert gate.state == 'pending'
    assert gate.reject() is True
    assert (gate.state, gate.reason, gate.refusals) == ('bypassed', 'noncompliant', 2)
    gate.satisfy()
    assert gate.state == 'bypassed'


def test_no_library_does_not_block_and_terminal_states_do_not_change():
    gate = ExperienceBrowseGate(False)
    assert gate.state == 'bypassed'
    gate.satisfy()
    assert gate.state == 'bypassed'
    gate = ExperienceBrowseGate(True)
    gate.satisfy()
    gate.bypass('response_capacity')
    assert gate.state == 'satisfied'


@pytest.mark.parametrize('reserved', [True, False])
def test_original_permission_and_reserve_cannot_be_overridden(facts, reserved):
    import asyncio
    from types import SimpleNamespace
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from reproagent.adapters.agentscope.middleware import ExplorationMiddleware
    from reproagent.adapters.agentscope.tools import TOOL_NAMES, EXPERIENCE_TOOL_NAMES
    context = facts.context()
    if reserved:
        for _ in range(context.budget.limits.agent_steps - 2):
            context.budget.take_step()
    gate = ExperienceBrowseGate(True)
    middleware = ExplorationMiddleware(context, SimpleNamespace(finished=False),
        allowed_tools=TOOL_NAMES + EXPERIENCE_TOOL_NAMES, browse_gate=gate)
    middleware.contract = SimpleNamespace(expected='x', missing_information=())
    async def decision(**kwargs):
        return PermissionDecision(behavior=PermissionBehavior.ALLOW if reserved else PermissionBehavior.DENY,
                                  message='SDK rule')
    result = asyncio.run(middleware.on_check_permission(None,
        {'tool': SimpleNamespace(name='list_experiences'), 'tool_input': {'category': 'model'}}, decision))
    assert result.behavior == PermissionBehavior.DENY
    assert gate.state == 'bypassed'
    assert gate.reason == ('RESERVED_FOR_PUBLISHING' if reserved else 'DENIED')


def test_notice_has_all_category_counts_from_frozen_snapshot():
    from reproagent.experience import ExperienceSnapshot
    from reproagent.experience_catalog import ExperienceCatalogView
    from tests.unit.test_experience_store import card
    view = ExperienceCatalogView(ExperienceSnapshot('ready', 'a' * 64, (card(),)))
    assert dict(view.notice().category_counts) == {'framework': 0, 'model': 1, 'workflow': 0}


@pytest.mark.parametrize('name', ['Read', 'Grep', 'Glob', 'read_experience',
                                 'write_candidate', 'revise_contract', 'request_information'])
def test_pending_refuses_other_tools_and_second_refusal_is_still_denied(facts, name):
    import asyncio
    from types import SimpleNamespace
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from reproagent.adapters.agentscope.middleware import ExplorationMiddleware
    from reproagent.adapters.agentscope.tools import TOOL_NAMES, EXPERIENCE_TOOL_NAMES
    gate = ExperienceBrowseGate(True)
    middleware = ExplorationMiddleware(facts.context(), SimpleNamespace(finished=False),
        allowed_tools=TOOL_NAMES + EXPERIENCE_TOOL_NAMES, browse_gate=gate)
    async def allow(**kwargs):
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message='allowed')
    async def run():
        for _ in range(2):
            result = await middleware.on_check_permission(None,
                {'tool': SimpleNamespace(name=name), 'tool_input': {}}, allow)
            assert result.behavior == PermissionBehavior.DENY
            assert 'EXPERIENCE_BROWSE_REQUIRED' in result.message
        assert gate.state == 'bypassed'
        result = await middleware.on_check_permission(None,
            {'tool': SimpleNamespace(name=name), 'tool_input': {}}, allow)
        assert result.behavior == PermissionBehavior.ALLOW
    asyncio.run(run())


def test_delivery_requires_matching_actual_tool_result():
    gate = ExperienceBrowseGate(True)
    gate.stage_delivery('catalog', '{"code":"ready"}')
    assert not gate.confirm_delivery([('other', 'list_experiences', 'success', '{"code":"ready"}')])
    assert not gate.confirm_delivery([('catalog', 'list_experiences', 'error', '{"code":"ready"}')])
    assert not gate.confirm_delivery([('catalog', 'list_experiences', 'success', 'truncated')])
    assert gate.state == 'pending'
    assert gate.confirm_delivery([('catalog', 'list_experiences', 'success', '{"code":"ready"}')])
    assert gate.state == 'satisfied'


def test_browse_decision_is_visible_in_trace_even_without_event_store(facts, monkeypatch):
    from types import SimpleNamespace
    from reproagent.adapters.agentscope import middleware as module
    points = []
    monkeypatch.setattr(module, 'mark', lambda name, **kwargs: points.append((name, kwargs)))
    middleware = module.ExplorationMiddleware(facts.context(), SimpleNamespace(finished=False),
        browse_gate=ExperienceBrowseGate(True))
    middleware.record_browse(category='model', ids=['exp-test'], bytes=100, tool_call_key='call-test')
    assert points[0][0] == 'experience.browse_gate'
    assert points[0][1]['attributes']['tool_call_key'] == 'call-test'


def test_observation_failure_does_not_block_browse(facts):
    from types import SimpleNamespace
    from reproagent.adapters.agentscope.middleware import ExplorationMiddleware
    class BrokenStore:
        def append_event(self, *args):
            raise RuntimeError('observation unavailable')
    middleware = ExplorationMiddleware(facts.context(), SimpleNamespace(finished=False),
        store=BrokenStore(), browse_gate=ExperienceBrowseGate(True))
    middleware.record_browse()


def test_browse_refusal_keeps_denied_permission_filter(facts, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from reproagent.adapters.agentscope import middleware as module
    points = []
    def mark(name, **kwargs):
        points.append((name, kwargs))
        return SimpleNamespace(capture=lambda *args, **kwargs: None)
    monkeypatch.setattr(module, 'mark', mark)
    middleware = module.ExplorationMiddleware(facts.context(), SimpleNamespace(finished=False),
        browse_gate=ExperienceBrowseGate(True))
    async def allow(**kwargs):
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message='allowed')
    asyncio.run(middleware.on_check_permission(None,
        {'tool': SimpleNamespace(name='Read'), 'tool_input': {}}, allow))
    attributes = next(kwargs['attributes'] for name, kwargs in points if name == 'permission')
    assert attributes['result_code'] == 'DENIED'
    assert attributes['error_category'] == 'EXPERIENCE_BROWSE_REQUIRED'
