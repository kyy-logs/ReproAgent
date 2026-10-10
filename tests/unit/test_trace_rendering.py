"""The offline viewer: one self-contained page, and no way to make it do anything else.

The viewer is the only part of the trace a person reads directly, so it has to be
honest about what it does not know, it has to survive metadata that came from
somewhere untrusted, and it has to work with no network at all.  It also must not be
able to write anywhere except its own two files.
"""
import contextlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from reproagent import cli
from reproagent.observability import (
    MAX_READ_BYTES,
    TRACE_DIRECTORY,
    TRACE_FILENAME,
    write_trace,
)
from reproagent.trace_rendering import read_trace, render_trace, validate_trace, write_trace_html


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
    for remote in ("http://", "https://", "src=", "@import", "cdn."):
        assert remote not in lowered
    # The page is one file with its styling inlined.
    assert "<style" in lowered


def test_missing_or_corrupt_trace_is_rejected(tmp_path):
    empty = tmp_path / "empty-task"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        read_trace(empty)

    broken = tmp_path / "broken-task" / TRACE_DIRECTORY
    broken.mkdir(parents=True)
    (broken / TRACE_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        read_trace(broken.parent)

    assert cli.main(["trace", str(empty)]) == 2
    assert cli.main(["trace", str(broken.parent)]) == 2
    assert cli.main(["trace", str(tmp_path / "absent")]) == 2


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-only")
def test_a_junction_at_the_trace_directory_is_refused(tmp_path):
    """A junction moves "the task directory" somewhere else, and needs no elevation.

    ``is_symlink`` reports False for a junction, so a check built on it alone would let
    the trace be written into the delivered artifacts tree, or straight out of the task.
    """
    task = tmp_path / "task"
    (task / "artifacts").mkdir(parents=True)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(task / "observability"), str(task / "artifacts")],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("junction creation unavailable on this host")

    with pytest.raises(ValueError):
        write_trace(task, document())
    assert not (task / "artifacts" / "trace.json").exists()

    # The viewer must refuse the same redirect rather than write through it.
    (task / "artifacts" / "trace.json").write_text(json.dumps(document()), encoding="utf-8")
    with pytest.raises(ValueError):
        write_trace_html(task, document())
    assert not (task / "artifacts" / "trace.html").exists()


@contextlib.contextmanager
def _junction(target, link):
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("junction creation unavailable on this host")
    yield


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-only")
def test_a_junction_out_of_the_task_is_refused(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / TRACE_FILENAME).write_text(json.dumps(document()), encoding="utf-8")

    with _junction(outside, task / TRACE_DIRECTORY):
        with pytest.raises(ValueError):
            write_trace(task, document())
        with pytest.raises(ValueError):
            write_trace_html(task, document())
    assert not (outside / "trace.html").exists()


def trace_with(root_extra=None, contents=()):
    """A one-span trace whose root carries the given link fields."""
    entry = {"span_id": "0" * 16, "parent_span_id": None, "name": "task", "kind": "span",
             "offset_seconds": 0.0, "duration_seconds": 1.0, "status": "ok", "attributes": {},
             "content_refs": [], "content_status": None}
    entry.update(root_extra or {})
    return document(spans=[entry], contents=list(contents))


def stored(content_id="c1", owner="0" * 16, **overrides):
    record = {"content_id": content_id, "owner_span_id": owner, "kind": "wire_request",
              "source": "wire_request", "availability": "captured", "text": '{"a": 1}',
              "original_bytes": 8, "captured_bytes": 8, "truncated": False, "redacted": False,
              "excerpt_mode": None}
    record.update(overrides)
    return record


def test_validate_trace_accepts_a_consistent_document():
    document_ = trace_with({"content_refs": ["c1"], "content_status": "captured"}, [stored()])
    assert validate_trace(document_) is document_


def test_validate_trace_rejects_a_content_reference_that_does_not_resolve():
    with pytest.raises(ValueError):
        validate_trace(trace_with({"content_refs": ["nope"]}))


