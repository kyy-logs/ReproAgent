"""The trace observes the SDK's own hooks without changing what the SDK does.

These tests run a real ``agentscope.Agent`` over the phase toolkit and a mock HTTP
transport, so what is asserted is the wire and the SDK's published protocol rather
than a hand-rolled imitation of it.  Two facts have to stay separate throughout:
a permission decision is not an execution, and a logical model call is not the
three HTTP attempts it may take.
"""
import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("agentscope")

from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolChunk, ToolResponse

from reproagent.adapters.agentscope.evidence import EvidenceLedger
from reproagent.adapters.agentscope.middleware import (
    PROTOCOL_ATTEMPTS,
    ExplorationMiddleware,
    PhaseProtocolError,
)
from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
from reproagent.adapters.agentscope.observability import TraceMiddleware
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.adapters.agentscope.snapshot_backend import SnapshotBackend
from reproagent.adapters.agentscope.tools import TOOL_NAMES, build_toolkit
from reproagent.core.budget import BudgetStopped
from reproagent.core.candidate_service import CandidateService
from reproagent.core.models import (
    AgentContext,
    BudgetLimits,
    IssueContract,
    ModelConfig,
    ProjectView,
    PythonPytestConfig,
    SourceRef,
    TaskRequest,
)
from reproagent.core.phase import PhaseGate
from reproagent.observability import TraceRecorder
from reproagent.store import TaskStore
from reproagent.workspace import Workspace

SYSTEM_PROMPT = "Explore the frozen snapshot and publish one reproduction candidate."
MODULE_TEXT = "def parse(values):\n    return [values[0]]\n"
OUTPUT_LIMIT = 1024
BUSY = httpx.Response(503, json={"error": {"message": "busy"}})


def chat(message, finish):
    return httpx.Response(200, json={
        "id": "offline-id", "object": "chat.completion", "created": 1, "model": "offline",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


def text_reply(text="This behaviour is not reproducible from the frozen snapshot."):
    return chat({"role": "assistant", "content": text}, "stop")


def tool_reply(name=None, arguments=None, *, call_id="call-1", calls=None):
    calls = calls if calls is not None else [(name, arguments, call_id)]
    return chat({"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}
        for name, arguments, call_id in calls]}, "tool_calls")


def read_reply(path, call_id):
    return tool_reply("Read", {"file_path": str(path)}, call_id=call_id)


def write_payload(path="tests/test_repro.py", content="def test_repro():\n    assert False\n",
                  hypothesis="empty input"):
    return {"files": [{"path": path, "content": content, "role": "test"}], "hypothesis": hypothesis}


def contract(**overrides):
    values = dict(contract_id="contract-1", version=1, description_hash="description-hash",
                  trigger="parse([]) raises IndexError", expected="parse([]) returns []",
                  reported_actual="IndexError", observable_checks=("parse([]) == []",),
                  sources=(SourceRef("input/issue.md", "0" * 64, 1, 1, "issue"),),
                  assumptions=(), missing_information=())
    return IssueContract(**{**values, **overrides})


def find_ripgrep():
    candidates = (os.environ.get("REPROAGENT_RG_PATH"), shutil.which("rg"), shutil.which("rg.exe"))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def environment(tmp_path, projects, facts, answers=(), *, limits=None, handler=None, **contract_overrides):
    repo = projects.plain(tmp_path / "repo")
    (repo / "example" / "parser.py").write_text(MODULE_TEXT, encoding="utf-8", newline="")
    task = tmp_path / "task"
    store = TaskStore(task)
    workspace = Workspace(task, store)
    snapshot = workspace.freeze(TaskRequest(repo, task, repo / "issue.md", language=PythonPytestConfig()),
                                facts.context())
    project = ProjectView(snapshot)
    context = facts.context(limits=limits) if limits is not None else facts.context()
    gate = PhaseGate()
    service = CandidateService(project, workspace, context)
    service.bind_contract(contract(**contract_overrides))
    backend = SnapshotBackend(project, store, context, rg_path=find_ripgrep())
    ledger = EvidenceLedger(project, store)
    toolkit = build_toolkit(backend, ledger, service, gate, context)
    requests, answers = [], list(answers)

    def scripted(request):
        requests.append(json.loads(request.content))
        return answers.pop(0)

    factory = AgentScopeModelFactory(
        ModelConfig(base_url="https://offline.example/v1", model="offline", output_limit_field="max_tokens",
                    max_output_tokens=OUTPUT_LIMIT), store, transport=httpx.MockTransport(handler or scripted))
    model = factory.create(context, "exploration")
    runtime = AgentScopeRuntime(model, toolkit, gate, context, system_prompt=SYSTEM_PROMPT, store=store)
    return SimpleNamespace(repo=repo, task=task, store=store, workspace=workspace, snapshot=snapshot, project=project,
                           context=context, gate=gate, service=service, backend=backend, ledger=ledger,
                           toolkit=toolkit, factory=factory, model=model, runtime=runtime, requests=requests,
                           answers=answers, root=snapshot.root, module=snapshot.root / "example" / "parser.py")


@contextlib.contextmanager
def traced(tmp_path, projects, facts, *, answers=(), **kwargs):
    """An environment whose runtime is built while a recorder is installed.

    The runtime decides once, at construction, whether to put the trace middleware in
    front of its own, so the recorder has to be current before the environment exists.
    """
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, answers=answers, **kwargs)
        env.recorder = recorder
        yield env


