import asyncio
import json
from dataclasses import replace
import pytest

from reproagent.experience import ExperienceSnapshot
from tests.unit.test_experience_store import card, exp
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply, write_payload
from reproagent.adapters.agentscope.tools import build_toolkit, TOOL_NAMES
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime


def configured(tmp_path, projects, facts, answers):
    env = environment(tmp_path, projects, facts, answers=answers)
    fixed = exp().ExperienceView(ExperienceSnapshot("ready", "a" * 64, (card(detail="UNIQUE_ADVICE"),)))
    summaries = fixed.select("pytest", ())
    toolkit = build_toolkit(env.backend, env.ledger, env.service, env.gate, env.context, experience_view=fixed)
    env.runtime = AgentScopeRuntime(env.model, toolkit, env.gate, env.context,
        system_prompt="Read optional advice then reproduce from original evidence.", store=env.store, experience_view=fixed)
    return env, fixed, summaries


def test_native_sdk_reads_one_experience_then_publishes(tmp_path, projects, facts):
    env, fixed, summaries = configured(tmp_path, projects, facts, [
        tool_reply("read_experience", {"id": card().id}, call_id="memory"),
        tool_reply("write_candidate", write_payload(), call_id="publish")])
    async def run():
        try:
            return await env.runtime.explore(replace(agent_context(env), experience_summaries=summaries))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    result = asyncio.run(run())
    assert result.kind == "candidate"
    assert env.context.budget.steps_used == 2
    assert fixed.read_ids == (card().id,)
    assert [t["function"]["name"] for t in env.requests[0]["tools"]] == [*TOOL_NAMES, "read_experience"]
    assert "UNIQUE_ADVICE" in json.dumps(env.requests[1]["messages"])
    events, errors = env.store.read_events()
    assert not errors
    assert [e.payload["id"] for e in events if e.kind == "experience.read"] == [card().id]


def test_revised_phase_does_not_reload_experience(tmp_path, projects, facts):
    env, fixed, summaries = configured(tmp_path, projects, facts, [
        tool_reply("read_experience", {"id": card().id}, call_id="memory"),
        tool_reply("write_candidate", write_payload(), call_id="publish"),
        tool_reply("read_experience", {"id": card().id}, call_id="again"),
        tool_reply("request_information", {"question": "missing facts"}, call_id="stop")])
    async def run():
        try:
            await env.runtime.explore(replace(agent_context(env), experience_summaries=summaries))
            env.service.bind_contract(replace(env.service.contract, version=2))
            return await env.runtime.explore(replace(agent_context(env), experience_summaries=summaries))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(run()).kind == "request_information"
    assert fixed.read_ids == (card().id,)
    user_inputs = [json.loads(m.get_text_content()) for m in env.runtime.messages if m.role == "user" and (m.get_text_content() or "").startswith("{")]
    assert sum("experience_summaries" in value for value in user_inputs) == 1
    assert env.context.budget.steps_used == 4


def test_optional_summary_cannot_displace_issue(tmp_path, projects, facts):
    from reproagent.core.models import BudgetLimits
    env, fixed, summaries = configured(tmp_path, projects, facts, [])
    plain = environment(tmp_path / "plain", projects, facts)
    message = plain.runtime._message(agent_context(plain))
    env.context.budget.limits = replace(env.context.budget.limits, tool_response_bytes=len(message.get_text_content().encode()) + 20)
    output = env.runtime._message(replace(agent_context(env), experience_summaries=summaries))
    assert "contract" in json.loads(output.get_text_content())
    assert "experience_summaries" not in json.loads(output.get_text_content())
    with pytest.raises(ValueError):
        fixed.read(card().id)

def test_experience_is_not_source_evidence(tmp_path, projects, facts):
    env, fixed, summaries = configured(tmp_path, projects, facts, [
        tool_reply("revise_contract", {"source_refs": [{"path": "learning/evidence.jsonl",
            "content_hash": "a" * 64, "start_line": 1, "end_line": 1}], "reason": "use historical advice"}, call_id="fake"),
        tool_reply("request_information", {"question": "need current evidence"}, call_id="stop")])
    async def run():
        try:
            return await env.runtime.explore(replace(agent_context(env), experience_summaries=summaries))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(run()).kind == "request_information"
    assert not list((env.task / "candidates").glob("*"))
    assert env.service.contract.version == 1


def test_detail_respects_task_tool_limit(tmp_path, projects, facts):
    from reproagent.adapters.agentscope.tools import ReadExperience
    env, fixed, summaries = configured(tmp_path, projects, facts, [])
    env.context.budget.limits = replace(env.context.budget.limits, tool_response_bytes=32)
    async def run():
        with pytest.raises(ValueError, match="budget"):
            await ReadExperience(fixed, env.store, env.context).call(id=summaries[0].id)
    asyncio.run(run())
    assert fixed.read_ids == ()
    events, _ = env.store.read_events()
    assert not any(e.kind == "experience.read" for e in events)
