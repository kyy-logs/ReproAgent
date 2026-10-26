import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reproagent.core.models import TaskRequest, TaskState
from reproagent.core.protocol import ProviderRequestError
from reproagent.experience import ExperienceService, ExperienceSnapshot
from reproagent.experience_drafts import CapturedDraft, LessonDraft
from tests.unit.test_controller import PASSING, nothing, publish, setup


def exploration(*, offset=5):
    return {
        "source_refs": (),
        "tool_errors": (
            {"tool": "Read", "tool_call_id": "read-1", "state": "error", "message_offset": offset,
             "block_index": 0, "arguments": {"file_path": "absent.py"}, "result": "absent",
             "link_state": "matched"},
        ),
        "capture_issues": (),
        "draft_origin": {"call_id": "draft-call", "message_offset": 5, "block_index": 0,
                         "arguments_hash": "arguments-hash"},
    }


def run_task(tmp_path, projects, facts):
    request, _, controller, context = setup(
        tmp_path, projects, facts, plan=[publish(content=PASSING)])
    result = asyncio.run(controller.run(request, context))
    return request, controller.store, result, context


@pytest.mark.parametrize(("enabled", "saw_none"), [(True, True), (False, False)])
def test_fallback_is_skipped_when_explicit_none_or_disabled(tmp_path, projects, facts, enabled, saw_none):
    request, store, result, context = run_task(tmp_path, projects, facts)
    calls = []

    class Gateway:
        async def complete(self, model_request, call_context):
            calls.append(model_request)
            raise AssertionError("fallback must not call the model")

    library = tmp_path / "experiences"
    service = ExperienceService(
        replace(request, experience_file=library, learn_experience=True,
                experience_mode="draft_review", experience_draft_fallback=enabled),
        store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=None, saw_invalid=False, saw_none=saw_none)

    outcome = asyncio.run(service.learn(result, context))

    assert calls == []
    assert outcome.code == "skipped_no_draft"
    assert outcome.fallback_used is False


def test_fallback_is_skipped_on_a_successful_task_without_controlled_failure(tmp_path, projects, facts):
    request, _, controller, context = setup(tmp_path, projects, facts)
    store = controller.store
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.DONE
    calls = []

    class Gateway:
        async def complete(self, model_request, call_context):
            calls.append(model_request)
            raise AssertionError("a successful task must not pay for a fallback")

    library = tmp_path / "experiences"
    service = ExperienceService(
        replace(request, experience_file=library, learn_experience=True,
                experience_mode="draft_review", experience_draft_fallback=True),
        store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=None, saw_invalid=False, saw_none=False)

    outcome = asyncio.run(service.learn(result, context))

    assert calls == []
    assert outcome.code == "skipped_no_draft"
    assert outcome.fallback_used is False


def test_fallback_synthesizes_a_draft_after_a_rejected_task(tmp_path, projects, facts):
    request, store, result, context = run_rejected_candidate_task(tmp_path, projects, facts)
    assert result.status != TaskState.DONE
    library = tmp_path / "experiences"
    calls, contexts = [], []

    class Gateway:
        async def complete(self, model_request, call_context):
            payload = json.loads(model_request.messages[-1]["content"])
            calls.append(payload)
            contexts.append(call_context)
            if "draft" not in payload:
                source_id = next(entry["id"] for entry in payload["evidence"]
                                 if entry["kind"] != "agent_hypothesis")
                return SimpleNamespace(text=json.dumps({"lesson_draft": {
                    "situation": "a fixture rejected a path object",
                    "observation": "setup failed before the target",
                    "lesson": "check the accepted path type",
                    "source_ids": [source_id]}}))
            evidence_id = payload["draft_evidence_ids"][0]
            return SimpleNamespace(text=json.dumps({
                "decision": "accept", "reason_code": "SUPPORTED",
                "experience": {"category": "framework", "tags": ["pytest"],
                    "name": "fallback draft lesson", "description": "when setup rejects a path",
                    "summary": "setup failed before the target", "detail": "check the path type",
                    "evidence_ids": [evidence_id]}}))

    service = ExperienceService(
        replace(request, experience_file=library, learn_experience=True,
                experience_mode="draft_review", experience_draft_fallback=True),
        store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=None, saw_invalid=False, saw_none=False)
    service.exploration = exploration(offset=1)

    outcome = asyncio.run(service.learn(result, context))

    assert outcome.code == "written", outcome
    assert outcome.fallback_used is True
    assert outcome.review_decision == "accept"
    assert len(calls) == 2
    fallback, review = calls
    assert "draft" not in fallback
    assert fallback["response_schema"]["properties"]["lesson_draft"]["required"] == [
        "situation", "observation", "lesson", "source_ids"]
    assert review["draft"]["source_ids"] == review["draft_evidence_ids"]
    assert "decision" in review["response_schema"]["properties"]
    assert len(contexts) == 2 and contexts[0] is not contexts[1]
    assert [c.budget.limits.task_timeout_seconds for c in contexts] == [30, 30]

    from reproagent.experience import load_experience_snapshot
    snapshot = load_experience_snapshot(library)
    assert snapshot.state == "ready"
    assert [card.description for card in snapshot.cards] == ["when setup rejects a path"]
    events, errors = store.read_events()
    assert not errors
    review_event = next(event for event in events if event.kind == "experience.review")
    assert review_event.payload["written"] is True
    assert review_event.payload["fallback_used"] is True


