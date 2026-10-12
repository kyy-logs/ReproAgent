import asyncio
import json
from dataclasses import replace

from reproagent.experience import ExperienceSnapshot
from reproagent.experience_catalog import ExperienceCatalogView
from reproagent.adapters.agentscope.tools import build_toolkit, TOOL_NAMES
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from tests.unit.test_experience_store import card
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply, write_payload


def catalog_environment(tmp_path, projects, facts, answers):
    env = environment(tmp_path, projects, facts, answers=answers)
    fixed = ExperienceCatalogView(ExperienceSnapshot('ready', 'a' * 64, (card(detail='UNIQUE_LOADED_DETAIL'), card('other', 'UNREAD_DETAIL'))))
    toolkit = build_toolkit(env.backend, env.ledger, env.service, env.gate, env.context, experience_view=fixed)
    env.runtime = AgentScopeRuntime(env.model, toolkit, env.gate, env.context,
        system_prompt='Browse optional category catalogs, then reproduce from original sources.', store=env.store, experience_view=fixed)
    return env, fixed


def test_sdk_catalog_then_detail_then_publish(tmp_path, projects, facts):
    env, fixed = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
        tool_reply('read_experience', {'id': card().id}, call_id='memory'),
        tool_reply('write_candidate', write_payload(), call_id='publish')])
    async def run():
        try: return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(run()).kind == 'candidate'
    assert env.context.budget.steps_used == 3
    assert [t['function']['name'] for t in env.requests[0]['tools']] == [*TOOL_NAMES, 'list_experiences', 'read_experience']
    assert 'UNIQUE_LOADED_DETAIL' not in json.dumps(env.requests[0]['messages'])
    assert 'UNIQUE_LOADED_DETAIL' not in json.dumps(env.requests[1]['messages'])
    assert 'UNIQUE_LOADED_DETAIL' in json.dumps(env.requests[2]['messages'])
    assert all('UNREAD_DETAIL' not in json.dumps(r['messages']) for r in env.requests)
    assert fixed.read_ids == (card().id,)
    events, _ = env.store.read_events()
    assert any(e.kind == 'experience.catalog' and e.payload['ids'] for e in events)


def test_error_does_not_auto_inject_detail_or_summary(tmp_path, projects, facts):
    env, fixed = catalog_environment(tmp_path, projects, facts, [
        tool_reply('Read', {'file_path': 'absent.py'}, call_id='error'),
        tool_reply('request_information', {'question': 'Need source'}, call_id='stop')])
    async def run():
        try: return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(run()).kind == 'request_information'
    assert fixed.read_ids == ()
    assert all('experience_summaries' not in json.dumps(r['messages']) for r in env.requests)
