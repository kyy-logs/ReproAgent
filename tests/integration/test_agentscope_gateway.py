import asyncio
import json

import httpx
import pytest

pytest.importorskip('agentscope')

from reproagent.adapters.agentscope.gateway import AgentScopeModelGateway
from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
from reproagent.core.budget import Budget, BudgetStopped, BudgetedGateway
from reproagent.core.models import BudgetLimits, CallContext, ModelConfig, ModelRequest
from reproagent.core.protocol import ModelOutputError


def config(**kwargs):
    return ModelConfig(base_url='https://offline.example/v1', model='offline', output_limit_field='max_tokens', **kwargs)


def build_gateway(handler, store=None, **kwargs):
    """The structured-request gateway over the shared SDK model factory."""
    factory = AgentScopeModelFactory(config(**kwargs), store, transport=httpx.MockTransport(handler))
    return AgentScopeModelGateway(factory)


def context(**limits):
    return CallContext(Budget(BudgetLimits(**limits)))


def reply(content='{}', finish='stop', usage=None):
    data = {'id':'offline-id','object':'chat.completion','created':1,'model':'offline',
            'choices':[{'index':0,'finish_reason':finish,'message':{'role':'assistant','content':content}}]}
    if usage is not None:
        data['usage'] = usage
    return httpx.Response(200, json=data)


def test_real_sdk_preserves_parameters_usage_and_disables_hidden_retries():
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return reply(usage={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15})
    gateway = build_gateway(handle)
    result = asyncio.run(gateway.complete(ModelRequest(({'role':'system','content':'JSON only'}, {'role':'user','content':'hello'}), max_output_tokens=27), context()))
    assert len(calls) == 1 and calls[0]['max_tokens'] == 27
    assert 'max_completion_tokens' not in calls[0]
    assert calls[0]['response_format'] == {'type':'json_object'} and calls[0]['stream'] is False
    assert calls[0]['messages'] == [{'role':'system','content':'JSON only'}, {'role':'user','content':'hello'}]
    assert result.text == '{}' and result.usage['total_tokens'] == 15
    assert result.cost_value is None and result.cost_kind == 'unknown'


def test_compressed_response_is_rejected_before_decompression(monkeypatch):
    import gzip
    from agentscope.model import OpenAIChatModel
    consumed,closed,parsed,encodings=[],[],[],[]
    payload=gzip.compress(b'x'*5000000)
    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield payload
        async def aclose(self): closed.append(True)
    def handle(request):
        encodings.append(request.headers.get('accept-encoding'))
        return httpx.Response(200,stream=CompressedStream(),headers={'content-encoding':'gzip','content-type':'application/json'})
    def parse(*args,**kwargs):
        parsed.append(True)
        raise AssertionError('SDK must not parse compressed response')
    monkeypatch.setattr(OpenAIChatModel,'_parse_completion_response',parse)
    model=build_gateway(handle)
    with pytest.raises(ModelOutputError,match='content encoding'):
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)),context()))
    assert encodings==['identity'] and not consumed and closed and not parsed


def test_sdk_and_native_backends_resolve_the_same_limit_and_thinking_switch():
    calls=[]
    def handle(request): calls.append(json.loads(request.content)); return reply()
    def model(**kwargs): return build_gateway(handle, **kwargs)
    asyncio.run(model(max_output_tokens=8192, thinking_mode='disabled').complete(ModelRequest(({'role':'user','content':'hello'},)), context()))
    assert calls[-1]['max_tokens'] == 8192 and calls[-1]['thinking'] == {'type':'disabled'}
    asyncio.run(model(max_output_tokens=8192).complete(ModelRequest(({'role':'user','content':'hello'},), max_output_tokens=1024), context()))
    assert calls[-1]['max_tokens'] == 1024 and 'thinking' not in calls[-1]
    asyncio.run(model().complete(ModelRequest(({'role':'user','content':'hello'},)), context()))
    assert calls[-1]['max_tokens'] == 4096 and 'thinking' not in calls[-1]


@pytest.mark.parametrize('finish,content,code,retryable', [('length','{}','OUTPUT_TRUNCATED',False),
    ('content_filter','{}','OUTPUT_FILTERED',False), ('tool_calls','{}','OUTPUT_FILTERED',False),
    ('stop','','EMPTY_OUTPUT',True)])
def test_original_provider_finish_reason_and_empty_text_are_not_lost(finish, content, code, retryable):
    calls = []
    def handle(request): calls.append(request); return reply(content, finish, {'prompt_tokens':10,'completion_tokens':5})
    model = build_gateway(handle, input_cost_per_million=1, output_cost_per_million=1)
    ctx = context()
    with pytest.raises(ModelOutputError) as error:
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)), ctx))
    assert error.value.code == code and error.value.retryable is retryable
    assert len(calls) == 1 and ctx.budget.cost_spent > 0


