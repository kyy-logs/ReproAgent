"""The bounded, task-local trace recorder.

These tests pin the recorder's own contract -- isolation between tasks, bounded
memory, a whitelist instead of free text, and a summary that charges HTTP, token
and cost once, on the leaf that actually reached the wire.  Nothing here touches
the SDK or the CLI; those arrive in later tasks.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from reproagent import observability
from reproagent.observability import (
    MAX_ATTRIBUTE_BYTES,
    MAX_SPANS,
    TraceRecorder,
    mark,
    record_check,
    span,
    tool_call_key,
    trace_session,
    tracing_enabled,
)


def named(document, name):
    return [entry for entry in document["spans"] if entry["name"] == name]


class FakeClock:
    """A clock the test advances by hand, so durations are exact."""

    def __init__(self, value=1000.0):
        self.value = value
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.value

    def advance(self, delta):
        self.value += delta


def test_disabled_trace_is_noop(monkeypatch):
    # With tracing off there is no recorder at all, so nothing may reach for a
    # clock or a file.  Pointing the module's ``time`` at an exploding stub makes
    # any such reach a failure rather than something to notice by eye.
    def explode(*_args, **_kwargs):
        raise AssertionError("the clock must not be read while tracing is off")

    monkeypatch.setattr(observability, "time", SimpleNamespace(monotonic=explode, time=explode))

    with trace_session(enabled=False) as recorder:
        assert recorder is None
        assert tracing_enabled() is False
        with span("explore") as handle:
            handle.annotate(purpose="exploration")
        mark("permission", attributes={"result_code": "ALLOWED"})


def test_nested_spans_use_monotonic_duration():
    clock, wall = FakeClock(1000.0), FakeClock(5000.0)

    with TraceRecorder(clock=clock, wall_clock=wall) as recorder:
        with span("explore") as outer:
            clock.advance(0.5)
            wall.advance(0.5)
            with span("tool.Read") as inner:
                inner.annotate(tool="Read", result_code="ALLOWED")
                clock.advance(1.5)
                wall.advance(1.5)
            clock.advance(1.0)
            wall.advance(1.0)
        outer.annotate(purpose="exploration")

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=3.0)
    spans = {entry["name"]: entry for entry in document["spans"]}

    assert spans["tool.Read"]["duration_seconds"] == pytest.approx(1.5)
    assert spans["explore"]["duration_seconds"] == pytest.approx(3.0)
    # The wall clock places the span in the timeline; the monotonic one times it.
    assert spans["explore"]["offset_seconds"] == pytest.approx(0.0)
    assert spans["tool.Read"]["offset_seconds"] == pytest.approx(0.5)
    # The parent link comes from this task's own stack.
    assert spans["tool.Read"]["parent_span_id"] == spans["explore"]["span_id"]
    assert spans["explore"]["parent_span_id"] is None
    assert len(document["trace_id"]) == 32
    assert len(spans["explore"]["span_id"]) == 16

    assert document["schema_version"] == 1
    assert document["task_id"] == "task-1"
    assert document["status"] == "DONE"
    assert document["main_duration"] == 3.0
    assert document["partial"] is False
    assert document["metrics_complete"] is True


def test_a_clock_that_goes_backwards_yields_zero_duration():
    clock, wall = FakeClock(1000.0), FakeClock(5000.0)

    with TraceRecorder(clock=clock, wall_clock=wall) as recorder:
        with span("explore"):
            clock.advance(-2.0)
            wall.advance(-2.0)

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    entry = document["spans"][0]

    # A step backwards is a broken clock, not a negative duration.
    assert entry["duration_seconds"] == 0.0
    assert entry["offset_seconds"] is None or entry["offset_seconds"] >= 0.0


def test_concurrent_tasks_do_not_share_parent():
    barrier = threading.Barrier(2)
    documents = {}

    def worker(name):
        with trace_session(enabled=True) as recorder:
            # Both tasks are inside a session at the same time.
            barrier.wait(timeout=10)
            with span(f"explore.{name}"):
                with span(f"tool.{name}"):
                    pass
            documents[name] = recorder.finish(task_id=name, status="DONE", main_duration=1.0)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert documents["a"]["trace_id"] != documents["b"]["trace_id"]
    for name, document in documents.items():
        spans = {entry["name"]: entry for entry in document["spans"]}
        # Each document holds exactly its own two spans, linked to each other.
        assert sorted(spans) == [f"explore.{name}", f"tool.{name}"]
        assert spans[f"tool.{name}"]["parent_span_id"] == spans[f"explore.{name}"]["span_id"]
        assert spans[f"explore.{name}"]["parent_span_id"] is None


def test_recorder_failure_preserves_domain_exception():
    with TraceRecorder() as recorder:
        with pytest.raises(ValueError, match="domain boom"):
            with span("explore") as handle:
                # A value the recorder cannot project: an observation fault, which
                # must be contained rather than raised in place of the domain error.
                handle.annotate(tool=object())
                raise ValueError("domain boom")

        # Cancellation is a domain control-flow signal, never swallowed.
        with pytest.raises(asyncio.CancelledError):
            with span("explore"):
                raise asyncio.CancelledError()

    document = recorder.finish(task_id="task-1", status="FAILED", main_duration=1.0)

    assert document["metrics_complete"] is False
    assert document["warnings"], "an observation fault must leave a fixed diagnostic"
    blob = json.dumps(document)
    # Diagnostics are fixed text: no exception message, no domain content.
    assert "domain boom" not in blob


def test_caps_mark_partial_without_faking_totals():
    with TraceRecorder() as recorder:
        for index in range(MAX_SPANS + 5):
            mark("tool.point", attributes={"tool": "Read", "attempt": index})

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)

    assert document["partial"] is True
    assert document["metrics_complete"] is False
    assert len(document["spans"]) == MAX_SPANS
    assert document["summary"]["span_count"] == MAX_SPANS
    # The summary counts what was kept, not what was attempted.
    assert document["summary"]["http_attempts"] == 0
    assert document["warnings"]

    # The per-entry ceiling drops the oversized entry whole rather than storing a
    # truncated one that would read as complete.
    with TraceRecorder() as bounded:
        with span("explore") as handle:
            handle.annotate(purpose="x" * MAX_ATTRIBUTE_BYTES, tool="Read")
        with span("explore") as handle:
            handle.annotate(tool="Read")

    bounded_document = bounded.finish(task_id="task-2", status="DONE", main_duration=1.0)
    first, second = bounded_document["spans"]

    assert "purpose" not in first["attributes"]
    assert first["attributes"]["tool"] == "Read"
    assert second["attributes"]["tool"] == "Read"
    assert bounded_document["partial"] is True


def test_metadata_projection_excludes_content_and_credentials():
    secret = "sk-live-abcdef0123456789"

    with TraceRecorder(secrets=(secret,)) as recorder:
        with span("explore") as handle:
            handle.annotate(
                purpose="exploration",
                tool="Read",
                result_code="ALLOWED",
                candidate_id="cand-1",
                attempt=2,
                output_limit=4096,
                # None of the following belong in a trace.
                prompt="please read the whole parser",
                response="the parser looks wrong",
                source="def parse(values): ...",
                tool_result={"stdout": "..."},
                reasoning="the model thought about it",
                authorization=f"Bearer {secret}",
                model_config={"api_key": secret},
                tool_name=secret,
            )
        # An *allowed* field whose value happens to carry a credential must be
        # redacted rather than merely relying on the key being dropped.
        with span("explore") as handle:
            handle.annotate(result_code=f"DENIED for {secret}")

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    attributes = document["spans"][0]["attributes"]

    assert attributes["purpose"] == "exploration"
    assert attributes["tool"] == "Read"
    assert attributes["candidate_id"] == "cand-1"
    assert attributes["attempt"] == 2
    assert attributes["output_limit"] == 4096
    for dropped in ("prompt", "response", "source", "tool_result", "reasoning",
                    "authorization", "model_config"):
        assert dropped not in attributes

    # The allowed field keeps its shape, with the credential masked in place.
    assert document["spans"][1]["attributes"]["result_code"] == "DENIED for <redacted>"

    blob = json.dumps(document)
    assert secret not in blob


def test_only_http_leaves_contribute_usage_and_cost():
    with TraceRecorder() as recorder:
        with span("model.logical") as logical:
            logical.annotate(purpose="exploration", usage={"input_tokens": 10, "output_tokens": 4},
                             cost=0.001)
            for attempt in (1, 2):
                with span("model.http_attempt") as http:
                    http.annotate(attempt=attempt, usage={"input_tokens": 10, "output_tokens": 4},
                                  cost=0.001)

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    summary = document["summary"]

    assert summary["logical_calls"] == 1
    assert summary["http_attempts"] == 2
    # The retries share one logical call, and only the leaves are charged: not
    # 10 (logical) + 20 (leaves), and not 0.001 + 0.002 on top.
    assert summary["usage"]["input_tokens"] == 20
    assert summary["usage"]["output_tokens"] == 8
    assert summary["usage"]["complete"] is True
    assert summary["cost"]["amount"] == pytest.approx(0.002)
    assert summary["cost"]["complete"] is True


def test_unknown_usage_and_cost_stay_unknown():
    with TraceRecorder() as recorder:
        with span("model.http_attempt") as http:
            http.annotate(attempt=1, usage={"input_tokens": 10})

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    summary = document["summary"]

    # A missing figure is unknown, never a confident zero.
    assert summary["usage"]["output_tokens"] is None
    assert summary["usage"]["complete"] is False
    assert summary["cost"]["amount"] is None
    assert summary["cost"]["complete"] is False


def test_a_span_closed_from_another_context_still_propagates():
    """The event loop finalizes an abandoned stream from its own context.

    Resetting the span's context variable there would raise "created in a different
    Context" and replace whatever was travelling through the span -- the exact promise
    that a domain exception reaches the caller unchanged.
    """
    async def main():
        async def stream():
            with span("tool.Read"):
                yield 1
                yield 2

        with trace_session(enabled=True) as recorder:
            generator = stream()

            async def consume_first():
                return await generator.__anext__()

            await asyncio.create_task(consume_first())
            await generator.aclose()
            return recorder

    document = asyncio.run(main()).finish(task_id="task-1", status="DONE", main_duration=1.0)
    assert [entry["name"] for entry in document["spans"]] == ["tool.Read"]


def test_a_nested_session_does_not_inherit_a_foreign_parent():
    """A parent id must come from the recorder writing the document, never another task's."""
    with trace_session(enabled=True) as outer:
        with span("outer-work"):
            with trace_session(enabled=True) as inner:
                with span("inner-work"):
                    pass
            inner_document = inner.finish(task_id="inner", status="DONE", main_duration=1.0)
        outer_document = outer.finish(task_id="outer", status="DONE", main_duration=1.0)

    inner_span = inner_document["spans"][0]
    assert inner_span["name"] == "inner-work"
    # The outer span is still open here, but it belongs to another document.
    assert inner_span["parent_span_id"] is None
    assert inner_span["span_id"] not in {entry["span_id"] for entry in outer_document["spans"]}


