import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.experience import ExperienceSnapshot, ExperienceView
from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.adapters.agentscope.tools import build_toolkit
from reproagent.core.models import BudgetLimits
from reproagent.core.serialization import bytes_hash
from reproagent.experience_triggers import ExperienceTrigger
from tests.unit.test_experience_trigger_view import triggered, RANGE
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply, write_payload


def configured(tmp_path, projects, facts, *, memory=None, limits=None):
    env = environment(tmp_path, projects, facts, limits=limits)
    memory = memory or triggered("引用范围被拒时先取得有效范围。", "UNIQUE_RECOVERY_ADVICE")
    view = ExperienceView(ExperienceSnapshot("ready", "a"*64, (memory,)))
    initial = view.select("pytest logging format", ("_pytest",))
    toolkit = build_toolkit(env.backend, env.ledger, env.service, env.gate, env.context, experience_view=view)
    env.runtime = AgentScopeRuntime(env.model, toolkit, env.gate, env.context,
        system_prompt="Reproduce from original facts; historical advice is conditional.",
        store=env.store, experience_view=view)
    env.view, env.memory, env.initial = view, memory, initial
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


def test_actual_tool_error_injects_next_round_and_detail_is_readable(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    env.answers.extend([tool_reply("revise_contract", revision(env), call_id="bad-range"),
        tool_reply("read_experience", {"id": env.memory.id}, call_id="detail"),
        tool_reply("request_information", {"question": "need current evidence"}, call_id="stop")])
    assert run(env).kind == "request_information"
    assert injected(env.requests[0]) == []
    assert injected(env.requests[1]) == [env.memory.id]
    assert any(m.get("role") == "tool" and "displayed whole" in str(m.get("content")) for m in env.requests[1]["messages"])
    assert "UNIQUE_RECOVERY_ADVICE" in json.dumps(env.requests[2]["messages"])
    assert env.context.budget.steps_used == len(env.requests) == 3
    assert env.view.read_ids == (env.memory.id,)
    events, errors = env.store.read_events()
    assert not errors
    assert [e.payload["ids"] for e in events if e.kind == "experience.injected"] == [[env.memory.id]]


def test_unrelated_error_does_not_inject(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    env.answers.extend([tool_reply("revise_contract", revision(env, wrong_hash=True), call_id="bad"),
        tool_reply("request_information", {"question": "missing facts"}, call_id="stop")])
    assert run(env).kind == "request_information"
    assert all(not injected(r) for r in env.requests)


def test_repeated_error_displays_summary_once(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    env.answers.extend([tool_reply("revise_contract", revision(env), call_id="bad1"),
        tool_reply("revise_contract", revision(env), call_id="bad2"),
        tool_reply("request_information", {"question": "missing facts"}, call_id="stop")])
    run(env)
    events, _ = env.store.read_events()
    assert sum(e.kind == "experience.injected" for e in events) == 1
    assert env.context.budget.steps_used == len(env.requests) == 3


def test_real_reserved_permission_triggers_without_running_reader(tmp_path, projects, facts):
    memory = triggered("预留预算时用已有证据结束探索。", condition=ExperienceTrigger("permission", "Read", "RESERVED_FOR_PUBLISHING"))
    env = configured(tmp_path, projects, facts, memory=memory, limits=BudgetLimits(agent_steps=4))
    env.answers.extend([tool_reply("Read", {"file_path": str(env.module)}, call_id="reserved"),
        tool_reply("write_candidate", write_payload(), call_id="publish")])
    assert run(env).kind == "candidate"
    assert injected(env.requests[1]) == [memory.id]
    assert env.context.budget.steps_used == 2
    assert env.ledger.issued_refs == ()


def test_protocol_rejection_uses_existing_correction_round(tmp_path, projects, facts):
    memory = triggered("每轮只调用一个工具。", condition=ExperienceTrigger("protocol_error", "", "MULTIPLE_TOOL_CALLS"))
    env = configured(tmp_path, projects, facts, memory=memory)
    env.answers.extend([tool_reply(calls=[("Read", {"file_path": str(env.module)}, "a"), ("Glob", {}, "b")]),
        tool_reply("request_information", {"question": "missing facts"}, call_id="stop")])
    assert run(env).kind == "request_information"
    assert injected(env.requests[1]) == [memory.id]
    assert env.context.budget.steps_used == len(env.requests) == 2


def test_optional_retrieval_fault_never_replays_tool(tmp_path, projects, facts, monkeypatch):
    env = configured(tmp_path, projects, facts)
    def fail(_):
        raise RuntimeError("advisory storage failure")
    monkeypatch.setattr(env.view, "match_event", fail, raising=False)
    env.answers.extend([tool_reply("revise_contract", revision(env), call_id="bad"),
        tool_reply("request_information", {"question": "missing facts"}, call_id="stop")])
    assert run(env).kind == "request_information"
    assert len(env.requests) == env.context.budget.steps_used == 2
    assert env.service.contract.version == 1


def test_capacity_omitted_card_is_not_readable(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    size = len(env.runtime._message(agent_context(env)).get_text_content().encode("utf-8"))
    env.context.budget.limits = replace(env.context.budget.limits, tool_response_bytes=size+100)
    env.answers.extend([tool_reply("revise_contract", revision(env), call_id="bad"),
        tool_reply("request_information", {"question":"missing facts"}, call_id="stop")])
    run(env)
    assert all(not injected(r) for r in env.requests)
    with pytest.raises(ValueError):
        env.view.read(env.memory.id)
    events, _ = env.store.read_events()
    assert any(e.kind == "experience.omitted" and e.payload["code"] == "context_capacity" for e in events)


def test_allowed_reader_does_not_trigger_permission_memory(tmp_path, projects, facts):
    memory = triggered("reader denied hint", condition=ExperienceTrigger("permission", "Read", "DENIED"))
    env = configured(tmp_path, projects, facts, memory=memory)
    env.answers.extend([tool_reply("Read", {"file_path":str(env.module)}, call_id="read"),
        tool_reply("request_information", {"question":"missing facts"}, call_id="stop")])
    run(env)
    assert all(not injected(r) for r in env.requests)
    assert env.ledger.issued_refs == ()  # Read has its own wider evidence view.


def test_cancelled_runtime_does_not_admit_or_send_advice(tmp_path, projects, facts):
    from reproagent.core.budget import BudgetStopped
    env = configured(tmp_path, projects, facts)
    env.runtime._experience_event({"kind":RANGE.kind, "tool":RANGE.tool, "code":RANGE.code})
    env.context.cancel_event.set()
    with pytest.raises(BudgetStopped) as error:
        asyncio.run(env.runtime._experience_input(env.runtime._agent, {"messages":[]}))
    assert error.value.reason == "CANCELLED"
    assert env.requests == []
    with pytest.raises(ValueError):
        env.view.read(env.memory.id)


def test_closed_phase_does_not_admit_advice(tmp_path, projects, facts):
    env = configured(tmp_path, projects, facts)
    env.answers.append(tool_reply("request_information", {"question":"missing facts"}, call_id="stop"))
    run(env)
    env.runtime._experience_event({"kind":RANGE.kind, "tool":RANGE.tool, "code":RANGE.code})
    original = {"messages":[]}
    assert asyncio.run(env.runtime._experience_input(env.runtime._agent, original)) is original
    assert len(env.requests) == 1
    with pytest.raises(ValueError):
        env.view.read(env.memory.id)


def test_failed_context_append_does_not_grant_detail_access(tmp_path, projects, facts):
    from types import SimpleNamespace
    from agentscope.message import UserMsg
    class BrokenContext(list):
        def append(self, message):
            raise RuntimeError("context is unavailable")
    env = configured(tmp_path, projects, facts)
    env.runtime._experience_event({"kind":RANGE.kind,"tool":RANGE.tool,"code":RANGE.code})
    agent = SimpleNamespace(state=SimpleNamespace(context=BrokenContext()),
        model=env.runtime._agent.model,context_config=env.runtime._agent.context_config)
    original = {"messages":[UserMsg(name="user",content="original facts")]}
    assert asyncio.run(env.runtime._experience_input(agent,original)) is original
    with pytest.raises(ValueError):
        env.view.read(env.memory.id)


def test_real_error_capture_write_freeze_and_next_task_retrieval(tmp_path, projects, facts):
    from reproagent import experience as exp
    from reproagent.learning_material import capture_exploration
    from reproagent.core.models import EvidenceRef
    from reproagent.core.serialization import canonical_bytes
    first = configured(tmp_path/"first", projects, facts)
    first.answers.extend([tool_reply("revise_contract", revision(first), call_id="bad"),
        tool_reply("request_information", {"question":"missing facts"}, call_id="stop")])
    run(first)
    errors = capture_exploration(first.runtime.messages, ())['tool_errors']
    assert errors[0]['error_code'] == RANGE.code
    assert errors[0]['link_state'] == 'matched'
    entry = {"id":"E1", "kind":"tool_error", "text":canonical_bytes(errors[0]).decode('utf-8')}
    raw = canonical_bytes(entry)+b'\n'
    path = first.store.root/'learning/evidence.jsonl'
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    material = exp.LearningInput({"evidence":[entry]},
        {"E1":EvidenceRef('learning/evidence.jsonl',bytes_hash(raw),1,1)},'first@hash',0)
    proposal = {"experience":{"category":"workflow","tags":["revise_contract"],
        "summary":"遇到引用范围拒绝，先读取有效范围。","detail":"Observed refusal; recovery not validated.","evidence_ids":["E1"]}}
    learned = exp.validate_experience(proposal,material,first.store)
    library = tmp_path/'library.json'
    assert exp.append_experience(library,learned,None) == 'written'
    before = library.read_bytes()
    loaded = exp.load_experience_snapshot(library).cards[0]
    second = configured(tmp_path/"second",projects,facts,memory=loaded)
    second.answers.extend([tool_reply("revise_contract",revision(second),call_id="bad"),
        tool_reply("request_information",{"question":"missing facts"},call_id="stop")])
    run(second)
    assert injected(second.requests[0]) == []
    assert injected(second.requests[1]) == [learned.id]
    assert library.read_bytes() == before


def test_trace_retains_controlled_condition_and_injected_ids(tmp_path, projects, facts):
    from reproagent.observability import trace_session
    with trace_session() as recorder:
        env = configured(tmp_path, projects, facts)
        env.answers.extend([tool_reply("revise_contract",revision(env),call_id="bad"),
            tool_reply("request_information",{"question":"missing facts"},call_id="stop")])
        run(env)
        doc = recorder.finish(task_id="offline",status="NEEDS_INFORMATION",main_duration=1)
    injected_events = [s for s in doc['spans'] if s['name'] == 'experience.injected']
    assert injected_events[0]['attributes']['experience_ids'] == [env.memory.id]
    retrieved = next(s for s in doc['spans'] if s['name'] == 'experience.retrieved')
    assert retrieved['attributes']['experience_trigger']['code'] == RANGE.code


def test_diagnostic_write_failure_preserves_terminal_tool_result(tmp_path, projects, facts, monkeypatch):
    env = configured(tmp_path,projects,facts)
    append = env.store.append_event
    def broken(kind,*args,**kwargs):
        if kind == 'exploration.tool_error':
            raise OSError('diagnostic disk failure')
        return append(kind,*args,**kwargs)
    monkeypatch.setattr(env.store,'append_event',broken)
    env.answers.extend([tool_reply('revise_contract',revision(env),call_id='bad'),
        tool_reply('request_information',{'question':'missing facts'},call_id='stop')])
    assert run(env).kind == 'request_information'
    assert len(env.requests) == 2
    assert any(m.get('role') == 'tool' and 'displayed whole' in str(m.get('content')) for m in env.requests[1]['messages'])


def test_advice_respects_sdk_token_threshold(tmp_path, projects, facts, monkeypatch):
    env = configured(tmp_path,projects,facts,limits=BudgetLimits(tool_response_bytes=200000))
    env.runtime._agent.context_config.trigger_ratio = 0.01
    threshold = env.runtime._agent.context_config.trigger_ratio * env.runtime._agent.model.context_size
    async def tokens(messages,tools=None,**kwargs):
        return int(threshold)-1 if not any('experience_summaries' in (m.get_text_content() or '') for m in messages) else int(threshold)+10
    monkeypatch.setattr(env.model,'count_tokens',tokens)
    env.answers.extend([tool_reply('revise_contract',revision(env),call_id='bad'),
        tool_reply('request_information',{'question':'missing facts'},call_id='stop')])
    try:
        result = run(env)
    except Exception:
        pytest.fail('historical advice bypassed the SDK threshold and stopped a valid original task')
    assert result.kind == 'request_information'
    assert all(not injected(r) for r in env.requests)
    with pytest.raises(ValueError):
        env.view.read(env.memory.id)
