import json
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


def test_native_projection_redacts_names_status_and_exception_events():
    from reproagent.otel_backend import open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    secret = "test-private-key"
    with open_task_trace(TaskTraceSink(secrets=(secret,))) as session:
        with trace.get_tracer("agentscope").start_as_current_span("chat " + secret) as s:
            s.set_attribute("gen_ai.operation.name", "chat")
            s.set_attribute("gen_ai.input.messages", json.dumps({"password": "unknown-password", "text": secret}))
            s.set_attribute("gen_ai.tool.call.id", "RAW_CALL_ID")
            s.set_attribute("unknown.attribute", "UNSAFE_EXTRA")
            s.set_status(Status(StatusCode.ERROR, secret))
            s.record_exception(ValueError(secret))
        session.close()
        doc=session.sink.finish(task_id="one",status="FAILED",main_duration=1)
    text=json.dumps(doc)
    assert not any(x in text for x in (secret, "unknown-password", "RAW_CALL_ID", "UNSAFE_EXTRA"))
    native = next(s for s in doc["spans"] if s["instrumentation_scope"] == "agentscope")
    assert native["otel_status"] == "ERROR"
    assert native["content_refs"]


def test_native_content_sources_and_limits():
    from reproagent.otel_backend import open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    with open_task_trace(TaskTraceSink()) as session:
        with trace.get_tracer("agentscope").start_as_current_span("chat offline") as s:
            s.set_attribute("gen_ai.operation.name", "chat")
            s.set_attribute("gen_ai.input.messages", json.dumps({"text": "中" * 40000}, ensure_ascii=False))
        session.close()
        doc=session.sink.finish(task_id="one",status="DONE",main_duration=1)
    content=next(c for c in doc["contents"] if c["source"] == "sdk_model_input")
    assert content["truncated"]
    assert len(json.dumps(content,ensure_ascii=False).encode()) <= 32768


def test_dropped_sdk_attributes_mark_partial():
    from reproagent.otel_backend import open_task_trace
    from reproagent.otel_projection import TaskTraceSink
    with open_task_trace(TaskTraceSink()) as session:
        with trace.get_tracer("agentscope").start_as_current_span("chat offline") as s:
            for i in range(80): s.set_attribute("unknown."+str(i), i)
        session.close()
        doc=session.sink.finish(task_id="one",status="DONE",main_duration=1)
    assert doc["partial"] and not doc["metrics_complete"]