def test_validate_trace_accepts_content_shared_by_several_spans():
    """Content is stored once and cited by every span that saw it.

    Deduplication is the whole point of content ids, so a record legitimately has more
    than one citing span.  `owner_span_id` names the first of them; requiring every
    reference to be that one would reject the ordinary case of two model calls whose
    provider returned no reasoning at all.
    """
    shared = stored(owner="0" * 16)
    other = {"span_id": "1" * 16, "parent_span_id": "0" * 16, "name": "second", "kind": "span",
             "offset_seconds": 1.0, "duration_seconds": 1.0, "status": "ok", "attributes": {},
             "content_refs": ["c1"], "content_status": "not_returned"}
    document_ = document(spans=[{"span_id": "0" * 16, "parent_span_id": None, "name": "task",
                                 "kind": "span", "offset_seconds": 0.0, "duration_seconds": 2.0,
                                 "status": "ok", "attributes": {},
                                 "content_refs": ["c1"], "content_status": "not_returned"}, other],
                         contents=[shared])

    assert validate_trace(document_) is document_
    assert other["content_refs"] == ["c1"]


def test_validate_trace_still_rejects_a_reference_with_no_record():
    with pytest.raises(ValueError):
        validate_trace(trace_with({"content_refs": ["c1"]}, [stored(content_id="other")]))


def test_validate_trace_rejects_a_parent_that_is_not_in_the_document():
    entry = {"span_id": "1" * 16, "parent_span_id": "9" * 16, "name": "tool.Read", "kind": "span",
             "offset_seconds": 0.0, "duration_seconds": 1.0, "status": "ok", "attributes": {}}
    with pytest.raises(ValueError):
        validate_trace(document(spans=[entry]))


def test_validate_trace_rejects_a_parent_cycle():
    first = {"span_id": "1" * 16, "parent_span_id": "2" * 16, "name": "a", "kind": "span",
             "offset_seconds": 0.0, "duration_seconds": 1.0, "status": "ok", "attributes": {}}
    second = {"span_id": "2" * 16, "parent_span_id": "1" * 16, "name": "b", "kind": "span",
              "offset_seconds": 0.0, "duration_seconds": 1.0, "status": "ok", "attributes": {}}
    with pytest.raises(ValueError):
        validate_trace(document(spans=[first, second]))


def test_captured_content_is_shown_once_as_text():
    hostile = "<script>alert('x')</script>"
    record = stored(text=json.dumps({"content": hostile}), captured_bytes=len(hostile))

    page = render_trace(trace_with({"content_refs": ["c1"]}, [record]))

    # Captured content is data to look at, never markup to run.
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert page.count("&lt;script&gt;") == 1, "each content id is rendered once"


def test_a_missing_capture_is_shown_with_its_reason():
    record = stored(availability="not_returned", text=None, captured_bytes=0)

    page = render_trace(trace_with({"content_refs": ["c1"], "content_status": "not_returned"}, [record]))

    assert "not_returned" in page
    # Not drawn as content that happened to be blank: the reason is what is shown.
    assert "<pre></pre>" not in page
    assert '<p class="unknown">not_returned</p>' in page


def test_the_page_marks_an_incomplete_content_set():
    page = render_trace(document(content_complete=False, contents=[]))
    assert "missing or was cut short" in page


def test_an_oversized_page_is_refused():
    """The page has a ceiling too, and a document past it is refused rather than cut."""
    oversized = document(spans=[span(f"{index:016x}", "tool.Read", tool="x" * 400)
                                for index in range(20000)])

    with pytest.raises(ValueError):
        render_trace(oversized)