def agent_context(env, feedback="", history=()):
    return AgentContext(contract=env.service.contract, project=env.service.project,
                        feedback=feedback, history=tuple(history))


def explore(env, feedback=""):
    return asyncio.run(env.runtime.explore(agent_context(env, feedback=feedback)))


def document(env, status="DONE"):
    return env.recorder.finish(task_id="task-1", status=status, main_duration=1.0)


def named(doc, name):
    return [entry for entry in doc["spans"] if entry["name"] == name]


def by_kind(doc, kind):
    return [entry for entry in doc["spans"] if entry["kind"] == kind]


def test_native_hooks_observe_without_changing_response(tmp_path, projects, facts):
    answers = [read_reply(None, "call-1"), text_reply()]
    untraced = environment(tmp_path / "plain", projects, facts, answers=[
        read_reply(None, "call-1"), text_reply()])
    answers[0] = read_reply(untraced.module, "call-1")

    with traced(tmp_path, projects, facts, answers=answers) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        result = explore(env)
        messages = env.runtime.messages
        doc = document(env)

    plain = explore(untraced)

    # The observation changed neither the domain result nor the conversation the task kept.
    assert result == plain
    assert len(messages) == len(untraced.runtime.messages)
    assert [entry["name"] for entry in named(doc, "sdk.model_round")]
    read = named(doc, "tool.Read")
    assert len(read) == 1 and read[0]["attributes"]["tool"] == "Read"
    assert read[0]["attributes"]["result_code"] == "SUCCESS"
    # The round closes before the agent acts, so a tool is a sibling of the model round
    # it followed -- not a child of it. Both hang off the phase span Task 3 will add.
    round_entry = named(doc, "sdk.model_round")[0]
    assert read[0]["parent_span_id"] == round_entry["parent_span_id"]


def test_outer_hook_sees_protocol_rejection(tmp_path, projects, facts):
    two_calls = tool_reply(calls=[("Read", {"file_path": "a.py"}, "call-1"),
                                  ("Read", {"file_path": "b.py"}, "call-2")])
    with traced(tmp_path, projects, facts, answers=[two_calls] * PROTOCOL_ATTEMPTS) as env:
        with pytest.raises(PhaseProtocolError):
            explore(env)
        doc = document(env, status="FAILED")

    rounds = named(doc, "sdk.model_round")
    # The domain middleware answers its own on_model_call without delegating, so the
    # outer hook can only see the rejection if it wraps the whole call.
    assert rounds and all(entry["attributes"].get("result_code") == "MULTIPLE_TOOL_CALLS"
                          for entry in rounds)
    assert all(entry["status"] == "error" for entry in rounds)
    # A rejected response runs nothing at all.
    assert not [entry for entry in doc["spans"] if entry["name"].startswith("tool.")]


