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


def test_native_tool_parts_strip_raw_ids_and_unknown_names():
    from reproagent.observability import trace_session
    with trace_session() as recorder:
        with trace.get_tracer('agentscope').start_as_current_span('chat offline') as s:
            s.set_attribute('gen_ai.operation.name','chat')
            s.set_attribute('gen_ai.output.messages',json.dumps([{'role':'assistant','parts':[
                {'type':'tool_call','id':'RAW_NESTED_CALL_ID','name':'UNKNOWN_NESTED_TOOL','arguments':{'path':'example.py'}},
                {'type':'tool_call_response','id':'RAW_NESTED_CALL_ID','response':'valid-result'}]}]))
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1)
    text=json.dumps(doc)
    assert 'RAW_NESTED_CALL_ID' not in text and 'UNKNOWN_NESTED_TOOL' not in text
    assert 'valid-result' in text and 'example.py' in text


def test_event_names_are_redacted_before_storage():
    from reproagent.observability import trace_session,mark
    with trace_session(secrets=('PRIVATE_EVENT_SECRET',)) as recorder:
        mark('event PRIVATE_EVENT_SECRET')
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1)
    assert 'PRIVATE_EVENT_SECRET' not in json.dumps(doc)


def test_whole_metadata_entry_remains_bounded_with_long_names_and_many_refs():
    from reproagent.observability import trace_session,span
    with trace_session() as recorder:
        with span('long-'+('x'*3000)) as h:
            for i in range(100): h.capture('body',str(i),source='program_artifact')
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1)
    assert doc['partial']
    assert all(len(json.dumps(s,ensure_ascii=False).encode())<=2048 for s in doc['spans'])
