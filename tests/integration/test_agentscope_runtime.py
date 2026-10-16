"""One exploration phase as an SDK agent: bounded, terminable, and keeping its history.

The runtime runs a real ``agentscope.Agent`` over the phase toolkit and a real
``AgentScopeModelFactory`` model behind a mock HTTP transport, so every assertion here is
about wire requests and SDK behaviour rather than about ``max_iters``: the twentieth
decision is the last one that may reach the provider, a rejected response never runs a
tool, the SDK's own grace and compression cannot buy an extra call, and a finished phase
resumes with the very history it built.
"""
import asyncio
import json
import math
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

import httpx
import pytest

from agentscope.agent import Agent, ReActConfig
from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from pydantic import BaseModel

from reproagent.adapters.agentscope.evidence import EvidenceLedger
from reproagent.adapters.agentscope.middleware import (
    PROTOCOL_ATTEMPTS,
    ExplorationMiddleware,
    PhaseProtocolError,
)
from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime, sdk_end_reason
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
from reproagent.core.phase import PhaseGate, PhaseResult
from reproagent.core.serialization import canonical_hash
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


def arguments_text(value):
    return value if isinstance(value, str) else json.dumps(value)


def text_reply(text="This behaviour is not reproducible from the frozen snapshot."):
    return chat({"role": "assistant", "content": text}, "stop")


def tool_reply(name=None, arguments=None, *, call_id="call-1", calls=None):
    """One assistant answer with *calls*, each ``(name, arguments, call_id)``."""
    calls = calls if calls is not None else [(name, arguments, call_id)]
    return chat({"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": arguments_text(arguments)}} for name, arguments, call_id in calls]},
        "tool_calls")


def read_reply(path, call_id):
    return tool_reply("Read", {"file_path": str(path)}, call_id=call_id)


def write_payload(path="tests/test_repro.py", content="def test_repro():\n    assert False\n", hypothesis="empty input"):
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


def environment(tmp_path, projects, facts, answers=None, *, limits=None, handler=None, **contract_overrides):
    """One frozen task with the phase toolkit, a mock SDK transport and a runtime."""
    repo = projects.plain(tmp_path / "repo")
    (repo / "example" / "parser.py").write_text(MODULE_TEXT, encoding="utf-8", newline="")
    task = tmp_path / "task"
    store = TaskStore(task)
    workspace = Workspace(task, store)
    snapshot = workspace.freeze(TaskRequest(repo, task, repo / "issue.md", language=PythonPytestConfig()), facts.context())
    project = ProjectView(snapshot)
    context = facts.context(limits=limits) if limits is not None else facts.context()
    gate = PhaseGate()
    service = CandidateService(project, workspace, context)
    service.bind_contract(contract(**contract_overrides))
    backend = SnapshotBackend(project, store, context, rg_path=find_ripgrep())
    ledger = EvidenceLedger(project, store)
    toolkit = build_toolkit(backend, ledger, service, gate, context)
    requests, answers = [], list(answers or ())

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


def agent_context(env, feedback="", history=()):
    return AgentContext(contract=env.service.contract, project=env.service.project,
                        feedback=feedback, history=tuple(history))


def explore(env, feedback=""):
    return asyncio.run(env.runtime.explore(agent_context(env, feedback=feedback)))


def candidate_ids(env):
    directory = env.task / "candidates"
    return sorted(item.name for item in directory.iterdir()) if directory.exists() else []


def events(env, kind=None):
    stored, errors = env.store.read_events()
    assert not errors
    return [event.payload for event in stored if kind is None or event.kind == kind]


def wire_progress(request):
    notices = []
    for message in request['messages']:
        if message.get('role') != 'system':
            continue
        try:
            content = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if isinstance(content, dict) and {'budget', 'facts', 'guidance'} <= content.keys():
            notices.append(content)
    assert len(notices) == 1, 'each actual request must have exactly one fresh progress notice'
    return notices[0]


def test_budget_progress_refreshes_on_each_wire_request(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([read_reply(env.module, f'call-{i}') for i in range(19)])
    env.answers.append(tool_reply('write_candidate', write_payload(), call_id='publish'))
    assert explore(env).kind == 'candidate'
    assert len(env.requests) == 20 and env.context.budget.steps_used == 20
    views = [wire_progress(request) for request in env.requests]
    for index, mode, used, left in [(14, 'normal', 14, 6), (15, 'converging', 15, 5),
                                    (19, 'converging', 19, 1)]:
        assert views[index]['budget']['mode'] == mode
        assert views[index]['budget']['used_steps'] == used
        assert views[index]['budget']['remaining_steps'] == left
    assert all(bool(v['guidance']) == (i >= 12) for i, v in enumerate(views))
    assert not any(getattr(m, 'name', '') == 'exploration-progress' for m in env.runtime.messages)
    with pytest.raises(BudgetStopped):
        explore(env)
    assert len(env.requests) == 20


def test_budget_progress_http_retry_keeps_one_snapshot(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1), answers=[
        httpx.Response(503, json={'error': {'message': 'busy'}}),
        httpx.Response(503, json={'error': {'message': 'busy'}}),
        tool_reply('request_information', {'question': 'Need evidence'})])
    assert explore(env).kind == 'request_information'
    assert len(env.requests) == 3 and env.context.budget.steps_used == 1
    views = [wire_progress(request) for request in env.requests]
    assert all(view == views[0] for view in views)
    assert views[0]['budget']['remaining_steps'] == 1


