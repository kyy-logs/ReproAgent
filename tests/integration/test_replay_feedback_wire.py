import asyncio
import json
from dataclasses import replace
from reproagent.observability import TraceRecorder

from tests.unit.test_controller import setup, ScriptedModel, publish, ask
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply


class ChangingReviewer(ScriptedModel):
    async def complete(self, request, context):
        if request.response_kind == "verdict" and "verdict" in self.kinds:
            self.classification = "NOT_REPRODUCED"
        return await super().complete(request, context)


def test_semantic_disagreement_reaches_next_sdk_wire_without_declaring_success(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path / "product", projects, facts, plan=[publish(), ask()])
    controller.verifier.gateway = ChangingReviewer()
    with TraceRecorder() as recorder:
        result = asyncio.run(controller.run(request, ctx))
        trace = recorder.finish(task_id=result.task_id, status=result.status.value, main_duration=result.duration)
    assert result.status.value == "NEEDS_INFORMATION" and result.evidence_level.value == "SINGLE_OBSERVATION"
    feedback = controller.explorer.contexts[1].feedback
    seen = []
    def handler(http_request):
        wire = json.loads(http_request.content)
        for msg in wire["messages"]:
            if msg["role"] != "user" or not isinstance(msg["content"], str):
                continue
            try: data = json.loads(msg["content"])
            except ValueError: continue
            if "feedback" in data: seen.append(json.loads(data["feedback"]))
        return tool_reply("request_information", {"question":"Need original expectation support"})
    env = environment(tmp_path / "sdk", projects, facts, handler=handler)
    async def go():
        try: return await env.runtime.explore(replace(agent_context(env), feedback=feedback))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(go()).kind == "request_information"
    data = seen[-1]
    replay = data["replay_observation"]
    assert replay["kind"] == "semantic_disagreement" and replay["execution_consistent"] is True
    assert replay["semantic_confirmed"] is False
    assert replay["verdict_classes"] == ["REPRODUCED", "NOT_REPRODUCED"]
    assert "语义审查" in data["instruction"] and "不得视为成功" in data["instruction"]
    assert data["result_code"] == "REPLAY_UNCONFIRMED" and data["decision"] == "continue"
    event = next(e for e in controller.store.read_events()[0] if e.kind == "verification.feedback")
    assert event.payload["replay_observation"] == replay
    points = [s for s in trace["spans"] if s["name"] == "verification.feedback"]
    assert points[0]["attributes"]["replay_observation"] == replay
