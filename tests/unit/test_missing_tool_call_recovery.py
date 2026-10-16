"""Only SDK tool calls may advance a phase; prose gets bounded correction."""
import asyncio
import json

import pytest

from reproagent.adapters.agentscope.middleware import PhaseProtocolError, PROTOCOL_ATTEMPTS
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import BudgetLimits, TaskState, EvidenceLevel
from tests.integration.test_agentscope_runtime import (
    agent_context, candidate_ids, environment, events, explore, pending_tasks,
    read_reply, text_reply, tool_reply, write_payload,
)
from tests.integration.test_agentscope_explorer import exploration, run_task
from tests.unit.test_controller import BUGGY


@pytest.mark.parametrize("text", ["Done. SECRET_REFUSED_TEXT", "", json.dumps(write_payload()) + "\n" + json.dumps(write_payload())])
def test_prose_or_embedded_json_requires_an_actual_tool_call(tmp_path, projects, facts, text):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([text_reply(text), tool_reply("write_candidate", write_payload(), call_id="real-publish")])
    result = explore(env)
    assert result.kind == "candidate"
    assert len(env.requests) == 2 == env.context.budget.steps_used
    assert candidate_ids(env) == [result.candidate_id]
    assert [e["result_code"] for e in events(env, "exploration.protocol_error")] == ["MISSING_TOOL_CALL"]
    correction = next(m["content"] for m in env.requests[1]["messages"]
                      if m.get("role") == "user" and m["content"].startswith("<system-reminder>"))
    assert "MISSING_TOOL_CALL" in correction and "exactly one" in correction
    assert "actual tool call" in correction
    assert "SECRET_REFUSED_TEXT" not in json.dumps(env.requests[1]["messages"])
    assert "SECRET_REFUSED_TEXT" not in str(events(env))
    assert not [m for m in env.requests[1]["messages"] if m.get("tool_calls")]


def test_missing_calls_share_the_existing_protocol_cap(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, answers=[
        tool_reply("Write", {"path": "x"}), text_reply("not a call"),
        text_reply("still not a call"), tool_reply("write_candidate", write_payload())])
    with pytest.raises(PhaseProtocolError) as failure:
        explore(env)
    assert failure.value.code == "MISSING_TOOL_CALL"
    assert failure.value.failures == ("UNKNOWN_TOOL", "MISSING_TOOL_CALL", "MISSING_TOOL_CALL")
    assert len(env.requests) == PROTOCOL_ATTEMPTS == env.context.budget.steps_used
    assert not candidate_ids(env) and not env.gate.finished
    assert len(env.answers) == 1


def test_correction_keeps_successful_reader_history(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([read_reply(env.module, "read-first"), text_reply("revise JSON in prose"),
                        tool_reply("request_information", {"question": "Which behaviour?"}, call_id="real-end")])
    result = explore(env)
    assert result.kind == "request_information"
    assert len(env.requests) == 3 == env.context.budget.steps_used
    assert any(m.get("role") == "tool" and "parse" in str(m) for m in env.requests[-1]["messages"])


@pytest.mark.parametrize("steps", [1, 2])
def test_correction_cannot_buy_steps(tmp_path, projects, facts, steps):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=steps),
                      answers=[text_reply("no call") for _ in range(4)])
    with pytest.raises(BudgetStopped) as failure:
        explore(env)
    assert failure.value.reason == "EXHAUSTED"
    assert len(env.requests) == steps == env.context.budget.steps_used
    assert not candidate_ids(env)


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_task_stop_prevents_a_correction_request(tmp_path, projects, facts, stop):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        if stop == "cancel":
            env.context.cancel_event.set()
        else:
            env.context.budget.deadline = 0
        return text_reply("no call")
    env = environment(tmp_path, projects, facts, handler=handler)
    async def run():
        with pytest.raises(BudgetStopped) as failure:
            await env.runtime.explore(agent_context(env))
        assert failure.value.reason == ("CANCELLED" if stop == "cancel" else "EXHAUSTED")
        assert len(seen) == 1 == env.context.budget.steps_used
        await env.runtime.aclose()
        assert not pending_tasks()
    asyncio.run(run())


def test_recovery_reaches_real_controller_execution_and_verification(tmp_path, projects, facts):
    request, controller, context, requests, _ = exploration(tmp_path, projects, facts, [
        text_reply("The bug is reproduced, with JSON but no tool call."),
        tool_reply("write_candidate", write_payload(content=BUGGY), call_id="publish-after-correction")])
    result = run_task(request, controller, context)
    assert result.status == TaskState.DONE
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert len(requests) == 2 == context.budget.steps_used
    assert len(list((request.output_dir / "runs").glob("*/execution.json"))) == 2
    assert (request.output_dir / "artifacts/reproduction/replay.py").is_file()


@pytest.mark.parametrize("attempt", [1, PROTOCOL_ATTEMPTS])
def test_finished_phase_wins_over_a_late_prose_response(tmp_path, projects, facts, attempt):
    from reproagent.core.phase import PhaseResult
    seen = []
    expected = PhaseResult("request_information", question="Bound result")
    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == attempt:
            env.gate.finish(expected)
        return text_reply("late text cannot replace a bound phase result")
    env = environment(tmp_path, projects, facts, handler=handler)
    assert explore(env) == expected
    assert len(seen) == attempt == env.context.budget.steps_used
    assert len(events(env, "exploration.protocol_error")) == attempt - 1
    assert events(env, "exploration.finished")[0]["sdk_end_reason"] == "phase_ended"


def test_missing_call_code_is_a_controlled_learnable_fact():
    from reproagent.experience_triggers import evidence_triggers, trigger_from_fact
    from reproagent.learning_material import CONTROLLED
    fact = {"kind": "protocol_error", "tool": "", "code": "MISSING_TOOL_CALL"}
    assert "MISSING_TOOL_CALL" in CONTROLLED
    assert trigger_from_fact(fact) is not None
    assert evidence_triggers({"kind": "tool_error", "text": json.dumps({
        "kind": "exploration.protocol_error", "code": "MISSING_TOOL_CALL"})}) == (trigger_from_fact(fact),)