def test_denial_is_not_tool_execution(tmp_path, projects, facts):
    """A tool the phase never registered is refused whole, before the SDK acts on it."""
    with traced(tmp_path, projects, facts,
                answers=[tool_reply("run_the_tests", {})] * PROTOCOL_ATTEMPTS) as env:
        with pytest.raises(PhaseProtocolError):
            explore(env)
        doc = document(env, status="FAILED")

    # No execution happened, and none is counted -- there was never a call to refuse.
    assert not [entry for entry in doc["spans"] if entry["name"].startswith("tool.")]
    assert doc["summary"]["tool_executions"] == 0
    codes = [entry["attributes"].get("result_code") for entry in named(doc, "sdk.model_round")]
    assert codes == ["UNKNOWN_TOOL"] * PROTOCOL_ATTEMPTS


def test_a_refused_permission_is_a_point_and_runs_nothing(tmp_path, projects, facts):
    """A refusal the engine itself makes is recorded as a decision, never an execution."""
    denial = PermissionDecision(behavior=PermissionBehavior.DENY, message="refused by a configured rule")

    async def refuse():
        async def next_handler(**_kwargs):
            return denial

        middleware = ExplorationMiddleware(facts.context(), PhaseGate(), None, allowed_tools=TOOL_NAMES)
        return await middleware.on_check_permission(
            agent=None, input_kwargs={"tool": SimpleNamespace(name="Read"), "tool_input": {}},
            next_handler=next_handler)

    with TraceRecorder() as recorder:
        assert asyncio.run(refuse()).behavior is PermissionBehavior.DENY
        doc = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)

    points = [entry for entry in by_kind(doc, "event") if entry["name"] == "permission"]
    assert [entry["attributes"]["result_code"] for entry in points] == ["DENIED"]
    # The decision is a point event with no duration, and no execution was recorded.
    assert points[0]["duration_seconds"] is None
    assert doc["summary"]["tool_executions"] == 0


def test_permission_and_execution_are_joined_by_call(tmp_path, projects, facts):
    """The two points are joined by the SDK's own call id, not by name or by adjacency.

    Six reads of the same file produce six identical-looking permission points and six
    identical-looking tool spans; only the call key says which decision admitted which
    execution, which is the whole question when one of them was refused.
    """
    with traced(tmp_path, projects, facts, answers=[read_reply(None, "call-1"), text_reply()]) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        explore(env)
        doc = document(env)

    allowed = [entry for entry in by_kind(doc, "event")
               if entry["name"] == "permission" and entry["attributes"].get("result_code") == "ALLOWED"]
    executions = named(doc, "tool.Read")

    # One admitted call, one execution: the permission point never counts as a second.
    assert len(allowed) == 1 and len(executions) == 1
    assert doc["summary"]["tool_executions"] == 1
    assert allowed[0]["attributes"]["tool"] == "Read"
    # ...and the two are the same call, by identity.
    key = allowed[0]["attributes"]["tool_call_key"]
    assert key and key == executions[0]["attributes"]["tool_call_key"]


def test_the_trace_records_the_budget_and_the_registered_tool_set(tmp_path, projects, facts):
    """"Why did it end" needs the budget it ended on, and the surface it could have used."""
    with traced(tmp_path, projects, facts, answers=[read_reply(None, "call-1"), text_reply()]) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        explore(env)
        doc = document(env)

    registered = [entry for entry in by_kind(doc, "event") if entry["name"] == "exploration.tools"]
    assert registered, "the phase records the surface it actually registered"
    attributes = registered[0]["attributes"]
    # The real toolkit, not the six the docstring happens to mention.
    assert set(attributes["tool_set"]) == set(TOOL_NAMES)
    # ...and what it had left, which is what says why it later had to stop.
    assert attributes["budget"]["steps_remaining"] > 0
    assert attributes["budget"]["seconds_remaining"] > 0


