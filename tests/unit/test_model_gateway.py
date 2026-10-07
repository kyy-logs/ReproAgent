import asyncio
import importlib
import json

import httpx
import pytest
from reproagent.core.budget import Budget
from reproagent.core.models import BudgetLimits, CallContext, ModelConfig, ModelRequest


def gateway(handler):
    module = importlib.import_module('reproagent.adapters.models.provider')
    return module.ChatCompletionGateway(ModelConfig(base_url='https://offline.example/v1', model='offline'), transport=httpx.MockTransport(handler))


def context(): return CallContext(Budget(BudgetLimits()))
def request(): return ModelRequest(({'role':'user','content':'hello'},))
def reply(**extra): return httpx.Response(200, json={'id':'a','choices':[{'finish_reason':'stop','message':{'content':'{}'}}], **extra})


def test_rate_limit_has_at_most_two_extra_attempts():
    calls = []
    def handle(req): calls.append(req); return httpx.Response(429)
    with pytest.raises(Exception): asyncio.run(gateway(handle).complete(request(), context()))
    assert len(calls) == 3


def test_cancel_closes_inflight_request_and_sends_no_new_request():
    calls, stopped = [], []
    async def handle(req):
        calls.append(req)
        try: await asyncio.sleep(30)
        finally: stopped.append(True)
    async def run():
        ctx = context(); model = gateway(handle)
        pending = asyncio.create_task(model.complete(request(), ctx))
        while not calls: await asyncio.sleep(0)
        ctx.cancel_event.set()
        with pytest.raises(Exception): await pending
        with pytest.raises(Exception): await model.complete(request(), ctx)
    asyncio.run(run())
    assert len(calls) == 1 and stopped


def test_usage_missing_is_unknown_not_zero():
    result = asyncio.run(gateway(lambda req: reply()).complete(request(), context()))
    assert result.cost_kind == 'unknown' and result.cost_value is None


@pytest.mark.parametrize('body', [None, {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}, {'choices':[{'finish_reason':'stop','message':{}}]}])
def test_truncated_or_invalid_provider_response_is_protocol_error(body):
    model = gateway(lambda req: httpx.Response(200, json=body))
    with pytest.raises(ValueError): asyncio.run(model.complete(request(), context()))


@pytest.mark.parametrize('message', [{'content':'{'}, {}])
def test_truncated_content_preserves_returned_usage_and_cost(message):
    from dataclasses import replace
    from reproagent.core.protocol import ModelOutputError
    model=gateway(lambda req: httpx.Response(200,json={'choices':[{'finish_reason':'length','message':message}],
                                                     'usage':{'prompt_tokens':10,'completion_tokens':5}}))
    model.config=replace(model.config,input_cost_per_million=1,output_cost_per_million=1)
    events=[]
    class Store:
        def append_event(self,kind,refs,payload): events.append(payload)
    model.attempt_store=Store(); ctx=context()
    with pytest.raises(ModelOutputError):
        asyncio.run(model.complete(request(),ctx))
    assert events[0]['usage'] == {'prompt_tokens':10,'completion_tokens':5}
    assert ctx.budget.cost_spent > 0 and ctx.budget.unknown_cost_calls == 0


def test_credentials_never_enter_records(monkeypatch):
    monkeypatch.setenv('REPROAGENT_API_KEY', 'private-secret-123')
    calls = []
    def handle(req): calls.append(req); return reply()
    result = asyncio.run(gateway(handle).complete(request(), context()))
    assert calls[0].headers['Authorization'] == 'Bearer private-secret-123'
    from dataclasses import asdict
    assert 'private-secret-123' not in json.dumps(asdict(result))
    ctx = CallContext(Budget(BudgetLimits(model_cost_limit=1)))
    with pytest.raises(ValueError): asyncio.run(gateway(handle).complete(request(), ctx))


def attempt_events():
    events = []
    class Store:
        def append_event(self, kind, refs, payload): events.append(payload)
    return events, Store()


