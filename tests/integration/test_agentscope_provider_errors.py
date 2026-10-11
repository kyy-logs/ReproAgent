"""Provider rejection diagnostics through the real SDK, without a live provider."""
import asyncio
import json

import httpx
import pytest

from reproagent.core.models import ModelRequest
from reproagent.observability import trace_session
from reproagent.store import TaskStore
from reproagent.trace_rendering import render_trace
from tests.integration.test_agentscope_gateway import build_gateway, context, reply


@pytest.mark.parametrize("status,count,category", [
    (400, 1, "input"), (401, 1, "authentication"),
    (429, 3, "rate_limit"), (503, 3, "provider"),
])
def test_provider_failure_keeps_status_and_bounded_redacted_error(tmp_path, monkeypatch, status, count, category):
    secret = "synthetic-private-api-key"
    monkeypatch.setenv("REPROAGENT_API_KEY", secret)
    store = TaskStore(tmp_path)
    def handle(request):
        return httpx.Response(status, json={"error": {
            "code": "invalid_request", "type": "request_error",
            "message": "request rejected " + secret + " " + "长" * 3000,
            "api_key": secret, "debug": "PRIVATE_DEBUG_FIELD"}},
            headers={"x-private-header": "PRIVATE_HEADER_VALUE"})
    gateway = build_gateway(handle, store)
    async def run():
        try:
            with pytest.raises(RuntimeError) as raised:
                await gateway.complete(ModelRequest(({"role":"user", "content":"Return JSON"},), "learning"), context())
            assert raised.value.status_code == status
            assert secret not in str(raised.value)
        finally:
            await gateway.aclose()
    # Redaction must work even when an embedding caller did not register the key.
    with trace_session() as recorder:
        asyncio.run(run())
        document = recorder.finish(task_id="offline", status="DONE", main_duration=0)
    events, errors = store.read_events()
    attempts = [event.payload for event in events if event.kind == "model.attempt"]
    assert not errors and len(attempts) == count
    assert all(a["http_status_code"] == status and a["outcome"] == "http_error" for a in attempts)
    assert all(a["usage"] == {} and a["cost_value"] is None for a in attempts)
    assert "request rejected" not in json.dumps(attempts)
    spans = [s for s in document["spans"] if s["name"] == "model.http_attempt"]
    assert len(spans) == count and all(s["attributes"]["http_status_code"] == status for s in spans)
    logical = next(s for s in document["spans"] if s["name"] == "model.logical")
    assert logical["attributes"]["error_category"] == category
    previews = [c for c in document["contents"] if c["source"] == "provider_error"]
    assert previews and all(len(c["text"].encode()) < 4096 for c in previews)
    assert "request rejected" in previews[0]["text"] and "<redacted>" in previews[0]["text"]
    assert json.loads(previews[0]["text"])["truncated"] is True
    encoded = json.dumps(document, ensure_ascii=False)
    assert all(value not in encoded for value in (secret, "PRIVATE_DEBUG_FIELD", "PRIVATE_HEADER_VALUE"))
    page = render_trace(document)
    assert "request rejected" in page and "http_status_code" in page


def test_retry_success_does_not_inherit_failed_attempt_status(tmp_path):
    store, calls = TaskStore(tmp_path), []
    def handle(request):
        calls.append(request)
        return httpx.Response(503, json={"error":{"message":"temporary"}}) if len(calls) == 1 else reply()
    gateway = build_gateway(handle, store)
    async def run():
        try:
            return await gateway.complete(ModelRequest(({"role":"user","content":"Return JSON"},)), context())
        finally:
            await gateway.aclose()
    assert asyncio.run(run()).text == "{}"
    events, _ = store.read_events()
    attempts = [e.payload for e in events if e.kind == "model.attempt"]
    assert [(a["http_status_code"], a["outcome"]) for a in attempts] == [(503, "http_error"), (200, "completed")]