def test_repeated_reads_are_told_apart_by_their_call_key(tmp_path, projects, facts):
    answers = [read_reply(None, f"call-{index}") for index in range(3)] + [text_reply()]
    with traced(tmp_path, projects, facts, answers=answers) as env:
        for index in range(3):
            env.answers[index] = read_reply(env.module, f"call-{index + 1}")
        explore(env)
        doc = document(env)

    executions = named(doc, "tool.Read")
    keys = [entry["attributes"]["tool_call_key"] for entry in executions]

    # Same tool, same file, three different calls -- three different keys, so a renderer
    # can never pair a refusal with the wrong execution.
    assert len(executions) == 3
    assert len(set(keys)) == 3 and all(keys)


def test_reserved_denial_is_distinct_from_plain_denial(tmp_path, projects, facts):
    # Six readers leave exactly the reserved three steps, so the seventh read is refused
    # for a different reason than an unregistered name -- and that difference has to
    # survive into the trace, or the reserve becomes invisible exactly when it matters.
    answers = [read_reply(None, f"call-{index}") for index in range(7)] + [text_reply()]
    with traced(tmp_path, projects, facts, answers=answers, limits=BudgetLimits(agent_steps=9)) as env:
        for index in range(7):
            env.answers[index] = read_reply(env.module, f"call-{index + 1}")
        explore(env)
        doc = document(env)

    codes = [entry["attributes"].get("result_code") for entry in by_kind(doc, "event")
             if entry["name"] == "permission"]
    assert "ALLOWED" in codes
    assert "RESERVED_FOR_PUBLISHING" in codes
    assert "DENIED" not in codes


async def acting(middleware, tool_call, items):
    """Drive ``on_acting`` exactly as the SDK's own chain does."""
    forward = []

    async def next_handler(**_kwargs):
        for item in items:
            forward.append(item)
            yield item

    received = [item async for item in middleware.on_acting(
        agent=None, input_kwargs={"tool_call": tool_call}, next_handler=next_handler)]
    return forward, received


CALL = ToolCallBlock(id="call-1", name="write_candidate", input="{}")


def test_tool_stream_is_forwarded_once(tmp_path, projects, facts):
    chunk = ToolChunk(content=[TextBlock(text="working")])
    final = ToolResponse(content=[TextBlock(text="done")], state=ToolResultState.SUCCESS)

    with TraceRecorder() as recorder:
        middleware = TraceMiddleware(facts.context())
        forward, received = asyncio.run(acting(middleware, CALL, [chunk, final]))
        doc = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)

    # The same objects, in the same order, exactly once each: no copying, no reordering,
    # no second call to the handler.
    assert [id(item) for item in received] == [id(item) for item in forward]
    assert received == [chunk, final]
    span = named(doc, "tool.write_candidate")[0]
    assert span["attributes"]["result_code"] == "SUCCESS"


def test_published_candidate_survives_retirement(tmp_path, projects, facts):
    with traced(tmp_path, projects, facts,
                answers=[read_reply(None, "call-1"), tool_reply("write_candidate", write_payload(), call_id="call-2")]) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        result = explore(env)
        doc = document(env)

    assert result.kind == "candidate"
    published = named(doc, "tool.write_candidate")
    assert len(published) == 1
    # The runtime retires the SDK reply once the phase has its result; that teardown must
    # not rewrite a successful publication into a tool failure.
    assert published[0]["attributes"]["result_code"] == "SUCCESS"
    assert published[0]["status"] != "error"


def contents(doc, source):
    return [record for record in doc["contents"] if record["source"] == source]


def test_the_real_wire_request_is_captured(tmp_path, projects, facts):
    """The input captured is the request that was sent, not the message before formatting."""
    with traced(tmp_path, projects, facts,
                answers=[read_reply(None, "call-1"), text_reply()]) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        explore(env)
        doc = document(env)

    wires = contents(doc, "wire_request")
    assert len(wires) == len(env.requests), "one capture per HTTP attempt"

    bodies = [json.loads(record["text"]) for record in wires]
    # The provider's own request shape, not this product's.
    assert "messages" in bodies[0] and "model" in bodies[0]
    # The second request really carries the first tool's result, so the capture follows
    # the conversation rather than a snapshot of the first call.
    assert len(bodies[-1]["messages"]) > len(bodies[0]["messages"])


