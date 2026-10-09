"""The offline viewer: one self-contained page, and no way to make it do anything else.

The viewer is the only part of the trace a person reads directly, so it has to be
honest about what it does not know, it has to survive metadata that came from
somewhere untrusted, and it has to work with no network at all.  It also must not be
able to write anywhere except its own two files.
"""
import json
from pathlib import Path

import pytest

from reproagent import cli
from reproagent.observability import (
    TRACE_DIRECTORY,
    TRACE_FILENAME,
    render_trace,
    write_trace,
    write_trace_html,
)


def span(span_id, name, *, parent=None, kind="span", offset=0.0, duration=1.0, status="ok", **attributes):
    return {"span_id": span_id, "parent_span_id": parent, "name": name, "kind": kind,
            "offset_seconds": offset, "duration_seconds": duration, "status": status,
            "attributes": dict(attributes)}


def document(**overrides):
    body = {
        "schema_version": 1,
        "trace_id": "a" * 32,
        "task_id": "task-1",
        "status": "DONE",
        "main_duration": 12.5,
        "total_duration": 20.0,
        "partial": False,
        "metrics_complete": True,
        "warnings": [],
        "spans": [
            span("0" * 16, "task", parent=None, duration=20.0),
            span("1" * 16, "explore", parent="0" * 16, offset=1.0, duration=4.0),
            span("2" * 16, "sdk.model_round", parent="1" * 16, offset=1.2, duration=2.5),
            span("3" * 16, "model.logical", parent="2" * 16, offset=1.2, duration=2.5, purpose="exploration"),
            span("4" * 16, "model.http_attempt", parent="3" * 16, offset=1.2, duration=2.4,
                 attempt=1, result_code="completed", cost=0.0012,
                 usage={"input_tokens": 1200, "output_tokens": 300}),
            span("5" * 16, "tool.Read", parent="1" * 16, offset=4.0, duration=0.2,
                 tool="Read", result_code="SUCCESS"),
            span("6" * 16, "verify", parent="0" * 16, offset=8.0, duration=1.5, result_code="REPRODUCED"),
            span("7" * 16, "export", parent="0" * 16, offset=18.0, duration=0.5, status="error"),
            span("8" * 16, "permission", parent="1" * 16, kind="event", duration=None,
                 tool="Read", result_code="RESERVED_FOR_PUBLISHING"),
        ],
        "summary": {
            "span_count": 9, "logical_calls": 1, "http_attempts": 1, "tool_executions": 1,
            "usage": {"input_tokens": 1200, "output_tokens": 300, "complete": True},
            "cost": {"amount": 0.0012, "complete": True},
            "learning": {"code": "appended", "event_recorded": True},
        },
    }
    return {**body, **overrides}


def page_for(doc):
    html = render_trace(doc)
    assert html.startswith("<!DOCTYPE html>")
    return html


def test_trace_html_renders_failure_and_cost_breakdown(tmp_path):
    html = page_for(document())

    # The phases a reader asks about, the failure, and the cost of the wire attempts.
    for expected in ("explore", "model.http_attempt", "tool.Read", "verify", "export"):
        assert expected in html
    assert "error" in html
    assert "0.0012" in html
    assert "1200" in html and "300" in html
    # Nested durations overlap, so the page must not present their sum as a total.
    assert "20.0" in html
    assert str(sum((1.0, 4.0, 2.5, 2.5, 2.4, 0.2, 1.5, 0.5))) not in html


def test_rendering_escapes_untrusted_metadata():
    hostile = "<script>alert('x')</script>"
    doc = document(spans=[span("0" * 16, "task", reason=hostile)],
                   warnings=[f"a warning mentioning {hostile}"])

    html = page_for(doc)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_unknown_or_partial_data_is_explicit():
    doc = document(
        partial=True, metrics_complete=False,
        warnings=["trace span cap reached; later observations were dropped"],
        summary={"span_count": 2, "logical_calls": 1, "http_attempts": 1, "tool_executions": 0,
                 "usage": {"input_tokens": None, "output_tokens": None, "complete": False},
                 "cost": {"amount": None, "complete": False}, "learning": None},
    )

    html = page_for(doc)

    # A missing figure is shown as unknown, never as a confident zero.
    assert "unknown" in html.lower()
    assert "incomplete" in html.lower()
    assert "span cap" in html


def test_viewer_is_offline():
    html = page_for(document())

    lowered = html.lower()
    for remote in ("http://", "https://", "<script", "src=", "@import", "cdn."):
        assert remote not in lowered
    # The page is one file with its styling inlined.
    assert "<style" in lowered


def test_missing_or_corrupt_trace_is_rejected(tmp_path):
    empty = tmp_path / "empty-task"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        write_trace_html(empty)

    broken = tmp_path / "broken-task" / TRACE_DIRECTORY
    broken.mkdir(parents=True)
    (broken / TRACE_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        write_trace_html(broken.parent)

    assert cli.main(["trace", str(empty)]) == 2
    assert cli.main(["trace", str(broken.parent)]) == 2
    assert cli.main(["trace", str(tmp_path / "absent")]) == 2


def test_an_oversized_page_is_refused():
    """The page has a ceiling too, and a document past it is refused rather than cut."""
    oversized = document(spans=[span(f"{index:016x}", "tool.Read", tool="x" * 400)
                                for index in range(20000)])

    with pytest.raises(ValueError):
        render_trace(oversized)


def test_observability_output_cannot_redirect_to_artifacts(tmp_path):
    task = tmp_path / "task"
    (task / "artifacts").mkdir(parents=True)
    write_trace(task, document())

    # There is no output option to point somewhere else, and the fixed destination
    # stays inside the task's own observability directory.
    with pytest.raises(SystemExit):
        cli.main(["trace", str(task), "--output", str(task / "artifacts/trace.html")])

    path = write_trace_html(task)
    assert path == task / TRACE_DIRECTORY / "trace.html"
    assert not (task / "artifacts" / "trace.html").exists()


def _tree(root):
    return {item.relative_to(root).as_posix(): item.read_bytes()
            for item in sorted(root.rglob("*")) if item.is_file()}


def test_trace_cli_changes_only_observability_files(tmp_path):
    task = tmp_path / "task"
    (task / "artifacts").mkdir(parents=True)
    (task / "artifacts" / "manifest.json").write_text('{"sealed":true}', encoding="utf-8")
    write_trace(task, document())
    before = _tree(task)

    assert cli.main(["trace", str(task)]) == 0

    after = _tree(task)
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    # Exactly one new file, inside observability/, and nothing else touched.
    assert changed == {f"{TRACE_DIRECTORY}/trace.html"}
    assert json.loads((task / "artifacts" / "manifest.json").read_text(encoding="utf-8")) == {"sealed": True}
