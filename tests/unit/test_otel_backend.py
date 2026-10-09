import asyncio
import json
import subprocess
import sys

from opentelemetry import trace


def test_provider_reused_without_global_reset():
    from reproagent.otel_backend import ensure_local_provider, open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    assert ensure_local_provider().available
    provider = trace.get_tracer_provider()
    with open_task_trace(TaskTraceSink()) as session:
        with trace.get_tracer("test").start_as_current_span("child"):
            pass
        session.close()
        doc = session.sink.finish(task_id="one", status="DONE", main_duration=1)
    assert trace.get_tracer_provider() is provider
    assert len(doc["trace_id"]) == 32
    assert all(len(s["span_id"]) == 16 for s in doc["spans"])
    assert next(s for s in doc["spans"] if s["name"] == "child")["parent_span_id"] == doc["root_span_id"]


def test_foreign_provider_disables_local_trace_without_touching_host():
    code = """from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from reproagent.otel_backend import ensure_local_provider
p=TracerProvider(); trace.set_tracer_provider(p)
r=ensure_local_provider()
assert not r.available and r.warning
assert trace.get_tracer_provider() is p
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_parallel_sessions_route_end_by_trace_id():
    from reproagent.otel_backend import open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    async def worker(name):
        with open_task_trace(TaskTraceSink()) as session:
            pending = trace.get_tracer("test").start_span(name)
            await asyncio.sleep(0)
            # Ending is allowed outside the current span context.
            pending.end()
            session.close()
            return session.sink.finish(task_id=name, status="DONE", main_duration=1)
    async def run():
        return await asyncio.gather(worker("one"), worker("two"))
    docs = asyncio.run(run())
    assert docs[0]["trace_id"] != docs[1]["trace_id"]
    assert {s["name"] for s in docs[0]["spans"]} == {"reproagent.task", "one"}
    assert {s["name"] for s in docs[1]["spans"]} == {"reproagent.task", "two"}


def test_disabled_after_enabled_is_not_sampled():
    from reproagent.otel_backend import ensure_local_provider
    assert ensure_local_provider().available
    s = trace.get_tracer("test").start_span("outside-session")
    assert not s.is_recording()


def test_late_span_never_reopens_a_finished_sink():
    from reproagent.otel_backend import open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    with open_task_trace(TaskTraceSink()) as session:
        pending = trace.get_tracer("test").start_span("late")
        session.close()
        doc = session.sink.finish(task_id="one", status="DONE", main_duration=1)
    before = json.dumps(doc)
    pending.end()
    assert json.dumps(doc) == before
    assert doc["partial"]