def test_budget_progress_protocol_correction_spends_new_step(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=4), answers=[
        tool_reply(calls=[('Read', {'file_path': 'a.py'}, 'a'), ('Read', {'file_path': 'b.py'}, 'b')]),
        tool_reply('request_information', {'question': 'Need evidence'})])
    env.context.budget.take_step()
    env.context.budget.take_step()
    assert explore(env).kind == 'request_information'
    assert [wire_progress(r)['budget']['mode'] for r in env.requests] == ['normal', 'converging']
    assert env.context.budget.steps_used == 4


def test_budget_progress_contract_revision_does_not_reset(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    source = next(item for item in env.snapshot.files if item.path == 'example/parser.py')
    env.answers.extend([
        read_reply(env.module, 'source'),
        tool_reply('revise_contract', {'source_refs': [{'path': source.path, 'content_hash': source.content_hash,
            'start_line': 1, 'end_line': 2}], 'reason': 'Need actual behaviour'}),
        tool_reply('request_information', {'question': 'Need evidence'})])
    for _ in range(13):
        env.context.budget.take_step()

    async def execute():
        try:
            assert (await env.runtime.explore(replace(agent_context(env),original_execution_count=1))).kind == 'revise_contract'
            env.service.bind_contract(replace(env.service.contract, version=2))
            return await env.runtime.explore(replace(agent_context(env),original_execution_count=1))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(execute()).kind == 'request_information'
    views = [wire_progress(r) for r in env.requests]
    assert [v['budget']['used_steps'] for v in views] == [13, 14, 15]
    assert [v['facts']['contract_version'] for v in views] == [1, 1, 2]
    assert views[-1]['budget']['mode'] == 'converging'


def test_budget_progress_is_ephemeral_and_coexists_with_browse(tmp_path, projects, facts):
    from tests.integration.test_agentscope_experience_catalog import catalog_environment
    env, _ = catalog_environment(tmp_path, projects, facts, [
        tool_reply('list_experiences', {'category': 'model'}, call_id='catalog'),
        tool_reply('request_information', {'question': 'Need evidence'})])
    assert explore(env).kind == 'request_information'
    assert wire_progress(env.requests[1])['budget']['used_steps'] == 1
    catalog = next(m for m in env.requests[1]['messages'] if m.get('tool_call_id') == 'catalog')
    assert json.loads(catalog['content'])['items']
    assert 'Current experience browse state: satisfied' in json.dumps(env.requests[1]['messages'])
    assert not any(getattr(m, 'name', '') == 'exploration-progress' for m in env.runtime.messages)


@pytest.mark.parametrize('steps', [1, 2, 3])
def test_budget_progress_small_limits_can_finish_without_extra_call(tmp_path, projects, facts, steps):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=steps), answers=[
        tool_reply('request_information', {'question': 'Need evidence'})])
    assert explore(env).kind == 'request_information'
    assert len(env.requests) == 1
    assert wire_progress(env.requests[0])['budget']['remaining_steps'] == steps


@pytest.mark.parametrize('stop', ['cancel', 'deadline', 'exhausted'])
def test_budget_progress_stopped_calls_do_not_reach_http(tmp_path, projects, facts, stop):
    env = environment(tmp_path, projects, facts, answers=[text_reply()])
    if stop == 'cancel':
        env.context.cancel_event.set()
    elif stop == 'deadline':
        env.context.budget.deadline = env.context.budget.clock() - 1
    else:
        for _ in range(20):
            env.context.budget.take_step()
    with pytest.raises(BudgetStopped) as error:
        explore(env)
    assert error.value.reason == ('CANCELLED' if stop == 'cancel' else 'EXHAUSTED')
    assert env.requests == []


@pytest.mark.parametrize('overflow', [False, True])
def test_budget_progress_counts_final_messages_before_http(tmp_path, projects, facts, monkeypatch, overflow):
    env = environment(tmp_path, projects, facts, answers=[
        tool_reply('request_information', {'question': 'Need evidence'})])
    threshold = env.runtime._agent.context_config.trigger_ratio * env.model.context_size
    async def count_tokens(messages, tools=None, **kwargs):
        # Simulate the token boundary, not the SDK message/HTTP assembly.
        has_progress = any(getattr(m, 'name', '') == 'exploration-progress' for m in messages)
        return threshold if has_progress and overflow else threshold - 1
    monkeypatch.setattr(env.model, 'count_tokens', count_tokens)
    if overflow:
        with pytest.raises(BudgetStopped) as error:
            explore(env)
        assert error.value.reason == 'NEEDS_INFORMATION'
        assert env.requests == [] and env.context.budget.steps_used == 0
    else:
        assert explore(env).kind == 'request_information'
        assert len(env.requests) == 1 and env.context.budget.steps_used == 1
    assert not any(getattr(m, 'name', '') == 'exploration-progress' for m in env.runtime.messages)


@pytest.mark.parametrize('stop', ['finished', 'cancel', 'deadline', 'exhausted'])
def test_budget_progress_stop_during_count_outranks_capacity(tmp_path, projects, facts, monkeypatch, stop):
    env = environment(tmp_path, projects, facts, answers=[text_reply()])
    async def count_tokens(messages, tools=None, **kwargs):
        if not any(getattr(m, 'name', '') == 'exploration-progress' for m in messages):
            return 1
        if stop == 'finished':
            env.gate.finish(PhaseResult('request_information', question='Need evidence'))
        elif stop == 'cancel':
            env.context.cancel_event.set()
        elif stop == 'deadline':
            env.context.budget.deadline = env.context.budget.clock() - 1
        else:
            for _ in range(20):
                env.context.budget.take_step()
        return env.model.context_size
    monkeypatch.setattr(env.model, 'count_tokens', count_tokens)
    if stop == 'finished':
        assert explore(env).kind == 'request_information'
    else:
        with pytest.raises(BudgetStopped) as error:
            explore(env)
        assert error.value.reason == ('CANCELLED' if stop == 'cancel' else 'EXHAUSTED')
    assert env.requests == []