def test_sdk_attempt_events_record_a_controlled_outcome_without_provider_text():
    secret='synthetic-reasoning-content'
    events=[]
    class Store:
        def append_event(self, kind, refs, payload): events.append(payload)
    model=build_gateway(lambda request: reply(secret, 'length', {'prompt_tokens':10,'completion_tokens':5}))
    model.attempt_store=Store()
    with pytest.raises(ModelOutputError):
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},),'verdict'), context()))
    assert events[0]['outcome']=='OUTPUT_TRUNCATED' and events[0]['backend']=='agentscope'
    assert events[0]['response_kind']=='verdict' and events[0]['finish_reason']=='length'
    assert events[0]['content_bytes']==len(secret.encode()) and secret not in json.dumps(events[0])


def test_sdk_rate_limit_has_only_three_counted_http_attempts():
    calls, events = [], []
    def handle(request): calls.append(request); return httpx.Response(429, json={'error':{'message':'limited'}})
    class Store:
        def append_event(self, kind, refs, payload): events.append((kind,payload))
    model = BudgetedGateway(build_gateway(handle), Store())
    with pytest.raises(RuntimeError): asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)), context()))
    assert len(calls) == 3 and len([event for event in events if event[0]=='model.attempt']) == 3


def test_sdk_cancel_closes_inflight_call_without_another_request():
    calls, closed = [], []
    async def handle(request):
        calls.append(request)
        try: await asyncio.sleep(30)
        finally: closed.append(True)
    async def run():
        ctx=context(); model=build_gateway(handle)
        pending=asyncio.create_task(model.complete(ModelRequest(({'role':'user','content':'hello'},)),ctx))
        while not calls: await asyncio.sleep(0)
        ctx.cancel_event.set()
        with pytest.raises(BudgetStopped): await pending
    asyncio.run(run())
    assert len(calls)==1 and closed


def test_sdk_http_errors_and_recorded_events_do_not_expose_key(monkeypatch):
    secret='synthetic-sdk-secret'
    monkeypatch.setenv('REPROAGENT_API_KEY',secret)
    def handle(request):
        assert request.headers['Authorization']=='Bearer '+secret
        return httpx.Response(401,json={'error':{'message':secret}})
    model=build_gateway(handle)
    with pytest.raises(RuntimeError) as error:
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)),context()))
    assert secret not in str(error.value)


def test_hard_cost_limit_is_rejected_without_sending_a_request():
    calls=[]
    def handle(request): calls.append(request); return reply()
    model=build_gateway(handle)
    with pytest.raises(ValueError): asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)),context(model_cost_limit=1)))
    assert not calls


def test_malformed_provider_field_names_are_not_exposed_in_protocol_errors():
    secret='synthetic-provider-private-field'
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=('{"'+secret+'":1,"'+secret+'":2}').encode(), headers={'content-type':'application/json'})
    model=build_gateway(handle)
    with pytest.raises(ModelOutputError) as error:
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)),context()))
    assert secret not in str(error.value)
    assert len(calls)==1


def test_oversized_stream_stops_reading_and_never_enters_sdk_parser(monkeypatch):
    from agentscope.model import OpenAIChatModel
    consumed,closed,parsed,calls=[],[],[],[]
    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(10):
                consumed.append(index)
                yield b'x'*600000
        async def aclose(self): closed.append(True)
    def handle(request):
        calls.append(request)
        return httpx.Response(200,stream=LargeStream(),headers={'content-type':'application/json'})
    def parse(*args,**kwargs):
        parsed.append(True)
        raise AssertionError('SDK must not parse an oversized body')
    monkeypatch.setattr(OpenAIChatModel,'_parse_completion_response',parse)
    events=[]
    class Store:
        def append_event(self, kind, refs, payload): events.append(payload)
    model=build_gateway(handle)
    model.attempt_store=Store()
    with pytest.raises(ModelOutputError,match='protocol limit') as error:
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)),context()))
    # The live streaming path has to classify the size failure like the post-read check.
    assert error.value.code=='RESPONSE_TOO_LARGE' and error.value.retryable is False
    assert len(calls)==1 and len(consumed)==2 and closed and not parsed
    assert events[0]['outcome']=='RESPONSE_TOO_LARGE' and events[0]['effective_output_limit']==4096


def test_sdk_reasoning_content_is_not_mistaken_for_invalid_final_text():
    def handle(request):
        response=reply()
        body=response.json()
        body['choices'][0]['message']['reasoning_content']='internal reasoning'
        return httpx.Response(200,json=body)
    result=asyncio.run(build_gateway(handle).complete(ModelRequest(({'role':'user','content':'hello'},)),context()))
    assert result.text=='{}' and 'internal reasoning' not in result.text


def test_sdk_deadline_stops_pending_http_without_retry():
    calls,closed=[],[]
    async def handle(request):
        calls.append(request)
        try: await asyncio.sleep(30)
        finally: closed.append(True)
    model=build_gateway(handle)
    with pytest.raises(BudgetStopped) as error:
        asyncio.run(model.complete(ModelRequest(({'role':'user','content':'hello'},)),context(task_timeout_seconds=0.25)))
    assert error.value.reason=='EXHAUSTED' and len(calls)<=1
    assert not calls or closed
