import importlib
from dataclasses import replace

import pytest

from reproagent.core.models import IssueContract, TaskResult, TaskState


def make_store(tmp_path):
    return importlib.import_module("reproagent.store").TaskStore(tmp_path)


def test_interrupted_replace_preserves_old_record(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    original = TaskResult("task-1", TaskState.PREPARING)
    store.save_record("task", "task-1", original)
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr("reproagent.store.os.replace", fail)
    with pytest.raises(OSError):
        store.save_record("task", "task-1", replace(original, status=TaskState.DONE))
    assert store.load_record("task", "task-1") == original


def test_truncated_event_tail_is_reported_without_inventing_success(tmp_path):
    store = make_store(tmp_path)
    assert store.append_event("start", (), {}).seq == 0
    assert store.append_event("progress", (), {}).seq == 1
    with (tmp_path / "events.jsonl").open("ab") as handle:
        handle.write(b'{"schema_version":1,"seq":2,"kind":"DONE"')
    before = (tmp_path / "events.jsonl").read_bytes()
    result = store.inspect()
    assert result["incomplete_records"] is True
    assert result["event_count"] == 2
    assert result["task"] is None
    assert (tmp_path / "events.jsonl").read_bytes() == before


def test_rejects_unknown_schema_and_path_escape(tmp_path):
    store = make_store(tmp_path)
    contract = IssueContract("contract-1")
    with pytest.raises(ValueError):
        store.save_record("contracts", "../outside", contract)
    store.save_record("contracts", "contract-1", contract)
    with pytest.raises(ValueError, match="immutable"):
        store.save_record("contracts", "contract-1", replace(contract, expected="changed"))
    path = tmp_path / "contracts" / "contract-1.json"
    path.write_text('{"schema_version":999}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        store.load_record("contracts", "contract-1")


def test_corrupt_middle_event_is_not_silently_skipped(tmp_path):
    store = make_store(tmp_path)
    store.append_event("start", (), {})
    with (tmp_path / "events.jsonl").open("ab") as handle:
        handle.write(b'not-json\n')
    result = store.inspect()
    assert result["incomplete_records"] is True
    assert result["errors"]
