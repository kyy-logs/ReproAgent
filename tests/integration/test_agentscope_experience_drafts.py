import asyncio
import json

from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.adapters.agentscope.tools import build_toolkit
from reproagent.experience_drafts import ExperienceDraftSession
from tests.integration.test_agentscope_runtime import (
    SYSTEM_PROMPT,
    environment as runtime_environment,
    explore,
    tool_reply,
    write_payload,
)


def draft_session_environment(tmp_path, projects, facts, answers, **kwargs):
    env = runtime_environment(tmp_path, projects, facts, answers=answers, **kwargs)
    session = ExperienceDraftSession(env.store)
    env.toolkit = build_toolkit(
        env.backend, env.ledger, env.service, env.gate, env.context,
        experience_mode="draft_review", draft_session=session)
    env.runtime = AgentScopeRuntime(env.model, env.toolkit, env.gate, env.context,
                                    system_prompt=SYSTEM_PROMPT, store=env.store,
                                    draft_session=session)
    env.session = session
    return env


def valid_draft(source_ids=None):
    return {
        "situation": "使用旧版 fixture 初始化时路径类型不兼容",
        "observation": "setup 抛 AttributeError，目标断言未执行",
        "lesson": "先核对 fixture 接受的路径类型；替代方案尚未执行",
        "source_ids": ["call-1"] if source_ids is None else source_ids,
    }


def write_with_draft(draft, *, call_id="call-1", status=None):
    payload = write_payload()
    payload["lesson_draft"] = draft
    if status is not None:
        payload["lesson_status"] = status
    return tool_reply("write_candidate", payload, call_id=call_id)


def write_decision(status, *, call_id="call-1"):
    payload = write_payload()
    payload["lesson_status"] = status
    return tool_reply("write_candidate", payload, call_id=call_id)


def test_valid_draft_is_recorded_after_the_real_tool_executes(tmp_path, projects, facts):
    env = draft_session_environment(tmp_path, projects, facts,
                                    [write_with_draft(valid_draft(), call_id="draft-call", status="draft")])
    identity = env.session.register_source({'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'})
    env.answers[0] = write_with_draft(valid_draft([identity]), call_id='draft-call', status='draft')
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is not None
    assert env.session.latest.call_id == "draft-call"
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["received"]


def test_explicit_none_decision_is_forwarded_from_the_real_tool_response(tmp_path, projects, facts):
    env = draft_session_environment(tmp_path, projects, facts, [write_decision("none", call_id="none-call")])
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is None
    assert env.session.saw_none is True
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["none"]


def test_conflicting_decision_does_not_reject_a_valid_candidate(tmp_path, projects, facts):
    env = draft_session_environment(
        tmp_path, projects, facts,
        [write_with_draft(valid_draft(), call_id="conflict-call", status="none")])
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is None
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["invalid_decision"]


def test_invalid_lesson_status_never_blocks_publication(tmp_path, projects, facts):
    env = draft_session_environment(tmp_path, projects, facts,
                                    [write_decision("skip", call_id="bad-decision")])
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is None
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["invalid_decision"]


def test_direct_tool_call_with_invalid_status_still_publishes(tmp_path, projects, facts):
    from agentscope.message import ToolCallBlock, ToolResultState
    from agentscope.state import AgentState

    env = draft_session_environment(tmp_path, projects, facts, [])
    payload = {**write_payload(), "lesson_status": "skip"}

    async def dispatch():
        response = None
        block = ToolCallBlock(id="direct-call", name="write_candidate", input=json.dumps(payload))
        async for chunk in env.toolkit.call_tool(block, AgentState()):
            response = chunk
        return response

    assert asyncio.run(dispatch()).state is ToolResultState.SUCCESS


def test_draft_without_decision_is_omitted(tmp_path, projects, facts):
    env = draft_session_environment(
        tmp_path, projects, facts,
        [write_with_draft(valid_draft(), call_id="draft-no-status")])
    identity = env.session.register_source({'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'})
    env.answers[0] = write_with_draft(valid_draft([identity]), call_id='draft-no-status')
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is None
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["omitted"]


def test_omitted_decision_is_recorded_from_the_real_tool_response(tmp_path, projects, facts):
    env = draft_session_environment(
        tmp_path, projects, facts,
        [tool_reply("write_candidate", write_payload(), call_id="omitted-call")])
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is None
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["omitted"]


def test_draft_status_without_a_draft_is_controlled(tmp_path, projects, facts):
    env = draft_session_environment(tmp_path, projects, facts,
                                    [write_decision("draft", call_id="missing-draft")])
    result = explore(env)
    assert result.kind == "candidate"
    assert env.session.latest is None
    events, errors = env.store.read_events()
    assert not errors
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["invalid_decision"]


def test_bad_optional_draft_does_not_reject_a_valid_candidate(tmp_path, projects, facts):
    bad = [
        "not-an-object",
        ["not", "an", "object"],
        {"situation": "only one field"},
        {"situation": "s", "observation": "o", "lesson": "l", "source_ids": ["a", "b", "c", "d"]},
        {"situation": "s", "observation": "o", "lesson": "x" * 1024, "source_ids": ["a"]},
    ]
    for index, draft in enumerate(bad):
        env = draft_session_environment(tmp_path / f"case-{index}", projects, facts,
                                        [write_with_draft(draft, call_id=f"call-{index}", status="draft")])
        result = explore(env)
        assert result.kind == "candidate"
        assert env.session.latest is None


def test_grounded_rejection_can_still_record_a_prior_legal_draft(tmp_path, projects, facts):
    env = draft_session_environment(
        tmp_path, projects, facts,
        [write_with_draft(valid_draft(), call_id="grounded-call", status="draft"),
         tool_reply("request_information", {"question": "missing fact"}, call_id="ask")],
        missing_information=("missing fact",))
    identity = env.session.register_source({'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'})
    env.answers[0] = write_with_draft(valid_draft([identity]), call_id='grounded-call', status='draft')
    result = explore(env)
    assert result.kind != "candidate"
    assert env.session.latest is not None


def test_legacy_and_read_only_do_not_collect_drafts(tmp_path, facts):
    from reproagent.core.models import TaskRequest
    from reproagent.experience import ExperienceService
    from reproagent.store import TaskStore

    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "output"
    issue = tmp_path / "issue.md"
    issue.write_text("issue", encoding="utf-8")
    for mode, learn, expected in [("legacy", True, False), ("draft_review", False, False),
                                  ("draft_review", True, True)]:
        request = TaskRequest(repo, output / mode / str(learn), issue,
                              experience_file=tmp_path / f"experiences-{mode}-{learn}",
                              learn_experience=learn, experience_mode=mode)
        service = ExperienceService(request, TaskStore(request.output_dir), None)
        service.prepare(None, facts.context())
        assert (service.draft_session is not None) is expected


def test_draft_schema_exists_only_in_draft_review_mode(tmp_path, projects, facts):
    legacy = runtime_environment(tmp_path / "legacy", projects, facts, answers=[])
    legacy_tool = next(tool for group in legacy.toolkit.tool_groups for tool in group.tools
                       if tool.name == "write_candidate")
    assert "lesson_draft" not in legacy_tool.input_schema["properties"]
    assert "lesson_status" not in legacy_tool.input_schema["properties"]

    draft = draft_session_environment(tmp_path / "draft", projects, facts, [])
    draft_tool = next(tool for group in draft.toolkit.tool_groups for tool in group.tools
                      if tool.name == "write_candidate")
    assert "lesson_draft" in draft_tool.input_schema["properties"]
    assert draft_tool.input_schema["properties"]["lesson_status"]["default"] is None
