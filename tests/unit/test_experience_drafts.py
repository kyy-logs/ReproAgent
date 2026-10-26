import json

import pytest

from reproagent.core.serialization import canonical_bytes
from reproagent.experience_drafts import (
    ExperienceDraftSession,
    LessonDraft,
    parse_lesson_draft,
)
from reproagent.store import TaskStore


def raw_draft(**overrides):
    value = {
        "situation": "旧版测试辅助类初始化出现路径方法缺失时",
        "observation": "初始化抛出 AttributeError，目标断言未执行",
        "lesson": "先核对要求的路径类型；替代方案尚未执行",
        "source_ids": ["call-1"],
    }
    value.update(overrides)
    return value


def registered_draft(session, **overrides):
    identity = session.register_source({'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'})
    return raw_draft(source_ids=[identity], **overrides)


def test_valid_draft_is_normalized_and_bounded():
    draft = parse_lesson_draft(raw_draft())
    assert draft == LessonDraft(
        "旧版测试辅助类初始化出现路径方法缺失时",
        "初始化抛出 AttributeError，目标断言未执行",
        "先核对要求的路径类型；替代方案尚未执行",
        ("call-1",),
    )
    assert len(canonical_bytes(json.loads(json.dumps({
        "situation": draft.situation,
        "observation": draft.observation,
        "lesson": draft.lesson,
        "source_ids": list(draft.source_ids),
    })))) <= 1024


@pytest.mark.parametrize("mutate", [
    lambda value: value.pop("situation"),
    lambda value: value.update(extra="x"),
    lambda value: value.update(situation="   "),
    lambda value: value.update(source_ids=[]),
    lambda value: value.update(source_ids=["a", "a"]),
    lambda value: value.update(source_ids=["a", "b", "c", "d"]),
    lambda value: value.update(source_ids="call-1"),
    lambda value: value.update(source_ids=[1]),
])
def test_invalid_draft_shapes_are_rejected(mutate):
    value = raw_draft()
    mutate(value)
    with pytest.raises(ValueError):
        parse_lesson_draft(value)


def test_exact_1024_byte_draft_is_accepted_and_1025_is_rejected():
    value = raw_draft(lesson="x" * 1024)
    # Find the largest lesson that still fits the canonical envelope.
    low, high = 0, 1024
    while low < high:
        middle = (low + high + 1) // 2
        candidate = dict(value, lesson="x" * middle)
        if len(canonical_bytes(candidate)) <= 1024:
            low = middle
        else:
            high = middle - 1
    assert len(canonical_bytes(dict(value, lesson="x" * low))) <= 1024
    parse_lesson_draft(dict(value, lesson="x" * low))
    with pytest.raises(ValueError):
        parse_lesson_draft(dict(value, lesson="x" * (low + 1)))


def test_session_persists_latest_valid_draft_and_records_a_controlled_event(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store, secrets=("secret-value",))
    code = session.offer(registered_draft(session, situation="secret-value situation"), call_id="call-1", arguments_hash="hash-1")
    assert code == "received"
    assert session.latest is not None
    assert session.latest.call_id == "call-1"
    assert session.latest.arguments_hash == "hash-1"
    assert session.latest.draft.situation == "[REDACTED] situation"
    events, errors = store.read_events()
    assert not errors
    draft_events = [event for event in events if event.kind == "experience.draft"]
    assert len(draft_events) == 1
    assert draft_events[0].payload["code"] == "received"
    assert session.latest.event_seq == draft_events[0].seq
    saved = json.loads((tmp_path / "task" / "learning" / "draft.json").read_text(encoding="utf-8"))
    assert saved["draft"]["situation"] == "[REDACTED] situation"
    assert "secret-value" not in json.dumps(saved)


def test_bad_draft_keeps_previous_draft_and_candidate_is_unaffected(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(registered_draft(session), call_id="call-1", arguments_hash="hash-1") == "received"
    previous = (tmp_path / "task" / "learning" / "draft.json").read_bytes()
    assert session.offer(raw_draft(source_ids=[]), call_id="call-2", arguments_hash="hash-2") == "invalid"
    assert session.latest.call_id == "call-1"
    assert (tmp_path / "task" / "learning" / "draft.json").read_bytes() == previous
    events, _ = store.read_events()
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["received", "invalid"]


def test_explicit_none_is_recorded_without_replacing_a_valid_draft(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(registered_draft(session), call_id="call-1", arguments_hash="h1") == "received"
    assert session.offer(None, status="none", call_id="call-2", arguments_hash="h2") == "none"
    assert session.latest.call_id == "call-1"
    events, _ = store.read_events()
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["received", "none"]


def test_missing_decision_and_conflicting_decisions_are_controlled(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(None, status=None, call_id="c1", arguments_hash="h1") == "omitted"
    assert session.offer(registered_draft(session), status="none", call_id="c2", arguments_hash="h2") == "invalid_decision"
    assert session.offer(None, status="draft", call_id="c3", arguments_hash="h3") == "invalid_decision"
    assert session.latest is None


def test_invalid_lesson_status_never_stores_the_model_text(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    status = "SECRET-123" + "x" * 4096
    assert session.offer(None, status=status, call_id="c1", arguments_hash="h1") == "invalid_decision"
    events, errors = store.read_events()
    assert not errors
    event = next(event for event in events if event.kind == "experience.draft")
    assert event.payload["decision"] == "invalid"
    assert event.payload["decision_code"] == "invalid_status"
    assert event.payload["status_length"] == len(status)
    persisted = b"\n".join(path.read_bytes() for path in (tmp_path / "task").rglob("*") if path.is_file())
    assert b"SECRET-123" not in persisted


@pytest.mark.parametrize(("raw", "metadata"), [
    (raw_draft(lesson="   "), {"format_state": "invalid", "source_state": "not_checked"}),
    (raw_draft(source_ids=["unknown-source"]),
     {"format_state": "valid", "source_state": "unknown_source"}),
])
def test_explicit_draft_with_a_malformed_or_unknown_draft_is_invalid_decision(tmp_path, raw, metadata):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(raw, status="draft", call_id="c1", arguments_hash="h1") == "invalid_decision"
    events, errors = store.read_events()
    assert not errors
    event = next(event for event in events if event.kind == "experience.draft")
    assert event.payload["code"] == "invalid_decision"
    for key, value in metadata.items():
        assert event.payload[key] == value
    assert session.latest is None


@pytest.mark.parametrize(("raw", "code"), [
    (raw_draft(lesson="   "), "invalid"),
    (raw_draft(source_ids=["unknown-source"]), "invalid_sources"),
])
def test_missing_status_keeps_the_existing_invalid_codes(tmp_path, raw, code):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(raw, status=None, call_id="c1", arguments_hash="h1") == code
    assert session.latest is None


def test_write_failure_keeps_in_memory_and_disk_draft(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    value = registered_draft(session)
    assert session.offer(value, call_id="call-1", arguments_hash="hash-1") == "received"
    previous = (tmp_path / "task" / "learning" / "draft.json").read_bytes()
    import reproagent.experience_drafts as module
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(module, "atomic_write", fail)
    assert session.offer(value, call_id="call-2", arguments_hash="hash-2") == "store_error"
    assert session.latest.call_id == "call-1"
    assert (tmp_path / "task" / "learning" / "draft.json").read_bytes() == previous