def pending_tasks():
    """Every task the running loop still has besides the one asking."""
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()}


async def until(predicate, timeout=10.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def test_twentieth_decision_may_finish_but_twenty_first_never_hits_http(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([read_reply(env.module, f"call-{index}") for index in range(19)])
    env.answers.append(tool_reply("write_candidate", write_payload(), call_id="call-20"))
    result = explore(env)
    assert result.kind == "candidate"
    # The twentieth decision published a real, immutable candidate.
    assert env.store.load_record("candidates", result.candidate_id).candidate_id == result.candidate_id
    assert candidate_ids(env) == [result.candidate_id]
    # Twenty logical requests and no twenty-first: no grace call, no final summary.
    assert len(env.requests) == 20
    assert env.context.budget.steps_used == 20
    assert not env.context.cancel_event.is_set()
    # Every request carried the configured ceiling, not just the attempt record.
    assert {request["max_tokens"] for request in env.requests} == {OUTPUT_LIMIT}
    assert [payload["steps_remaining"] for payload in events(env, "exploration.step")] == [
        remaining for remaining in range(19, -1, -1)]

    # An invalid response spends the step it cost; the correction round spends its own.
    corrected = environment(tmp_path / "corrected", projects, facts)
    corrected.answers.extend([
        tool_reply(calls=[("Glob", {"pattern": "**/*.py", "path": str(corrected.root)}, "call-1"),
                          ("Glob", {"pattern": "**/*.py", "path": str(corrected.root)}, "call-2")]),
        tool_reply("write_candidate", write_payload(), call_id="call-3")])
    assert explore(corrected).kind == "candidate"
    assert len(corrected.requests) == 2 and corrected.context.budget.steps_used == 2

    # A network retry is another HTTP attempt of the same logical step, never a new step.
    retried = environment(tmp_path / "retried", projects, facts)
    retried.answers.extend([BUSY, BUSY, tool_reply("write_candidate", write_payload())])
    assert explore(retried).kind == "candidate"
    assert len(retried.requests) == 3 and retried.context.budget.steps_used == 1


def test_multiple_or_unknown_tools_have_zero_side_effects(tmp_path, projects, facts):
    # Two calls in one response are refused whole: the first is never run either.
    env = environment(tmp_path, projects, facts)
    env.answers.extend([
        tool_reply(calls=[("write_candidate", write_payload(path="tests/test_first.py"), "call-1"),
                          ("write_candidate", write_payload(path="tests/test_second.py"), "call-2")]),
        tool_reply("write_candidate", write_payload(path="tests/test_third.py"), call_id="call-3")])
    result = explore(env)
    assert result.kind == "candidate" and len(env.requests) == 2
    assert candidate_ids(env) == [result.candidate_id]
    stored = env.store.load_record("candidates", result.candidate_id)
    assert (stored.storage_root / "tests" / "test_third.py").exists()
    assert [payload["result_code"] for payload in events(env, "exploration.protocol_error")] == ["MULTIPLE_TOOL_CALLS"]

    # An unknown tool is refused before there is any execution path for it to take, and the
    # rejected arguments never enter the conversation.
    unknown = environment(tmp_path / "unknown", projects, facts)
    unknown.answers.extend([tool_reply("Bash", {"command": "rm -rf /"}, call_id="call-1"), text_reply()])
    assert explore(unknown).kind == "no_candidate"
    assert len(unknown.requests) == 2
    assert [payload["result_code"] for payload in events(unknown, "exploration.protocol_error")] == ["UNKNOWN_TOOL"]
    history = unknown.runtime.messages
    assert not [block for message in history for block in message.get_content_blocks("tool_call")]
    assert not [block for message in history for block in message.get_content_blocks("tool_result")]
    assert "rm -rf" not in str(history)
    # Neither the rejected arguments nor the provider text enters an audit event.
    assert "rm -rf" not in str(events(unknown)) and "not reproducible" not in str(events(unknown))

    # Arguments that are not one complete JSON object are refused the same way.
    malformed = environment(tmp_path / "malformed", projects, facts)
    malformed.answers.extend([tool_reply("Read", '{"file_path": '),
                              tool_reply("request_information", {"question": "which parser?"}, call_id="call-2")])
    assert explore(malformed).kind == "request_information"
    assert [payload["result_code"] for payload in events(malformed, "exploration.protocol_error")] == ["INVALID_TOOL_INPUT"]
    assert len(malformed.requests) == 2

    # Protocol correction is bounded: the fourth answer is never asked for.
    stubborn = environment(tmp_path / "stubborn", projects, facts)
    stubborn.answers.extend([tool_reply("Write", {"path": "x"}) for _ in range(PROTOCOL_ATTEMPTS)] + [text_reply()])
    with pytest.raises(PhaseProtocolError) as failure:
        explore(stubborn)
    assert failure.value.code == "UNKNOWN_TOOL"
    assert len(stubborn.requests) == PROTOCOL_ATTEMPTS == 3
    assert stubborn.gate.result is None and candidate_ids(stubborn) == []

    # A plain answer ends the phase; it does not start a correction round.
    plain = environment(tmp_path / "plain", projects, facts)
    plain.answers.extend([text_reply(), text_reply()])
    result = explore(plain)
    assert result.kind == "no_candidate" and result.reason and len(plain.requests) == 1
    # The business kind and the SDK's own end reason are recorded separately, never merged.
    finished = events(plain, "exploration.finished")
    assert [(payload["action"], payload["result_code"], payload["sdk_end_reason"]) for payload in finished] == \
        [("no_candidate", "no_candidate", "completed")]


def test_sdk_grace_summary_and_compression_cannot_add_calls(tmp_path, projects, facts):
    # The SDK's own extra iteration at max_iters (the forced final summary) is a model call
    # like any other, so the step budget stops it before it reaches the wire.
    stopped = environment(tmp_path / "one", projects, facts, limits=BudgetLimits(agent_steps=1))
    stopped.answers.extend([read_reply(stopped.module, "call-1"), text_reply()])
    with pytest.raises(BudgetStopped) as failure:
        explore(stopped)
    assert failure.value.reason == "EXHAUSTED"
    assert len(stopped.requests) == 1

    # Structured-output grace: the SDK may reason for max_iters + grace iterations, and every
    # one of them is a model call the guard charges.
    class Summary(BaseModel):
        answer: str = ""

    grace = environment(tmp_path / "grace", projects, facts, limits=BudgetLimits(agent_steps=2))
    grace.answers.extend(text_reply() for _ in range(9))
    middleware = ExplorationMiddleware(grace.context, grace.gate, grace.store)
    grace.model.bind(middleware.before_model_call)
    agent = Agent(name="grace", system_prompt=SYSTEM_PROMPT, model=grace.model, toolkit=grace.toolkit,
                  middlewares=[middleware], react_config=ReActConfig(max_iters=1, structured_output_grace_iters=5))
    with pytest.raises(BudgetStopped) as failure:
        asyncio.run(agent.reply(_user("answer in the required schema"), structured_schema=Summary))
    assert failure.value.reason == "EXHAUSTED"
    assert len(grace.requests) == 2

    # The SDK's own end reasons are distinguishable in the record: an agent that runs its
    # grace out and stops on its own reports exceed_max_iters, not completed.
    ended = environment(tmp_path / "ended", projects, facts, limits=BudgetLimits(agent_steps=10))
    ended.answers.extend(text_reply() for _ in range(9))
    middleware = ExplorationMiddleware(ended.context, ended.gate, ended.store)
    ended.model.bind(middleware.before_model_call)
    agent = Agent(name="ended", system_prompt=SYSTEM_PROMPT, model=ended.model, toolkit=ended.toolkit,
                  middlewares=[middleware], react_config=ReActConfig(max_iters=1, structured_output_grace_iters=5))
    reply = asyncio.run(agent.reply(_user("answer in the required schema"), structured_schema=Summary))
    assert sdk_end_reason(reply) == "exceed_max_iters" and sdk_end_reason(reply) != "completed"
    assert ended.context.budget.steps_used == 6

    # Automatic compression is stopped explicitly: the message the phase sent stays whole,
    # source references included, and no exploration request is made.
    compressed = environment(tmp_path / "compressed", projects, facts)
    compressed.model.context_size = 64
    compressed.answers.append(text_reply())
    with pytest.raises(BudgetStopped) as failure:
        explore(compressed)
    assert failure.value.reason == "NEEDS_INFORMATION"
    assert "compress" in str(failure.value)
    assert compressed.requests == []
    history = compressed.runtime.messages
    assert len(history) == 1
    assert "input/issue.md" in str(history[0]) and "contract-1" in str(history[0])

    # A contract that cannot fit the configured context budget is a diagnostic too: no
    # source reference is dropped to make it fit, and no request is made.
    cramped = environment(tmp_path / "cramped", projects, facts, limits=BudgetLimits(tool_response_bytes=64))
    with pytest.raises(BudgetStopped) as failure:
        explore(cramped)
    assert failure.value.reason == "NEEDS_INFORMATION"
    assert cramped.requests == [] and cramped.runtime.messages == ()


def _user(text):
    from agentscope.message import UserMsg

    return UserMsg(name="user", content=text)


def measure_threshold(env):
    """Put the model's compression threshold just above the phase's first reasoning step.

    Returns the first step's own input size, counted exactly the way the compression hook
    counts it: the round's message in the context, plus the system prompt and tool schemas.
    The candidate's own arguments are what pushes the *next* step over the threshold.
    """
    from agentscope.message import SystemMsg
    from reproagent.core.exploration_progress import step_budget_view
    from reproagent.adapters.agentscope.progress import render_progress
    phase = agent_context(env)
    env.runtime._agent.state.context.append(env.runtime._message(phase))
    env.runtime._agent.state.context.append(SystemMsg(name='exploration-progress', content=render_progress(
        step_budget_view(env.context.budget.limits.agent_steps, env.context.budget.steps_used), phase)))
    try:
        first = asyncio.run(env.runtime.middleware._estimated_tokens(env.runtime._agent))
    finally:
        env.runtime._agent.state.context.clear()
    env.model.context_size = math.ceil(first / 0.8) + 8
    return first


def test_a_published_candidate_survives_the_compression_threshold(tmp_path, projects, facts):
    """A threshold stop must not overtake a phase that already has its result.

    The SDK asks to compress first thing in every reasoning step -- i.e. immediately after a
    domain tool ran, when that tool's own arguments (a candidate's files, up to the tool
    response budget) are already in the context.  The phase below is placed exactly there:
    its first step fits under the threshold and the step after the candidate crosses it.
    """
    published = environment(tmp_path / "published", projects, facts)
    published.answers.append(tool_reply("write_candidate", write_payload(content="x" * 24000), call_id="call-1"))
    first = measure_threshold(published)
    result = explore(published)
    assert result.kind == "candidate"
    assert published.store.load_record("candidates", result.candidate_id).candidate_id == result.candidate_id
    assert len(published.requests) == 1                      # the next model call never happens
    threshold = 0.8 * published.model.context_size
    after = asyncio.run(published.runtime.middleware._estimated_tokens(published.runtime._agent))
    assert first < threshold <= after                        # ... but the threshold was crossed

    # The very same step with a refused publication leaves the phase open, and there the
    # threshold really does stop it: that is the check the run above did not take.
    refused = environment(tmp_path / "refused", projects, facts)
    refused.answers.append(tool_reply(
        "write_candidate", write_payload(path="outside/test_repro.py", content="x" * 24000), call_id="call-1"))
    measure_threshold(refused)
    with pytest.raises(BudgetStopped) as failure:
        explore(refused)
    assert failure.value.reason == "NEEDS_INFORMATION" and "compress" in str(failure.value)


def test_a_finished_phase_answers_with_its_result_whatever_stopped_the_agent(tmp_path, projects, facts):
    """The precedence, decided once: the gate's result outranks a task stop that follows it.

    A cancel or an expired budget that arrives after a domain tool published is a stop of the
    *agent*, not of the phase, so the phase's caller still gets the result; the cancel event
    stays set, so the controller runs its cancellation path either way.  The stop is raised
    through the SDK's interface because no product call can be made to race the publish.
    """

    class _Stopping:
        def __init__(self, gate, failure):
            self.gate, self.failure = gate, failure
            self.react_config, self.state = SimpleNamespace(max_iters=0), SimpleNamespace(context=[])
            self.started = asyncio.Event()

        async def reply(self, message):
            self.started.set()
            await self.gate.event.wait()                 # the domain tool published first
            raise self.failure                           # ... and then the task was stopped

    async def scenario():
        for failure in (BudgetStopped("CANCELLED"), BudgetStopped("EXHAUSTED", "task time limit reached")):
            env = environment(tmp_path / failure.reason, projects, facts)
            env.runtime._agent = _Stopping(env.gate, failure)
            running = asyncio.ensure_future(env.runtime.explore(agent_context(env)))
            await until(env.runtime._agent.started.is_set)
            env.gate.finish(PhaseResult("candidate", candidate_id="candidate-1"))
            assert await running == PhaseResult("candidate", candidate_id="candidate-1")
            assert pending_tasks() == set()

    asyncio.run(scenario())


def test_phase_finish_resumes_history_without_user_cancel(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([read_reply(env.module, "call-1"), tool_reply("write_candidate", write_payload(), call_id="call-2")])
    assert explore(env).kind == "candidate"
    assert len(env.requests) == 2
    # A finished phase is not a user cancel, and it spent no extra request.
    assert not env.context.cancel_event.is_set()
    # The result is the phase's own; how the SDK reply ended is a separate field.
    published = events(env, "exploration.finished")[0]
    assert (published["result_code"], published["sdk_end_reason"]) == ("candidate", "phase_ended")

    # The next phase of the same task pays for the first phase's tool call and result out of
    # the same history, and sees the verifier's feedback as the new input.
    feedback = "the candidate passed on the original version, so it does not reproduce the bug"
    env.answers.append(tool_reply("request_information", {"question": "which parser?"}, call_id="call-3"))
    assert explore(env, feedback=feedback) == PhaseResult("request_information", question="which parser?")
    assert len(env.requests) == 3
    resumed = env.requests[2]["messages"]
    assert any(message.get("role") == "tool" and "parser.py" in json.dumps(message) for message in resumed)
    assert any(message.get("tool_calls") for message in resumed)
    assert any(feedback in json.dumps(message) for message in resumed)
    history = env.runtime.messages
    assert [block for message in history for block in message.get_content_blocks("tool_result")]

    # A second task never sees the first task's history.
    other = environment(tmp_path / "other", projects, facts)
    other.answers.append(text_reply())
    assert explore(other).kind == "no_candidate"
    assert feedback not in json.dumps(other.requests[0]["messages"])
    assert not any(message.get("role") == "tool" for message in other.requests[0]["messages"])


def test_a_cancelled_task_stops_the_run_and_leaves_nothing_behind(tmp_path, projects, facts):
    async def scenario():
        seen, release = [], asyncio.Event()

        async def held(request):
            seen.append(json.loads(request.content))
            await release.wait()
            return text_reply()

        env = environment(tmp_path, projects, facts, handler=held)
        running = asyncio.ensure_future(env.runtime.explore(agent_context(env)))
        await until(lambda: seen)
        env.context.cancel_event.set()
        release.set()
        with pytest.raises(BudgetStopped) as failure:
            await running
        assert failure.value.reason == "CANCELLED"
        assert len(seen) == 1 and env.gate.result is None
        await env.runtime.aclose()
        # Neither the SDK reply nor the watcher it was raced against survives the run.
        assert pending_tasks() == set()

    asyncio.run(scenario())


class _Stalled:
    """An SDK reply that never comes back on its own, driven through the same interface.

    Every call the product makes is cancel-aware, so a real reply cannot be made to hang
    from the outside; this stand-in is what makes the runtime's own bound observable.  It
    swallows the cancellation the way the SDK does by default, so the phase's outcome can
    only come from the phase.
    """

    def __init__(self, gate):
        self.gate = gate
        self.react_config = SimpleNamespace(max_iters=0)
        self.state = SimpleNamespace(context=[])
        self.started, self.cancelled, self.finished = asyncio.Event(), False, False

    async def reply(self, message):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
        finally:
            self.finished = True
        return "the reply was stopped"


def test_a_stalled_reply_is_cancelled_and_awaited(tmp_path, projects, facts):
    async def scenario():
        stopped = environment(tmp_path / "stopped", projects, facts,
                              limits=BudgetLimits(finalize_timeout_seconds=0.1))
        stand_in = _Stalled(stopped.gate)
        stopped.runtime._agent = stand_in
        running = asyncio.ensure_future(stopped.runtime.explore(agent_context(stopped)))
        await until(stand_in.started.is_set)
        stopped.gate.finish(PhaseResult("request_information", question="which parser?"))
        # The phase's own result decides, not the way the stalled reply ended.
        assert await running == PhaseResult("request_information", question="which parser?")
        assert stand_in.cancelled and stand_in.finished and pending_tasks() == set()

        # The same stalled reply with the user's cancel and no phase result is a cancel.
        cancelled = environment(tmp_path / "cancelled", projects, facts,
                                limits=BudgetLimits(finalize_timeout_seconds=0.1))
        stalled = _Stalled(cancelled.gate)
        cancelled.runtime._agent = stalled
        running = asyncio.ensure_future(cancelled.runtime.explore(agent_context(cancelled)))
        await until(stalled.started.is_set)
        cancelled.context.cancel_event.set()
        with pytest.raises(BudgetStopped) as failure:
            await running
        assert failure.value.reason == "CANCELLED"
        assert stalled.cancelled and stalled.finished and pending_tasks() == set()
        await cancelled.runtime.aclose()

    asyncio.run(scenario())


class _Stub:
    """A tool outside the phase surface, enough for the permission layer to decide on."""

    def __init__(self, name):
        self.name, self.is_read_only, self.is_external_tool = name, False, False


def test_the_permission_layer_allows_only_the_phase_tools(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    middleware = env.runtime.middleware

    def check(name, tool_input, decision):
        async def next_handler(**kwargs):
            return PermissionDecision(behavior=decision, message="the engine's own answer")

        return asyncio.run(middleware.on_check_permission(
            agent=None, input_kwargs={"tool": _Stub(name), "tool_input": tool_input}, next_handler=next_handler))

    # A tool outside the phase is denied without the engine being consulted at all.
    consulted = []

    async def never(**kwargs):
        consulted.append(True)
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="")

    outside = asyncio.run(middleware.on_check_permission(
        agent=None, input_kwargs={"tool": _Stub("Bash"), "tool_input": {"command": "rm -rf /"}}, next_handler=never))
    assert outside.behavior is PermissionBehavior.DENY and consulted == []

    # A phase tool is the engine's call, and an engine answer that would park the phase on a
    # user is turned into a refusal: this phase has no user to ask.
    assert check("Read", {"file_path": "a.py"}, PermissionBehavior.ALLOW).behavior is PermissionBehavior.ALLOW
    assert check("write_candidate", write_payload(), PermissionBehavior.ASK).behavior is PermissionBehavior.DENY
    assert tuple(TOOL_NAMES) == ("Read", "Grep", "Glob", "write_candidate", "revise_contract", "request_information")
    actions = events(env, "exploration.action")
    assert [payload["result_code"] for payload in actions] == ["DENIED", "ALLOWED", "DENIED"]
    assert actions[1]["action"] == "Read" and actions[1]["arguments_hash"] == canonical_hash({"file_path": "a.py"})
    assert all(payload["phase"] == "EXPLORATION" and "steps_remaining" in payload for payload in actions)
    assert {payload["components"]["agentscope"] for payload in actions} == {"2.0.9"}


def test_the_runtime_is_the_exploration_port(tmp_path, projects, facts):
    from reproagent.core.ports import ExplorationRuntime

    env = environment(tmp_path, projects, facts)
    assert isinstance(env.runtime, ExplorationRuntime)
    assert asyncio.run(env.runtime.aclose()) is None


@pytest.mark.parametrize('invalid_hash', [False, True], ids=['over-budget', 'hash-mismatch'])
def test_original_issue_cannot_be_dropped_or_read_with_wrong_hash(tmp_path, projects, facts, invalid_hash):
    from reproagent.core.models import IssueDescription
    from reproagent.core.serialization import bytes_hash
    env = environment(tmp_path, projects, facts, answers=[text_reply()])
    text = 'fact' if invalid_hash else 'X' * 32768
    issue = IssueDescription(text, '0' * 64 if invalid_hash else bytes_hash(text.encode()))
    phase = replace(agent_context(env), issue=issue)
    with pytest.raises(BudgetStopped) as stopped:
        asyncio.run(env.runtime.explore(phase))
    assert stopped.value.reason == 'NEEDS_INFORMATION'
    assert not env.requests and env.context.budget.steps_used == 0


def test_original_issue_is_kept_when_contract_changes_between_phases(tmp_path, projects, facts):
    from reproagent.core.models import IssueDescription
    from reproagent.core.serialization import bytes_hash
    env = environment(tmp_path, projects, facts, answers=[text_reply(), text_reply()])
    issue = IssueDescription('return an error value, do not raise', bytes_hash(b'return an error value, do not raise'))
    first = replace(agent_context(env), issue=issue)
    second = replace(first, contract=replace(first.contract, version=2))

    async def run():
        await env.runtime.explore(first)
        await env.runtime.explore(second)
        await env.runtime.aclose()
        await env.factory.aclose()
    asyncio.run(run())
    second_input = next(json.loads(message['content']) for message in reversed(env.requests[-1]['messages'])
                        if message.get('role') == 'user' and '"contract"' in message.get('content', '')
                        and '"issue"' in message.get('content', ''))
    assert second_input['issue']['text'] == issue.text
    assert second_input['contract']['version'] == 2


def test_the_last_steps_are_reserved_for_publishing(tmp_path, projects, facts):
    """Reads stop when the budget is nearly spent, so the phase can still publish.

    The pre-SDK action loop kept a reserve for writing and submitting; the migration
    removed that loop and the reserve went with it, so a large repository could spend every
    decision reading -- 20 of 20 in the Sphinx-8801 fixed-point run -- and never publish a
    candidate. A grounded contract now loses its readers for the last steps, which stay
    open for one publish and its protocol corrections. A contract that still misses facts
    keeps its readers while there is room, because reading is how those facts are found --
    but not for the last steps. That phase has to end too, with a revision request or a
    report of what is missing; reading it to the last step produces no phase result at
    all, which is what five of the seven holdout cases did at zero or one step remaining.
    """
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=6),
                      answers=[tool_reply("request_information", {"question": "which behaviour is expected"})])
    middleware = env.runtime.middleware

    def check(name, tool_input):
        async def next_handler(**kwargs):
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="")

        return asyncio.run(middleware.on_check_permission(
            agent=None, input_kwargs={"tool": _Stub(name), "tool_input": tool_input}, next_handler=next_handler))

    def leave(remaining):
        while env.context.budget.limits.agent_steps - env.context.budget.steps_used > remaining:
            env.context.budget.take_step()

    explore(env)  # one phase, which is where the runtime hands over the contract
    assert middleware.contract is not None

    # Facts still missing: reads stay open while there is room to gather them, because
    # reading is how a contract is grounded.
    middleware.contract = contract(missing_information=("which behaviour is expected",))
    leave(3)
    assert check("Read", {"file_path": "a.py"}).behavior is PermissionBehavior.ALLOW

    # The reserved steps end that phase too, and they end it with the revision its reading
    # justifies or with what is missing -- never with reading until nothing is left.
    leave(2)
    refused = check("Read", {"file_path": "a.py"})
    assert refused.behavior is PermissionBehavior.DENY
    assert "revise_contract" in refused.message and "request_information" in refused.message
    assert check("write_candidate", write_payload()).behavior is PermissionBehavior.ALLOW
    assert check("revise_contract", {"source_refs": [], "reason": "new evidence"}).behavior is PermissionBehavior.ALLOW
    assert check("request_information", {"question": "still missing"}).behavior is PermissionBehavior.ALLOW

    # A grounded contract holds everything a candidate needs, so the same steps are for
    # publishing it and the refusal says so.
    middleware.contract = contract()
    for name, tool_input in (("Read", {"file_path": "a.py"}),
                             ("Grep", {"pattern": "def parse"}),
                             ("Glob", {"pattern": "*.py"})):
        refused = check(name, tool_input)
        assert refused.behavior is PermissionBehavior.DENY and "publish" in refused.message
    assert check("write_candidate", write_payload()).behavior is PermissionBehavior.ALLOW
    assert check("revise_contract", {"source_refs": [], "reason": "new evidence"}).behavior is PermissionBehavior.ALLOW
    assert check("request_information", {"question": "still missing"}).behavior is PermissionBehavior.ALLOW

    assert "RESERVED_FOR_PUBLISHING" in [payload["result_code"] for payload in events(env, "exploration.action")]


