"""What one rejected exploration response costs, and what it is never allowed to do.

The phase answers with tool calls, so a response is judged whole before anything of it
runs: a second call, an unknown tool or arguments that are not one complete JSON object are
refused with a fixed code, the reply is asked for again within a bounded number of attempts,
and every attempt is charged to the step budget.  Nothing of a refused response executes --
there is no first call that runs before the second is judged.
"""
import asyncio
import json

import pytest

pytest.importorskip("agentscope")

from reproagent.adapters.agentscope.middleware import PROTOCOL_ATTEMPTS, PhaseProtocolError
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import BudgetLimits

from tests.integration.test_agentscope_runtime import (
    agent_context, environment, explore, text_reply, tool_reply, until, write_payload,
)

#: Every shape of response the phase refuses whole, and the code that names it.  A refusal
#: never carries model or provider text, so each one is a fixed, greppable code.
REFUSALS = [
    ("UNKNOWN_TOOL", tool_reply("Write", {"path": "tests/test_repro.py", "content": "x"})),
    ("UNKNOWN_TOOL", tool_reply("search_code", {"query": "parse", "scope": "snapshot"})),
    ("INVALID_TOOL_INPUT", tool_reply("Read", '{"file_path": ')),
    ("INVALID_TOOL_INPUT", tool_reply("write_candidate", "{not one object")),
    ("MULTIPLE_TOOL_CALLS", tool_reply(calls=[("Read", {"file_path": "a.py"}, "call-1"),
                                              ("Glob", {"pattern": "**/*.py"}, "call-2")])),
]


def test_a_rejected_response_is_corrected_and_each_attempt_is_counted(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([
        tool_reply("Write", {"path": "tests/test_repro.py", "content": "x"}),          # outside the phase surface
        tool_reply("Read", '{"file_path": '),                                          # not one complete JSON object
        tool_reply("write_candidate", write_payload(), call_id="call-3")])
    result = explore(env)
    assert result.kind == "candidate"
    # Two refusals, each charged to its own step, and the correction round asked for the
    # complete tool set rather than replaying the response that was refused.
    assert len(env.requests) == 3 == env.context.budget.steps_used
    assert [payload["result_code"] for payload in
            [event for event in _events(env, "exploration.protocol_error")]] == ["UNKNOWN_TOOL", "INVALID_TOOL_INPUT"]
    correction = env.requests[1]["messages"][-1]["content"]
    assert "write_candidate" in correction and "revise_contract" in correction
    assert "Write" not in json.dumps(env.requests[2])


@pytest.mark.parametrize("code, refused", REFUSALS)
def test_each_refusal_shape_has_its_own_code_and_no_side_effect(code, refused, tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([refused, tool_reply("write_candidate", write_payload(), call_id="call-9")])
    result = explore(env)
    assert result.kind == "candidate"
    assert len(env.requests) == 2 == env.context.budget.steps_used
    assert [payload["result_code"] for payload in _events(env, "exploration.protocol_error")] == [code]
    # The refused response ran nothing: only the phase's own candidate was published, and
    # the refused arguments never entered the conversation.
    assert [item.name for item in (env.task / "candidates").iterdir()] == [result.candidate_id]
    assert "not one object" not in json.dumps(env.requests[1]["messages"])


def test_protocol_correction_is_bounded_and_the_phase_stays_open(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([tool_reply(calls=[("Read", {"file_path": "a.py"}, "call-1"),
                                          ("Read", {"file_path": "b.py"}, "call-2")])] * (PROTOCOL_ATTEMPTS + 1))
    with pytest.raises(PhaseProtocolError) as failure:
        explore(env)
    assert failure.value.code == "MULTIPLE_TOOL_CALLS"
    assert len(env.requests) == PROTOCOL_ATTEMPTS == 3
    assert env.context.budget.steps_used == 3
    # Neither call of any refused response ran, and the phase published nothing.
    assert env.gate.result is None and not (env.task / "candidates").exists()


def test_a_correction_cannot_exceed_the_remaining_step_budget(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1))
    env.answers.extend([tool_reply("Write", {"path": "tests/test_repro.py"}), text_reply()])
    with pytest.raises(BudgetStopped) as failure:
        explore(env)
    assert failure.value.reason == "EXHAUSTED"
    # The refused response cost its step, and the correction it asked for never left.
    assert len(env.requests) == 1 == env.context.budget.steps_used


def test_a_plain_answer_never_becomes_an_action(tmp_path, projects, facts):
    """Two JSON objects in one text answer are text, not two actions.

    The phase acts through tool calls, so an answer that contains JSON but calls no tool
    runs nothing, publishes nothing and does not start a correction round.
    """
    env = environment(tmp_path, projects, facts)
    env.answers.extend([
        text_reply(json.dumps(write_payload()) + "\n" + json.dumps(write_payload())),
        text_reply()])
    result = explore(env)
    assert result.kind == "no_candidate" and result.reason
    assert len(env.requests) == 1 == env.context.budget.steps_used
    assert not (env.task / "candidates").exists()
    assert not _events(env, "exploration.protocol_error")


def test_cancel_between_corrections_sends_no_further_request(tmp_path, projects, facts):
    """A cancelled task is not asked for the correction round its refusal would start."""
    async def scenario():
        seen, release = [], asyncio.Event()

        async def held(request):
            seen.append(json.loads(request.content))
            await release.wait()
            return tool_reply("Write", {"path": "tests/test_repro.py"})    # refused whole

        env = environment(tmp_path, projects, facts, handler=held)
        running = asyncio.ensure_future(env.runtime.explore(agent_context(env)))
        await until(lambda: seen)
        env.context.cancel_event.set()
        release.set()
        with pytest.raises(BudgetStopped) as failure:
            await running
        assert failure.value.reason == "CANCELLED"
        # The refused response never bought a second request, whatever ended the first one.
        assert len(seen) == 1 == env.context.budget.steps_used

    asyncio.run(scenario())


def _events(env, kind):
    stored, errors = env.store.read_events()
    assert not errors
    return [event.payload for event in stored if event.kind == kind]