def test_the_span_cap_never_orphans_a_kept_span(monkeypatch):
    """A parent that closes after the cap is hit must still be written.

    Children close before their parents, so the cap drops the long-lived spans first --
    exactly the ones every remaining span points at.  The result was a document the
    writer produced and its own reader refused.
    """
    monkeypatch.setattr(observability, "MAX_SPANS", 3)
    with TraceRecorder() as recorder:
        with span("task"):
            for index in range(6):
                with span(f"child-{index}"):
                    pass

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    kept = {entry["span_id"] for entry in document["spans"]}
    parents = {entry["parent_span_id"] for entry in document["spans"] if entry["parent_span_id"]}

    assert document["partial"] is True, "the cap was reached"
    assert len(kept) < 7, "the cap still dropped what it could"
    # The invariant that matters: every parent a kept span points at is itself kept.
    assert parents <= kept, "a kept span points at a parent that was dropped"

    from reproagent.trace_rendering import validate_trace
    validate_trace(document)


def test_finishing_freezes_the_document_it_returns():
    """A span that closes afterwards must not rewrite a document already handed out.

    The stored spans are the live list, so an async generator the loop finalizes late --
    or a caller finishing inside its own session -- would add timeline rows to a
    document whose summary still counted the spans it had at finish.  The two would
    disagree, and nothing would say the trace was incomplete.
    """
    recorder = TraceRecorder()
    with recorder:
        with span("before-finish"):
            pass
        document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
        with span("closes-after-finish"):
            pass

    assert [entry["name"] for entry in document["spans"]] == ["before-finish"]
    assert document["summary"]["span_count"] == len(document["spans"]) == 1