@pytest.mark.parametrize('try_reserved_read', [False, True])
def test_batch_citation_feedback_reaches_next_wire_request(tmp_path, projects, facts, try_reserved_read):
    """The real formatter/SDK sends the complete result; answers are scripted."""
    env = None
    requests, issued = [], []

    def handler(request):
        actual = json.loads(request.content)
        requests.append(actual)
        n = len(requests)
        if n == 1:
            return tool_reply('Read', {'file_path': str(env.module), 'offset': 2, 'limit': 1}, call_id='read-source')
        if n == 2:
            result = next(m for m in actual['messages'] if m.get('tool_call_id') == 'read-source')
            import re
            shown = json.loads(re.search(r'<evidence>(.*?)</evidence>', result['content']).group(1))['refs'][0]
            issued.append(shown)
            return tool_reply('revise_contract', {'source_refs': [
                {**shown, 'content_hash': 'f' * 64}, {**shown, 'start_line': 1}],
                'reason': 'two invalid citations'}, call_id='bad-revision')
        feedback = next(m for m in actual['messages'] if m.get('tool_call_id') == 'bad-revision')
        payload = json.loads(feedback['content'])
        assert payload['error_count'] == 2 and payload['omitted_errors'] == 0
        assert [e['code'] for e in payload['errors']] == ['SOURCE_HASH_MISMATCH', 'SOURCE_RANGE_NOT_DISPLAYED']
        assert all(e['available_refs'] == issued for e in payload['errors'])
        assert not env.gate.finished
        if try_reserved_read and n == 3:
            return read_reply(env.module, 'reserved-read')
        if try_reserved_read:
            refusal = next(m for m in actual['messages'] if m.get('tool_call_id') == 'reserved-read')
            assert 'refused' in refusal['content']
        return tool_reply('revise_contract', {'source_refs': issued, 'reason': 'copy exact displayed references'},
                          call_id='fixed-revision')

    env = environment(tmp_path, projects, facts, handler=handler)
    # Enter the already shipped convergence mode; reads are initially still allowed.
    for _ in range(15):
        env.context.budget.take_step()

    async def go():
        try:
            return await env.runtime.explore(replace(agent_context(env),original_execution_count=1))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    result = asyncio.run(go())
    expected = 4 if try_reserved_read else 3
    assert result.kind == 'revise_contract'
    assert len(requests) == expected and env.context.budget.steps_used == 15 + expected
    assert len(events(env, 'exploration.action')) == expected
    assert len(events(env, 'exploration.tool_error')) == 1
    assert events(env, 'exploration.tool_error')[0]['result_code'] == 'SOURCE_HASH_MISMATCH'
    if try_reserved_read:
        assert events(env, 'exploration.action')[2]['result_code'] == 'RESERVED_FOR_PUBLISHING'
    for offset, request in enumerate(requests):
        progress = wire_progress(request)
        assert progress['budget']['mode'] == 'converging'
        assert progress['budget']['used_steps'] == 15 + offset


