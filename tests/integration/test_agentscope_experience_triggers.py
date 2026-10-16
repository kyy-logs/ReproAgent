import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.experience import ExperienceSnapshot
from reproagent.experience_catalog import ExperienceCatalogView
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.adapters.agentscope.tools import build_toolkit
from reproagent.core.models import BudgetLimits
from reproagent.core.serialization import bytes_hash
from reproagent.experience_triggers import ExperienceTrigger
from tests.unit.test_experience_trigger_view import triggered, RANGE
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply, write_payload


def configured(tmp_path, projects, facts, *, memory=None, limits=None, browse=True):
    env = environment(tmp_path, projects, facts, limits=limits)
    memory = memory or triggered("引用范围被拒时先取得有效范围。", "UNIQUE_RECOVERY_ADVICE")
    view = ExperienceCatalogView(ExperienceSnapshot("ready", "a"*64, (memory,)))
    initial = ()
    toolkit = build_toolkit(env.backend, env.ledger, env.service, env.gate, env.context, experience_view=view)
    env.runtime = AgentScopeRuntime(env.model, toolkit, env.gate, env.context,
        system_prompt="Reproduce from original facts; historical advice is conditional.",
        store=env.store, experience_view=view)
    env.view, env.memory, env.initial = view, memory, initial
    if browse:
        env.answers.append(tool_reply('list_experiences', {'category': memory.category}, call_id='catalog'))
    return env


def revision(env, *, wrong_hash=False):
    return {"source_refs": [{"path": "example/parser.py", "content_hash": "0"*64 if wrong_hash else bytes_hash(env.module.read_bytes()),
        "start_line": 1, "end_line": 1}], "reason": "Need a grounded contract"}


def run(env):
    async def go():
        try:
            return await env.runtime.explore(replace(agent_context(env), experience_summaries=env.initial))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    return asyncio.run(go())


def injected(request):
    result = []
    for message in request["messages"]:
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        try:
            data = json.loads(message["content"])
        except ValueError:
            continue
        if isinstance(data, dict):
            result.extend(s["id"] for s in data.get("experience_summaries", []))
    return result


@pytest.mark.parametrize("wrong_hash", [False, True])
def test_actual_tool_error_is_observed_without_automatic_injection(tmp_path, projects, facts, wrong_hash):
    env = configured(tmp_path, projects, facts)
    env.answers.extend([tool_reply("revise_contract", revision(env, wrong_hash=wrong_hash), call_id="bad"),
        tool_reply("request_information", {"question":"missing facts"}, call_id="stop")])
    assert run(env).kind == "request_information"
    assert all(not injected(r) for r in env.requests)
    assert env.view.read_ids == ()
    assert env.memory.id in env.view._shown  # Initial browsing authorizes IDs, not detail injection.
    events, _ = env.store.read_events()
    assert any(e.kind == "experience.triggered" for e in events)
    assert not any(e.kind == "experience.injected" for e in events)


def test_repeated_refusals_do_not_retrieve_or_replay_tools(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    env.answers.extend([tool_reply("revise_contract",revision(env),call_id="bad1"),
        tool_reply("revise_contract",revision(env),call_id="bad2"),
        tool_reply("request_information",{"question":"missing facts"},call_id="stop")])
    run(env)
    assert env.context.budget.steps_used == len(env.requests) == 4
    assert all(not injected(r) for r in env.requests)


def test_reserved_reader_permission_still_blocks_execution(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=4), browse=False)
    env.context.budget.take_step()  # At permission time only the scaled two-step reserve remains.
    env.answers.extend([tool_reply("list_experiences",{"category":"workflow"},call_id="reserved"),
        tool_reply("write_candidate",write_payload(),call_id="publish")])
    assert run(env).kind == "candidate"
    assert env.view.catalog_calls == 0
    events, _ = env.store.read_events()
    assert any(e.kind == "exploration.action" and e.payload.get("result_code") == "RESERVED_FOR_PUBLISHING" for e in events)


def test_protocol_correction_does_not_auto_inject_memory(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    env.answers.extend([tool_reply(calls=[("Read",{"file_path":str(env.module)},"a"),("Glob",{},"b")]),
        tool_reply("request_information",{"question":"missing facts"},call_id="stop")])
    assert run(env).kind == "request_information"
    assert env.context.budget.steps_used == len(env.requests) == 3
    assert all(not injected(r) for r in env.requests)


def test_cancelled_catalog_has_no_display_authorization(tmp_path, projects, facts):
    from reproagent.adapters.agentscope.tools import ListExperiences
    from reproagent.core.budget import BudgetStopped
    env = configured(tmp_path,projects,facts)
    env.context.cancel_event.set()
    with pytest.raises(BudgetStopped):
        asyncio.run(ListExperiences(env.view,env.store,env.context).call(category="workflow"))
    assert env.view.catalog_calls == 0 and env.view.read_ids == ()


def test_trace_keeps_error_condition_but_has_no_injection_span(tmp_path, projects, facts):
    from reproagent.observability import trace_session
    with trace_session() as recorder:
        env = configured(tmp_path, projects, facts)
        env.answers.extend([tool_reply("revise_contract",revision(env),call_id="bad"),
            tool_reply("request_information",{"question":"missing facts"},call_id="stop")])
        run(env)
        doc = recorder.finish(task_id="offline",status="NEEDS_INFORMATION",main_duration=1)
    assert not any(s["name"] == "experience.injected" for s in doc["spans"])
    observed = next(s for s in doc["spans"] if s["name"] == "experience.triggered")
    assert observed["attributes"]["experience_trigger"]["code"] == RANGE.code