def test_fallback_failure_is_isolated_and_not_retried(tmp_path, projects, facts):
    request, store, result, context = run_rejected_candidate_task(tmp_path, projects, facts)
    library = tmp_path / "experiences"
    calls = []

    class Gateway:
        async def complete(self, model_request, call_context):
            calls.append(model_request)
            return SimpleNamespace(text="not json")

    service = ExperienceService(
        replace(request, experience_file=library, learn_experience=True,
                experience_mode="draft_review", experience_draft_fallback=True),
        store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=None, saw_invalid=False, saw_none=False)
    service.exploration = exploration(offset=1)

    outcome = asyncio.run(service.learn(result, context))

    assert outcome.code == "invalid_json"
    assert outcome.fallback_used is False
    assert len(calls) == 1
    assert store.load_record("task", result.task_id) == result
    from reproagent.experience import load_experience_snapshot
    assert load_experience_snapshot(library).cards == ()
    events, errors = store.read_events()
    assert not errors
    assert not any(event.kind == "experience.review" for event in events)


def test_draft_review_writes_a_card_after_the_result_is_saved(tmp_path, projects, facts):
    request, store, result, context = run_task(tmp_path, projects, facts)
    library = tmp_path / "experiences"
    request = replace(request, experience_file=library, learn_experience=True,
                      experience_mode="draft_review")
    draft = CapturedDraft(LessonDraft("situation", "observation", "lesson", ("read-1",)),
                          "draft-call", 99, "arguments-hash")
    seen = []
    class Gateway:
        async def complete(self, model_request, call_context):
            payload = json.loads(model_request.messages[-1]["content"])
            seen.append(payload)
            evidence_id = payload["draft_evidence_ids"][0]
            return SimpleNamespace(text=json.dumps({
                "decision": "accept", "reason_code": "SUPPORTED",
                "experience": {"category": "framework", "tags": ["pytest"],
                    "name": "check path type", "description": "when a fixture rejects a path object",
                    "summary": "setup failed before the target", "detail": "check the path type",
                    "evidence_ids": [evidence_id]}}))
    service = ExperienceService(request, store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=draft, saw_invalid=False)
    service.exploration = exploration(offset=1)

    outcome = asyncio.run(service.learn(result, context))

    assert outcome.code == "written", outcome
    assert outcome.review_decision == "accept"
    assert outcome.review_reason_code == "SUPPORTED"
    assert seen and seen[0]["draft"]["source_ids"] == ["read-1"]
    from reproagent.experience import load_experience_snapshot
    snapshot = load_experience_snapshot(library)
    assert snapshot.state == "ready" and snapshot.cards
    events, errors = store.read_events()
    assert not errors
    assert any(event.kind == "experience.review" and event.payload["written"] for event in events)

    from reproagent.experience_catalog import ExperienceCatalogView
    view = ExperienceCatalogView(snapshot)
    page = view.list("framework")
    assert page["code"] == "ready"
    assert [item["description"] for item in page["items"]] == ["when a fixture rejects a path object"]
    card_id = page["items"][0]["id"]
    detail = view.read(card_id)
    assert detail["description"] == "when a fixture rejects a path object"

    # The index cache is disposable: a bad cache must not hide the category body.
    (library / "framework" / "index.json").write_bytes(b"broken cache")
    recovered = load_experience_snapshot(library)
    assert recovered.state == "ready" and recovered.cards
    recovered_view = ExperienceCatalogView(recovered)
    assert recovered_view.list("framework")["code"] == "ready"


