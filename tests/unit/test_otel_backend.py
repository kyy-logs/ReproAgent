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


def test_explicit_foreign_context_cannot_create_a_second_task_trace():
    from reproagent.otel_backend import open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    from opentelemetry.context import Context
    from opentelemetry.trace import NonRecordingSpan,SpanContext,TraceFlags
    foreign=trace.set_span_in_context(NonRecordingSpan(SpanContext(
        trace_id=123,span_id=456,is_remote=True,trace_flags=TraceFlags(1))),Context())
    with open_task_trace(TaskTraceSink()) as session:
        child=trace.get_tracer('test').start_span('foreign-context',context=foreign)
        assert not child.is_recording()
        child.end()


def test_nested_disabled_session_does_not_capture_into_outer_session():
    from reproagent.observability import trace_session,span,tracing_enabled
    with trace_session() as recorder:
        with trace_session(enabled=False) as disabled:
            assert disabled is None and not tracing_enabled()
            with span('disabled-task-content'): pass
        doc=recorder.finish(task_id='outer',status='DONE',main_duration=1)
    assert 'disabled-task-content' not in {s['name'] for s in doc['spans']}


def test_untraced_facade_runs_without_optional_runtime_imports():
    from pathlib import Path
    code="import sys;sys.path.insert(0,"+repr(str(Path(__file__).resolve().parents[2]/'src'))+");from reproagent.observability import span,mark,record_check;exec(\"with span('untraced'): assert record_check('binding',True)\");assert 'opentelemetry' not in sys.modules"
    result=subprocess.run([sys.executable,'-S','-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_recorder_constructor_failure_is_nonfatal(monkeypatch,capsys):
    from reproagent import observability
    calls=[]
    def broken(**kwargs): raise RuntimeError('observer-constructor-only')
    monkeypatch.setattr(observability,'TraceRecorder',broken)
    with observability.trace_session() as recorder:
        calls.append('domain-run')
        assert recorder is None
    assert calls==['domain-run']
    assert 'observer-constructor-only' not in capsys.readouterr().err