def test_finish_reason_classification_is_a_closed_set():
    from reproagent.adapters.models.provider import classify_finish_reason
    assert classify_finish_reason('stop') == 'stop' and classify_finish_reason('length') == 'length'
    assert classify_finish_reason('content_filter') == 'content_filter'
    assert classify_finish_reason('provider-private-reason') == 'other'
    assert classify_finish_reason('') == 'unknown' and classify_finish_reason(None) == 'unknown'


def test_failed_attempt_event_records_size_and_reason_not_raw_content():
    from reproagent.core.protocol import ModelOutputError
    secret = 'synthetic-reasoning-text'
    model = gateway(lambda req: httpx.Response(200, json={'choices':[{'finish_reason':'length','message':{'content':secret}}],
                                                          'usage':{'prompt_tokens':10,'completion_tokens':5}}))
    events, store = attempt_events(); model.attempt_store = store
    with pytest.raises(ModelOutputError):
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},), 'action', max_output_tokens=1234), context()))
    assert len(events) == 1 and events[0]['attempt'] == 1
    assert events[0]['response_kind'] == 'action' and events[0]['effective_output_limit'] == 1234
    assert events[0]['finish_reason'] == 'length' and events[0]['content_bytes'] == len(secret.encode())
    assert events[0]['usage'] == {'prompt_tokens':10,'completion_tokens':5} and events[0]['cost_kind'] == 'unknown'
    assert secret not in json.dumps(events[0])


@pytest.mark.parametrize('body,code,retryable', [
    ({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}, 'OUTPUT_TRUNCATED', False),
    ({'choices':[{'finish_reason':'content_filter','message':{'content':'{}'}}]}, 'OUTPUT_FILTERED', False),
    ({'choices':[{'finish_reason':'stop','message':{'content':'{}','tool_calls':[{'id':'call-1'}]}}]}, 'OUTPUT_FILTERED', False),
    ({'choices':[{'finish_reason':'stop','message':{'content':''}}]}, 'EMPTY_OUTPUT', True),
    ({'choices':[{'finish_reason':'stop','message':{'content':{}}}]}, 'EMPTY_OUTPUT', True),
    ({'choices':[{'finish_reason':'stop','message':[]}]}, 'INVALID_PROTOCOL', True),
])
def test_provider_failure_classes_never_repeat_the_identical_request(body, code, retryable):
    from reproagent.core.protocol import ModelOutputError
    calls = []
    def handle(req): calls.append(req); return httpx.Response(200, json=body)
    events, store = attempt_events()
    model = gateway(handle); model.attempt_store = store
    with pytest.raises(ModelOutputError) as error:
        asyncio.run(model.complete(request(), context()))
    assert error.value.code == code and error.value.retryable is retryable
    assert len(calls) == 1 and len(events) == 1 and events[0]['outcome'] == code


def test_attempt_outcome_is_projected_onto_a_closed_set():
    from reproagent.adapters.models.provider import attempt_payload
    payload = attempt_payload(1, {}, 'unknown', None, outcome='provider-private-outcome',
                              response_kind='action', effective_output_limit=1)
    assert payload['outcome'] == 'unknown'


def test_unparsable_provider_body_is_correctable_but_never_repeated():
    from reproagent.core.protocol import ModelOutputError
    calls = []
    def handle(req): calls.append(req); return httpx.Response(200, content=b'not-json', headers={'content-type':'application/json'})
    with pytest.raises(ModelOutputError) as error:
        asyncio.run(gateway(handle).complete(request(), context()))
    assert error.value.code == 'INVALID_PROTOCOL' and error.value.retryable is True
    assert len(calls) == 1


def test_completed_attempt_event_stays_within_the_configured_limit():
    model = gateway(lambda req: reply())
    events, store = attempt_events(); model.attempt_store = store
    result = asyncio.run(model.complete(request(), context()))
    assert result.text == '{}' and len(events) == 1
    assert events[0]['finish_reason'] == 'stop' and events[0]['content_bytes'] == 2
    assert events[0]['response_kind'] == 'action' and events[0]['effective_output_limit'] == 4096
    assert events[0]['outcome'] == 'completed'