@pytest.mark.parametrize(("status", "stop_reason"), [
    (TaskState.EXHAUSTED, "EXHAUSTED"),
    (TaskState.FAILED, "EXPORT_FAILED"),
])
def test_failed_task_can_still_write_a_reviewed_lesson(tmp_path, projects, facts, status, stop_reason):
    request, store, result, context = run_task(tmp_path, projects, facts)
    result = replace(result, status=status, stop_reason=stop_reason)
    library = tmp_path / "experiences"
    request = replace(request, experience_file=library, learn_experience=True,
                      experience_mode="draft_review")
    draft = CapturedDraft(LessonDraft("situation", "observation", "lesson", ("read-1",)),
                          "draft-call", 99, "arguments-hash")

    class Gateway:
        async def complete(self, model_request, call_context):
            payload = json.loads(model_request.messages[-1]["content"])
            return SimpleNamespace(text=json.dumps({
                "decision": "accept", "reason_code": "SUPPORTED",
                "experience": {"category": "framework", "tags": ["pytest"],
                    "name": "failed task lesson", "description": "when setup fails",
                    "summary": "setup failed", "detail": "check the setup",
                    "evidence_ids": [payload["draft_evidence_ids"][0]]}}))

    service = ExperienceService(request, store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=draft, saw_invalid=False)
    service.exploration = exploration(offset=1)
    outcome = asyncio.run(service.learn(result, context))
    assert outcome.code == "written", outcome
    assert result.status == status


INVALID_CANDIDATE_TEST = 'def test_bad(unknown_fixture): assert False\n'


def run_rejected_candidate_task(tmp_path, projects, facts):
    """Run a task whose candidate is rejected as INVALID_CANDIDATE before the draft review.

    The controller never turns a rejected candidate into DONE: it keeps the rejection on
    the attempt and the task ends for the next phase's own reason. The helper returns the
    real terminal result; the test pins both the terminal status and the rejection carrier
    instead of inventing a FAILED result the controller never emits.
    """
    request, _, controller, context = setup(
        tmp_path, projects, facts,
        plan=[publish(content=INVALID_CANDIDATE_TEST),
              nothing('the rejected candidate was not revised')])
    result = asyncio.run(controller.run(request, context))
    events, errors = controller.store.read_events()
    assert not errors
    assert any(event.kind == 'phase.result' and event.payload['result_code'] == 'INVALID_CANDIDATE'
               for event in events)
    assert result.status == TaskState.NEEDS_INFORMATION
    assert result.stop_reason == 'NO_CANDIDATE'
    return request, controller.store, result, context


def test_rejected_candidate_draft_is_still_reviewed_and_written(tmp_path, projects, facts):
    """A rejected candidate must not suppress a valid draft's review or its card.

    The draft source (the failed Read at offset 1) was recorded before the draft call at
    offset 5. The review payload must carry the real terminal task status and the
    candidate's own rejection carrier, and accept/SUPPORTED can only mean the lesson
    draft is supported -- never that the candidate reproduced.
    """
    request, store, rejected, context = run_rejected_candidate_task(tmp_path, projects, facts)
    library = tmp_path / "experiences"
    request = replace(request, experience_file=library, learn_experience=True,
                      experience_mode="draft_review")
    draft = CapturedDraft(LessonDraft("situation", "observation", "lesson", ("read-1",)),
                          "draft-call", 99, "arguments-hash")
    seen, prompts = [], []
    expected_status = rejected.status.value

    class Gateway:
        async def complete(self, model_request, call_context):
            prompts.append(model_request.messages[0]["content"])
            payload = json.loads(model_request.messages[-1]["content"])
            seen.append(payload)
            # The fake gateway cannot judge prose; it can refuse to proceed unless the
            # payload carries the real terminal status and the candidate's rejection.
            assert payload["task_status"] == expected_status
            assert payload["attempts"][0]["classification"] == "INVALID_CANDIDATE"
            workflow = next(entry for entry in payload["evidence"]
                            if entry["kind"] == "workflow")
            assert json.loads(workflow["text"])["task_status"] == expected_status
            return SimpleNamespace(text=json.dumps({
                "decision": "accept", "reason_code": "SUPPORTED",
                "experience": {"category": "framework", "tags": ["pytest"],
                    "name": "unknown fixture is not a reproduction",
                    "description": "when a candidate test errors on an unknown fixture before the target",
                    "summary": "候选测试在目标前因未知 fixture 报错，不能证明问题已复现；该教训仍可复用",
                    "detail": "先核对 fixture 是否存在；候选被判定无效不等于问题已复现",
                    "evidence_ids": [payload["draft_evidence_ids"][0]]}}))

    service = ExperienceService(request, store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=draft, saw_invalid=False)
    service.exploration = exploration(offset=1)

    outcome = asyncio.run(service.learn(rejected, context))

    assert outcome.code == "written", outcome
    assert outcome.review_decision == "accept"
    assert outcome.review_reason_code == "SUPPORTED"
    assert rejected.status == TaskState.NEEDS_INFORMATION
    assert rejected.stop_reason == "NO_CANDIDATE"
    assert seen and seen[0]["task_status"] == "NEEDS_INFORMATION"
    assert seen[0]["attempts"][0]["classification"] == "INVALID_CANDIDATE"
    # The prompt already tells the reviewer that support for a draft is not proof of a
    # fix, and explicitly forbids presenting a rejected/unverified candidate as success.
    assert "A reproduction is not a production fix." in prompts[0]
    assert ("When task_status is not DONE or the attempt classification is not REPRODUCED, "
            "the experience must say the candidate was rejected or unverified and must not "
            "claim reproduction or recovery.") in prompts[0]
    from reproagent.experience import load_experience_snapshot
    snapshot = load_experience_snapshot(library)
    assert snapshot.state == "ready" and snapshot.cards
    card = next(card for card in snapshot.cards if card.description == "when a candidate test errors on an unknown fixture before the target")
    assert card.category == "framework" and card.tags == ("pytest",)
    assert card.summary == "候选测试在目标前因未知 fixture 报错，不能证明问题已复现；该教训仍可复用"
    assert card.detail == "先核对 fixture 是否存在；候选被判定无效不等于问题已复现"
    events, errors = store.read_events()
    assert not errors
    review = next(event for event in events if event.kind == "experience.review")
    assert review.payload["written"] is True
    assert review.payload["decision"] == "accept"
    assert review.payload["reason_code"] == "SUPPORTED"