def test_a_span_captures_redacted_content_and_refers_to_it():
    with TraceRecorder(secrets=("sk-live-abcdef0123456789",)) as recorder:
        with span("sdk.model_round") as handle:
            content_id = handle.capture("wire_request", {"messages": [
                {"role": "user", "content": "compare with sk-live-abcdef0123456789"}]},
                source="wire_request")
        with span("tool.Read") as other:
            other.capture("sdk_tool_result", {"content": "ok"}, source="sdk_tool_result")

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)

    assert document["capture_mode"] == "content"
    assert document["content_complete"] is True
    entry = named(document, "sdk.model_round")[0]
    assert entry["content_refs"] == [content_id]
    assert entry["content_status"] == "captured"
    stored = {record["content_id"]: record for record in document["contents"]}
    assert "sk-live-abcdef0123456789" not in json.dumps(document)
    assert stored[content_id]["owner_span_id"] == entry["span_id"]


def test_a_span_that_captured_nothing_says_why():
    with TraceRecorder() as recorder:
        with span("model.logical") as handle:
            ref = handle.capture("provider_reasoning", None, source="provider_reasoning",
                                 availability="not_returned")

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    entry = named(document, "model.logical")[0]

    # The absence is recorded and attributed to the span, rather than left as an empty
    # list that reads like content nobody asked for.
    assert ref is not None
    assert entry["content_refs"] == [ref]
    assert entry["content_status"] == "not_returned"
    assert document["content_complete"] is False