@pytest.mark.parametrize("payload", [
    {"schema_version": 1, "spans": [1], "summary": {}},
    {"schema_version": 1, "spans": [None], "summary": {}},
    {"schema_version": 1, "spans": [], "summary": [1, 2]},
    {"schema_version": 1, "spans": "not a list", "summary": {}},
])
def test_a_schema_valid_but_malformed_trace_is_rejected(tmp_path, payload):
    """The stored file is hand-editable, so its shape is untrusted too.

    Only its version was checked before, so a structurally wrong document reached the
    renderer and died with a traceback and exit 1 instead of the fixed diagnostic.
    """
    task = tmp_path / "task" / TRACE_DIRECTORY
    task.mkdir(parents=True)
    (task / TRACE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        read_trace(task.parent)
    assert cli.main(["trace", str(task.parent)]) == 2


def test_an_oversized_stored_trace_is_refused_before_it_is_read(tmp_path):
    """The viewer bounds its input rather than loading whatever it is handed."""
    task = tmp_path / "task" / TRACE_DIRECTORY
    task.mkdir(parents=True)
    (task / TRACE_FILENAME).write_bytes(b"x" * (MAX_READ_BYTES + 1))

    with pytest.raises(ValueError):
        read_trace(task.parent)
    assert cli.main(["trace", str(task.parent)]) == 2


def test_a_trace_that_cannot_be_encoded_is_a_warning(tmp_path, capsys):
    """``write_trace`` is public and can be handed anything; a bad value is still a warning."""
    (tmp_path / TRACE_DIRECTORY).mkdir()

    assert cli._write_trace_or_warn(tmp_path, {"schema_version": 1, "not_encodable": object()}) is False
    assert cli.TRACE_WRITE_WARNING in capsys.readouterr().err


def test_observability_output_cannot_redirect_to_artifacts(tmp_path):
    task = tmp_path / "task"
    (task / "artifacts").mkdir(parents=True)
    write_trace(task, document())

    # There is no output option to point somewhere else, and the fixed destination
    # stays inside the task's own observability directory.
    with pytest.raises(SystemExit):
        cli.main(["trace", str(task), "--output", str(task / "artifacts/trace.html")])

    path = write_trace_html(task, document())
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


def native_document():
    from reproagent.observability import trace_session,span
    with trace_session() as recorder:
        with span('verify'):
            pass
        return recorder.finish(task_id='new',status='DONE',main_duration=1)


def test_schema1_page_rebuild_remains_compatible(tmp_path):
    legacy=document()
    write_trace(tmp_path,legacy)
    before=(tmp_path/'observability'/'trace.json').read_bytes()
    write_trace_html(tmp_path,read_trace(tmp_path))
    assert (tmp_path/'observability'/'trace.json').read_bytes()==before
    assert read_trace(tmp_path)['schema_version']==1


def test_schema2_displays_native_domain_and_reasoning_links():
    doc=native_document()
    doc['spans'][1]['otel_status']='ERROR'
    doc['spans'][1]['status']='ok'
    page=render_trace(doc)
    assert 'OTel: ERROR' in page and 'reproagent' in page


def test_missing_parent_and_late_span_are_explicitly_partial():
    doc=native_document()
    doc['spans'][1]['parent_span_id']='f'*16
    with pytest.raises(ValueError): validate_trace(doc)
    doc['partial']=True
    page=render_trace(doc)
    assert 'parent not retained' in page


def test_schema2_rejects_invalid_native_ids():
    doc=native_document()
    doc['spans'][1]['otel_span_id']='not-an-otel-id'
    with pytest.raises(ValueError): validate_trace(doc)


def island(page):
    import re
    return re.search(r'<script id="trace-view-data" type="application/json">(.*?)</script>', page, re.S).group(1)


def test_data_island_cannot_be_closed_by_trace_content():
    from reproagent.trace_view_model import build_trace_view
    hostile = '</script><script>window.__trace_xss_marker=1</script>" onerror="x\u2028\u2029&'
    doc = document(spans=[span("0"*16, hostile, reason=hostile)])
    raw = island(render_trace(doc))
    assert '<' not in raw and '>' not in raw and '&' not in raw
    assert '\u2028' not in raw and '\u2029' not in raw
    assert json.loads(raw) == build_trace_view(doc)


def test_shared_body_is_stored_once_in_html():
    doc=trace_with({"content_refs":["c1"]},[stored(text="ONE_SHARED_BODY")])
    doc["spans"].append(span("1"*16,"tool.Read",parent="0"*16))
    doc["spans"][1]["content_refs"]=["c1"]
    page=render_trace(doc)
    assert page.count("ONE_SHARED_BODY")==1
    assert page.count('id="trace-content-0"')==1
    assert "ONE_SHARED_BODY" not in island(page)


def test_only_packaged_module_is_executable():
    import base64,hashlib,re
    from importlib.resources import files
    source=files("reproagent").joinpath("resources/trace_viewer.mjs").read_text(encoding="utf-8")
    page=render_trace(document())
    scripts=re.findall(r'<script([^>]*)>(.*?)</script>',page,re.S)
    assert len(scripts)==2
    assert scripts[1][1]==source and 'type="module"' in scripts[1][0]
    digest=base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()
    assert "script-src 'sha256-"+digest+"'" in page
    assert "connect-src 'none'" in page
    for forbidden in ("innerHTML", "eval(", "new Function", "document.write", "fetch(", "XMLHttpRequest", "WebSocket"):
        assert forbidden not in source


def test_static_fallback_survives_missing_javascript():
    doc=trace_with({"content_refs":["c1"]},[stored(text="FALLBACK_BODY")])
    page=render_trace(doc)
    assert '<div id="trace-fallback">' in page
    assert '<div id="trace-app" hidden>' in page
    assert "FALLBACK_BODY" in page and "Decisions" in page
