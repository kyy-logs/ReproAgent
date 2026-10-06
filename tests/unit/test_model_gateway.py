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
