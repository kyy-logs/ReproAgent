import json
import pytest
from reproagent.observability_content import ContentStore
from reproagent.observability import TraceRecorder, span, write_trace


def test_absent_reasoning_is_informational():
    store=ContentStore()
    store.capture(owner_span_id="a"*16,kind="provider_reasoning",value=None,
                  source="provider_reasoning",availability="not_returned")
    r=store.records()[0]
    assert store.complete
    assert r["original_bytes"] is None
    assert r["reason_code"] == "PROVIDER_NOT_RETURNED"


def test_body_loss_does_not_invalidate_token_counts():
    with TraceRecorder() as recorder:
        with span("model.http_attempt",attributes={"usage":{"input_tokens":10,"output_tokens":5}}) as h:
            h.capture("wire_request","x"*900000,source="wire_request")
        doc=recorder.finish(task_id="one",status="DONE",main_duration=1)
    assert doc["partial"] and not doc["content_complete"]
    assert doc["metrics_complete"] and doc["summary"]["usage"]["complete"]
    assert doc["capture_health"]["issues"][0]["code"] == "RECORD_BYTE_LIMIT"
    assert doc["warnings"]


def test_capacity_reasons_and_observation_fault_are_not_confused(monkeypatch):
    import reproagent.observability_content as module
    monkeypatch.setattr(module,"MAX_CONTENT_ITEMS",1)
    s=ContentStore()
    for text in ("one","two"):
        s.capture(owner_span_id="a"*16,kind="wire_request",value=text,source="wire_request")
    assert s.issues()[-1]["code"] == "CONTENT_ITEM_LIMIT"
    assert s.issues()[-1]["limit_value"] == 1
    s=ContentStore()
    s.capture(owner_span_id="a"*16,kind="wire_request",value=object(),source="wire_request")
    assert s.issues()[0]["code"] == "CAPTURE_EXCEPTION"
    assert s.records()[0]["availability"] == "capture_error"


def test_json_writer_refuses_oversize_before_writing(tmp_path,monkeypatch):
    import reproagent.observability as module
    monkeypatch.setattr(module,"MAX_DOCUMENT_BYTES",100)
    with pytest.raises(ValueError):
        write_trace(tmp_path,{"text":"x"*200})
    assert not (tmp_path/"observability/trace.json").exists()


def test_native_attribute_boundary_has_a_specific_reason():
    from opentelemetry import trace
    with TraceRecorder() as recorder:
        with trace.get_tracer("agentscope").start_as_current_span("chat") as s:
            s.set_attribute("gen_ai.operation.name","chat")
            s.set_attribute("gen_ai.input.messages","x"*65536)
        doc=recorder.finish(task_id="one",status="DONE",main_duration=1)
    r=next(c for c in doc["contents"] if c["source"]=="sdk_model_input")
    assert r["reason_code"]=="OTEL_ATTRIBUTE_POSSIBLY_TRUNCATED"
    assert r["original_bytes"] is None
    assert doc["metrics_complete"]


def test_wire_input_under_256k_is_retained_whole():
    store=ContentStore();text='HEAD'+('x'*80000)+'MIDDLE'+('x'*1000)+'TAIL'
    store.capture(owner_span_id='a'*16,kind='wire_request',value={'messages':[text]},source='wire_request')
    r=store.records()[0]
    assert not r['truncated'] and 'MIDDLE' in r['text']
    assert store.complete


def test_final_record_size_includes_byte_count_and_escaping(monkeypatch):
    import reproagent.observability_content as module
    monkeypatch.setattr(module,'MAX_CONTENT_BYTES',550)
    store=ContentStore()
    for i in range(230,285):
        store.capture(owner_span_id='a'*16,kind='wire_request',value=('x'*i),source='wire_request')
    assert all(len(json.dumps(r,ensure_ascii=False).encode())<=550 for r in store.records())


def test_twenty_one_inputs_remain_bounded_and_visible():
    store=ContentStore()
    for i in range(21):
        for source in ('sdk_model_input','wire_request'):
            store.capture(owner_span_id=f'{i:016x}',kind=source,value=str(i)+' HEAD '+('x'*80000)+' TAIL',source=source)
    assert store.complete
    assert len(store.records())==42
    assert all(not r['truncated'] and 'TAIL' in r['text'] for r in store.records())
