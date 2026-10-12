import asyncio
import json

from reproagent.otel_projection import project_sdk_messages
from reproagent.observability import trace_session
from tests.integration.test_agentscope_experience_catalog import catalog_environment
from tests.integration.test_agentscope_runtime import agent_context, tool_reply, write_payload
from tests.unit.test_experience_store import card


def test_projection_preserves_catalog_tool_name():
    output = project_sdk_messages({'type':'tool_call', 'name':'list_experiences', 'id':'provider-private-id'})
    assert output['name'] == 'list_experiences'
    assert 'id' not in output


def test_catalog_trace_contains_call_chain_and_experience_ids(tmp_path, projects, facts):
    with trace_session() as recorder:
        env, fixed = catalog_environment(tmp_path, projects, facts, [
            tool_reply('list_experiences', {'category':'model'}, call_id='catalog'),
            tool_reply('read_experience', {'id':card().id}, call_id='detail'),
            tool_reply('write_candidate', write_payload(), call_id='publish')])
        async def run():
            try: return await env.runtime.explore(agent_context(env))
            finally:
                await env.runtime.aclose()
                await env.factory.aclose()
        assert asyncio.run(run()).kind == 'candidate'
        doc = recorder.finish(task_id='offline', status='DONE', main_duration=1)
    catalog = [s for s in doc['spans'] if s['name'] == 'experience.catalog']
    assert catalog and card().id in catalog[0]['attributes']['experience_ids']
    assert any(s['attributes'].get('tool') == 'list_experiences' for s in doc['spans'])
    assert 'UNIQUE_LOADED_DETAIL' in json.dumps(env.requests[-1]['messages'])