def test_a_rejected_response_is_captured_before_it_is_refused(tmp_path, projects, facts):
    two_calls = tool_reply(calls=[("Read", {"file_path": "a.py"}, "call-1"),
                                  ("Read", {"file_path": "b.py"}, "call-2")])
    with traced(tmp_path, projects, facts, answers=[two_calls] * PROTOCOL_ATTEMPTS) as env:
        with pytest.raises(PhaseProtocolError):
            explore(env)
        doc = document(env, status="FAILED")

    responses = contents(doc, "provider_response")
    # A response the phase refuses whole is exactly the one worth reading afterwards.
    assert responses
    assert any("tool_calls" in record["text"] and "Read" in record["text"] for record in responses)


def test_reasoning_is_kept_when_returned_and_marked_when_absent(tmp_path, projects, facts):
    with_reasoning = chat({"role": "assistant", "content": "done", "reasoning_content": "I checked the parser"},
                          "stop")
    without_reasoning = text_reply()

    with traced(tmp_path, projects, facts, answers=[with_reasoning]) as env:
        explore(env)
        present = contents(document(env), "provider_reasoning")[0]

    with traced(tmp_path / "plain", projects, facts, answers=[without_reasoning]) as env:
        explore(env)
        absent = contents(document(env), "provider_reasoning")[0]

    assert present["availability"] == "captured" and "I checked the parser" in present["text"]
    # A provider that returns no reasoning is normal, and is not reported as a fault.
    assert absent["availability"] == "not_returned"


def test_the_tool_input_and_result_are_captured_and_correlated(tmp_path, projects, facts):
    with traced(tmp_path, projects, facts,
                answers=[read_reply(None, "call-1"), text_reply()]) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        explore(env)
        doc = document(env)

    permission = next(entry for entry in by_kind(doc, "event") if entry["name"] == "permission")
    executed = named(doc, "tool.Read")[0]
    stored = {record["content_id"]: record for record in doc["contents"]}

    # The decision and the execution are the same call, by key.
    assert permission["attributes"]["tool_call_key"] == executed["attributes"]["tool_call_key"]
    # The arguments were captured on the permission point, the result on the execution.
    assert stored[permission["content_refs"][0]]["source"] == "sdk_tool_input"
    assert stored[executed["content_refs"][0]]["source"] == "sdk_tool_result"
    result = json.loads(stored[executed["content_refs"][0]]["text"])
    assert any("parse" in str(block) for block in result["content"])


def stable_requests(requests, root=None):
    """Request bodies with the two things that legitimately differ between runs removed.

    The seconds left on the task move between two runs, and each run works in its own
    tmp directory so its absolute paths differ.  What is left is the comparison that
    means something: the messages, the tools and the request options.
    """
    cleaned = []
    for body in requests:
        body = json.loads(json.dumps(body))
        for message in body.get("messages", []):
            content = message.get("content")
            if isinstance(content, str) and content.startswith("{"):
                try:
                    data = json.loads(content)
                except ValueError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("budget"), dict):
                    data["budget"].pop("seconds_remaining", None)
                    message["content"] = json.dumps(data, sort_keys=True)
        text = json.dumps(body, sort_keys=True)
        if root is not None:
            # A path inside a tool call's arguments is a JSON string within a JSON string,
            # so the same root appears with its backslashes escaped once and twice over.
            text_root = str(root)
            for form in (text_root, text_root.replace("\\", "/"),
                         text_root.replace("\\", "\\\\"), text_root.replace("\\", "\\\\\\\\")):
                text = text.replace(form, "<root>")
        cleaned.append(text)
    return cleaned