def test_batch_feedback_preserves_experience_browse_gate(tmp_path, projects, facts):
    from tests.integration.test_agentscope_experience_triggers import configured, run
    from reproagent.core.serialization import bytes_hash
    env = configured(tmp_path, projects, facts)
    ref = {'path': 'example/parser.py', 'content_hash': bytes_hash(env.module.read_bytes()),
           'start_line': 2, 'end_line': 2}
    env.answers.extend([
        tool_reply('Read', {'file_path': str(env.module), 'offset': 2, 'limit': 1}, call_id='read-source'),
        tool_reply('revise_contract', {'source_refs': [{**ref, 'content_hash': 'f' * 64},
            {**ref, 'start_line': 1}], 'reason': 'batch refusal'}, call_id='bad-revision'),
        tool_reply('revise_contract', {'source_refs': [ref], 'reason': 'copy displayed'}, call_id='fixed-revision')])
    assert run(env).kind == 'revise_contract'
    assert env.runtime.browse_gate.state == 'satisfied'
    assert env.context.budget.steps_used == len(env.requests) == 4
    feedback = next(m for m in env.requests[-1]['messages'] if m.get('tool_call_id') == 'bad-revision')
    assert json.loads(feedback['content'])['error_count'] == 2
    assert 'Current experience browse state: satisfied' in json.dumps(env.requests[-1]['messages'])
    assert len(events(env, 'exploration.tool_error')) == 1


