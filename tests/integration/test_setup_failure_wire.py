"""A mixed passing/setup-failed candidate must deliver the real fixture error."""
import asyncio
import json
from dataclasses import replace

from tests.unit.test_controller import setup, publish, ask
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply


def test_mixed_setup_failure_reaches_next_sdk_wire_with_original_error_and_recovery(tmp_path, projects, facts):
    text = ("def test_missing(warning_stream):\n    assert False\n"
            "def test_valid():\n    from example.parser import parse\n    assert parse([1]) == [1]\n")
    request, model, controller, ctx = setup(tmp_path / "product", projects, facts,
        plan=[publish(content=text), ask()])
    asyncio.run(controller.run(request, ctx))
    feedback = controller.explorer.contexts[1].feedback
    seen = []

    def handler(http_request):
        wire = json.loads(http_request.content)
        phases = []
        for message in wire["messages"]:
            if message["role"] != "user" or not isinstance(message["content"], str):
                continue
            try:
                data = json.loads(message["content"])
            except ValueError:
                continue
            if "feedback" in data:
                phases.append(data)
        actual = json.loads(phases[-1]["feedback"])
        seen.append(actual)
        return tool_reply("request_information", {"question":"Need original fixture usage"})

    env = environment(tmp_path / "sdk", projects, facts, handler=handler)
    async def go():
        try:
            return await env.runtime.explore(replace(agent_context(env), feedback=feedback))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(go()).kind == "request_information"
    assert len(seen) == 1
    actual = seen[0]
    issue = actual["verdict"]["issues"][0]
    assert issue["code"] == "FRAMEWORK_CHECK_FAILED" and issue["origin"] == "program_check"
    assert issue["measurements"]["executed"] is True
    assert "fixture 'warning_stream' not found" in issue["measurements"]["setup_failures"][0]["message"]
    assert "Read" in actual["instruction"] and "fixture" in actual["instruction"]
    assert actual["verdict"]["candidate_id"] and actual["verdict"]["run_ids"]