def test_record_check_returns_the_value_and_records_the_outcome():
    with TraceRecorder() as recorder:
        with span("verify"):
            first = record_check("binding", True)
            second = record_check("protection_ok", False)
            # The caller keeps its own short-circuit: the second check failed, so the
            # third is never reached.
            third = record_check("framework_ok", True) if second else None

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)

    assert (first, second, third) == (True, False, None)
    checks = {entry["attributes"]["check"]: entry["attributes"]["result_code"]
              for entry in document["spans"] if "check" in entry["attributes"]}
    assert checks == {"binding": "PASS", "protection_ok": "FAIL"}
    # A check the program never reached leaves no record at all -- which is the only
    # honest way to keep it from reading as one that passed.
    assert "framework_ok" not in checks


def test_tool_call_key_separates_reused_ids_and_repeats_deterministically():
    with trace_session(enabled=True) as recorder:
        first = tool_call_key(sdk_call_id="call_1", step_index=3)
        again = tool_call_key(sdk_call_id="call_1", step_index=3)
        later = tool_call_key(sdk_call_id="call_1", step_index=4)

    # A provider that reuses one id across steps must still produce distinct keys...
    assert first != later
    # ...and the same call must always produce the same key.
    assert first == again
    assert len(first) == 32
    # The raw SDK id is not what is stored.
    assert "call_1" not in first


def test_tool_call_key_is_none_while_tracing_is_off():
    assert tool_call_key(sdk_call_id="call_1", step_index=0) is None


def test_learning_is_reported_separately_from_the_main_run():
    with TraceRecorder() as recorder:
        with span("learn"):
            pass

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=2.0,
                               learning={"seconds": 4.0, "cost": 0.0})

    # Learning has its own budget and cannot be folded into the main duration.
    assert document["main_duration"] == 2.0
    assert document["summary"]["learning"] == {"seconds": 4.0, "cost": 0.0}
    assert document["total_duration"] >= 0.0