def test_exploration_prompt_explains_batch_citation_repair(tmp_path, projects, facts):
    from reproagent.core.agent import exploration_prompt
    env = environment(tmp_path, projects, facts)
    text = exploration_prompt(agent_context(env))
    assert 'When revise_contract returns error_count and errors' in text
    assert 'available_refs' in text and 'omitted_errors' in text


def test_relative_read_and_subrange_revision_reach_actual_sdk_wire(tmp_path, projects, facts):
    env = None
    seen = []
    def handler(request):
        actual = json.loads(request.content)
        seen.append(actual)
        if len(seen) == 1:
            return tool_reply('Read', {'file_path': 'example/parser.py'}, call_id='relative-read')
        result = next(m for m in actual['messages'] if m.get('tool_call_id') == 'relative-read')
        assert 'def parse(values):' in result['content']
        import re
        shown = json.loads(re.search(r'<evidence>(.*?)</evidence>', result['content']).group(1))['refs'][0]
        return tool_reply('revise_contract', {'source_refs': [{**shown, 'start_line': 2, 'end_line': 2}],
                                             'reason': 'fully visible implementation'}, call_id='subrange')
    env = environment(tmp_path, projects, facts, handler=handler)
    async def go():
        try:
            return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    result = asyncio.run(go())
    assert result.kind == 'revise_contract' and len(seen) == 2
    assert result.source_refs[0].start_line == result.source_refs[0].end_line == 2


@pytest.mark.parametrize('count,used,allowed', [(0,12,True),(0,13,False),(1,16,True),(1,17,False)])
def test_execution_policy_matches_actual_sdk_permission_and_wire(tmp_path,projects,facts,count,used,allowed):
    env = environment(tmp_path,projects,facts)
    env.answers.extend([read_reply(env.module,'policy-read'),
        tool_reply('request_information', {'question':'Need evidence'},call_id='finish')])
    for _ in range(used):
        env.context.budget.take_step()
    result = asyncio.run(env.runtime.explore(replace(agent_context(env),original_execution_count=count)))
    assert result.kind == 'request_information'
    action = events(env,'exploration.action')[0]
    assert action['result_code'] == ('ALLOWED' if allowed else 'RESERVED_FOR_PUBLISHING')
    progress = wire_progress(env.requests[0])
    assert progress['facts']['readers_available_next_call'] is allowed
    assert progress['facts']['original_execution_count'] == count
    user = next(json.loads(m['content']) for m in env.requests[0]['messages']
                if m['role']=='user' and m['content'].startswith('{'))
    assert user['original_execution_count'] == count