def test_overlong_review_field_is_reported_without_a_second_request_or_card(tmp_path, projects, facts):
    request, store, result, context = run_task(tmp_path, projects, facts)
    library = tmp_path / "experiences"
    request = replace(request, experience_file=library, learn_experience=True,
                      experience_mode="draft_review")
    draft = CapturedDraft(LessonDraft("situation", "observation", "lesson", ("read-1",)),
                          "draft-call", 99, "arguments-hash")
    calls = []

    class Gateway:
        async def complete(self, model_request, call_context):
            calls.append(model_request)
            payload = json.loads(model_request.messages[-1]["content"])
            return SimpleNamespace(text=json.dumps({
                "decision": "accept", "reason_code": "SUPPORTED",
                "experience": {"category": "framework", "tags": ["pytest"],
                    "name": "length boundary", "description": "d" * 165,
                    "summary": "observed", "detail": "check the boundary",
                    "evidence_ids": [payload["draft_evidence_ids"][0]]}}))

    service = ExperienceService(request, store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=draft, saw_invalid=False)
    service.exploration = exploration(offset=1)

    outcome = asyncio.run(service.learn(result, context))

    assert outcome.code == "invalid_field_length"
    assert outcome.field_errors == (
        {'field': 'description', 'actual_length': 165, 'limit': 160},)
    assert len(calls) == 1
    assert store.load_record("task", result.task_id) == result
    from reproagent.experience import load_experience_snapshot
    assert load_experience_snapshot(library).cards == ()
    events, errors = store.read_events()
    assert not errors
    learning = next(event for event in events if event.kind == "experience.learning")
    assert learning.payload["code"] == "invalid_field_length"
    assert learning.payload["field_errors"] == [
        {'field': 'description', 'actual_length': 165, 'limit': 160}]
    assert not any(event.kind == "experience.review" for event in events)


def test_review_rejection_does_not_write_a_card(tmp_path, projects, facts):
    request, store, result, context = run_task(tmp_path, projects, facts)
    library = tmp_path / "experiences"
    request = replace(request, experience_file=library, learn_experience=True,
                      experience_mode="draft_review")
    draft = CapturedDraft(LessonDraft("situation", "observation", "lesson", ("read-1",)),
                          "draft-call", 99, "arguments-hash")

    class Gateway:
        async def complete(self, model_request, call_context):
            return SimpleNamespace(text=json.dumps({
                "decision": "reject", "reason_code": "UNSUPPORTED", "experience": None}))

    service = ExperienceService(request, store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=draft, saw_invalid=False)
    service.exploration = exploration(offset=1)
    outcome = asyncio.run(service.learn(result, context))
    assert outcome.code == "review_rejected"
    from reproagent.experience import load_experience_snapshot
    assert load_experience_snapshot(library).cards == ()


def test_learning_provider_error_does_not_change_result_or_write(tmp_path, projects, facts):
    request, store, result, context = run_task(tmp_path, projects, facts)
    library = tmp_path / "experiences"
    request = replace(request, experience_file=library, learn_experience=True,
                      experience_mode="draft_review")
    draft = CapturedDraft(LessonDraft("situation", "observation", "lesson", ("read-1",)),
                          "draft-call", 99, "arguments-hash")

    class Gateway:
        async def complete(self, model_request, call_context):
            raise ProviderRequestError(status_code=503)

    service = ExperienceService(request, store, lambda _store: Gateway(), secrets=())
    service.path = library
    service.snapshot = ExperienceSnapshot("ready", "hash", ())
    service.draft_session = SimpleNamespace(latest=draft, saw_invalid=False)
    service.exploration = exploration(offset=1)
    before = result
    outcome = asyncio.run(service.learn(result, context))
    assert outcome.code == "provider_error"
    assert result == before
    from reproagent.experience import load_experience_snapshot
    assert load_experience_snapshot(library).cards == ()