def test_capture_does_not_change_the_request_or_add_a_call(tmp_path, projects, facts):
    """The same fixed scenario, traced and not, must reach the provider identically."""
    scripted = [read_reply(None, "call-1"), text_reply()]

    plain = environment(tmp_path / "plain", projects, facts, answers=list(scripted))
    plain.answers[0] = read_reply(plain.module, "call-1")
    explore(plain)

    with traced(tmp_path / "traced", projects, facts, answers=list(scripted)) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        explore(env)

    assert len(env.requests) == len(plain.requests), "capture adds no request"
    assert stable_requests(env.requests, env.root) == stable_requests(plain.requests, plain.root), \
        "capture does not change what is sent"


def test_a_finished_phase_does_not_report_a_failed_model_round(tmp_path, projects, facts):
    """A phase ends by refusing the next call, and that ending is not a failure.

    Once ``write_candidate`` publishes, the gate is finished and the guard turns the
    next model call away.  The round that was turned away is how the phase ended
    cleanly -- recording it as an error would make every successful publication look
    like it had gone wrong.
    """
    with traced(tmp_path, projects, facts,
                answers=[read_reply(None, "call-1"),
                         tool_reply("write_candidate", write_payload(), call_id="call-2")]) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        result = explore(env)
        doc = document(env)

    assert result.kind == "candidate"
    rounds = named(doc, "sdk.model_round")
    assert rounds
    assert all(entry["status"] != "error" for entry in rounds), \
        [(entry["status"], entry["attributes"]) for entry in rounds]
    assert any(entry["attributes"].get("result_code") == "PHASE_ENDED" for entry in rounds)


def test_a_budget_stop_is_not_a_failed_model_round(tmp_path, projects, facts):
    """Running out of budget ends the phase; it is not a round that went wrong.

    A task stopped by its own budget is a normal, expected ending, and the round that
    was cut short must not be the one place that reads as a failure.
    """
    with traced(tmp_path, projects, facts, answers=[read_reply(None, "call-1")],
                limits=BudgetLimits(agent_steps=1)) as env:
        env.answers[0] = read_reply(env.module, "call-1")
        with pytest.raises(BudgetStopped):
            explore(env)
        doc = document(env, status="EXHAUSTED")

    rounds = named(doc, "sdk.model_round")
    assert rounds
    assert all(entry["status"] != "error" for entry in rounds), \
        [(entry["status"], entry["attributes"]) for entry in rounds]


def test_cancelled_tool_is_not_reported_success(tmp_path, projects, facts):
    final = ToolResponse(content=[TextBlock(text="done")], state=ToolResultState.SUCCESS)

    async def cancelled():
        async def next_handler(**_kwargs):
            yield final
            raise asyncio.CancelledError()

        middleware = TraceMiddleware(facts.context())
        with pytest.raises(asyncio.CancelledError):
            async for _item in middleware.on_acting(agent=None, input_kwargs={"tool_call": CALL},
                                                    next_handler=next_handler):
                pass

    with TraceRecorder() as recorder:
        asyncio.run(cancelled())
        doc = recorder.finish(task_id="task-1", status="FAILED", main_duration=1.0)

    span = named(doc, "tool.write_candidate")[0]
    # Whatever the tool reported, the call was cancelled: it is not a success.
    assert span["status"] == "cancelled"


def test_three_http_attempts_share_one_logical_call(tmp_path, projects, facts):
    answers = [BUSY, BUSY, text_reply()]

    with traced(tmp_path, projects, facts, answers=answers) as env:
        explore(env)
        doc = document(env)

    logical = named(doc, "model.logical")
    attempts = named(doc, "model.http_attempt")

    # One logical call spent a step; the retries did not spend two more.
    assert len(logical) == 1 and len(attempts) == 3
    assert doc["summary"]["logical_calls"] == 1
    assert doc["summary"]["http_attempts"] == 3
    # Every attempt is charged to that one logical call, and only the wire leaves count.
    assert {entry["parent_span_id"] for entry in attempts} == {logical[0]["span_id"]}


def test_disabled_tracing_leaves_the_middleware_chain_alone(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, answers=[text_reply()])

    assert [type(middleware).__name__ for middleware in env.runtime._agent._model_call_middlewares] \
        == ["ExplorationMiddleware"]
