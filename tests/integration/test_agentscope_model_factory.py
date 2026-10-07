"""The factory is the one guarded entry point for every logical model call.

These tests exercise the real SDK over a mock HTTP transport: the guard has to run
once per logical call, the provider's own protocol has to survive the SDK conversion,
and every real HTTP attempt has to be bounded and accounted.
"""
import asyncio
import gzip
import json

import httpx
import pytest

pytest.importorskip('agentscope')

from agentscope.message import DataBlock, Msg, TextBlock, ToolCallBlock, ToolResultBlock, URLSource

from reproagent.adapters.agentscope.gateway import AgentScopeModelGateway
from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
from reproagent.core.budget import Budget, BudgetStopped
from reproagent.core.models import BudgetLimits, CallContext, ModelConfig, ModelRequest
from reproagent.core.protocol import ModelOutputError

READ_TOOL = {'type': 'function', 'function': {'name': 'read', 'description': 'read one registered file',
    'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}
READ_CALL = [{'id': 'call-1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{"path":"a.py"}'}}]
REASONING = 'internal reasoning that must not be logged'


def config(**kwargs):
    return ModelConfig(base_url='https://offline.example/v1', model='offline', output_limit_field='max_tokens', **kwargs)


def context(**limits):
    return CallContext(Budget(BudgetLimits(**limits)))


class Store:
    """Only the append path matters: an attempt record is not a written record file."""
    def __init__(self):
        self.events = []

    def append_event(self, kind, refs, payload):
        self.events.append((kind, payload))

    def attempts(self):
        return [payload for kind, payload in self.events if kind == 'model.attempt']


def reply(content='{}', finish='stop', usage=None, tool_calls=None, reasoning=None):
    message = {'role': 'assistant', 'content': content}
    if tool_calls is not None:
        message['tool_calls'] = tool_calls
    if reasoning is not None:
        message['reasoning_content'] = reasoning
    data = {'id': 'offline-id', 'object': 'chat.completion', 'created': 1, 'model': 'offline',
            'choices': [{'index': 0, 'finish_reason': finish, 'message': message}]}
    if usage is not None:
        data['usage'] = usage
    return httpx.Response(200, json=data)


def drive(scenario, handler, store=None, **kwargs):
    """Run one scenario against a fresh factory that is always closed again."""
    async def main():
        factory = AgentScopeModelFactory(config(**kwargs), store, transport=httpx.MockTransport(handler))
        try:
            return await scenario(factory)
        finally:
            await factory.aclose()
    return asyncio.run(main())


async def complete_once(factory):
    return await AgentScopeModelGateway(factory).complete(ModelRequest(({'role': 'user', 'content': 'hello'},)), context())


def test_config_limit_reaches_sdk_wire():
    calls = []

    def handler(http_request):
        calls.append(json.loads(http_request.content))
        return reply()

    async def scenario(factory):
        gateway = AgentScopeModelGateway(factory)
        await gateway.complete(ModelRequest(({'role': 'user', 'content': 'hello'},)), context())
        await gateway.complete(ModelRequest(({'role': 'user', 'content': 'hello'},), max_output_tokens=1024), context())

    drive(scenario, handler, max_output_tokens=8192)
    assert [call['max_tokens'] for call in calls] == [8192, 1024]
    assert 'max_completion_tokens' not in calls[0]

    calls.clear()
    drive(scenario, handler)
    assert [call['max_tokens'] for call in calls] == [4096, 1024]

    calls.clear()
    drive(complete_once, handler, thinking_mode='disabled')
    assert calls[0]['thinking'] == {'type': 'disabled'}

    for rejected in ({'max_output_tokens': 0}, {'max_output_tokens': True}, {'thinking_mode': 'auto'}, {'thinking_mode': ''}):
        with pytest.raises(ValueError):
            config(**rejected)
    with pytest.raises(ValueError):
        ModelRequest((), max_output_tokens=0)


def test_tool_roundtrip_preserves_provider_protocol():
    rounds = []
    answers = [reply(content=None, finish='tool_calls', tool_calls=READ_CALL), reply('{"ok": true}')]

    def handler(http_request):
        rounds.append(json.loads(http_request.content))
        return answers.pop(0)

    async def scenario(factory):
        model = factory.create(context(), 'exploration')
        question = Msg(name='user', role='user', content=[TextBlock(text='find the bug')])
        first = await model([question], tools=[READ_TOOL])
        assert first.is_last and [block.type for block in first.content] == ['tool_call']
        assert (first.content[0].name, first.content[0].input) == ('read', '{"path":"a.py"}')
        asked = Msg(name='assistant', role='assistant', content=list(first.content))
        result_message = Msg(name='assistant', role='assistant',
                             content=[ToolResultBlock(id='call-1', name='read', output='file body')])
        return await model([question, asked, result_message])

    second = drive(scenario, handler)
    assert second.content[0].text == '{"ok": true}'
    assert rounds[0]['tools'] == [READ_TOOL]
    assert rounds[1]['messages'][-2]['tool_calls'] == READ_CALL
    assert rounds[1]['messages'][-1] == {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'file body', 'name': 'read'}


def test_thinking_mode_carries_reasoning_back_in_the_provider_format(caplog):
    rounds = []
    answers = [reply(content=None, finish='tool_calls', tool_calls=READ_CALL, reasoning=REASONING), reply('{"ok": true}')]

    def handler(http_request):
        rounds.append(json.loads(http_request.content))
        return answers.pop(0)

    async def scenario(factory):
        model = factory.create(context(), 'exploration')
        question = Msg(name='user', role='user', content=[TextBlock(text='find the bug')])
        first = await model([question], tools=[READ_TOOL])
        assert [block.type for block in first.content] == ['thinking', 'tool_call']
        asked = Msg(name='assistant', role='assistant', content=list(first.content))
        result_message = Msg(name='assistant', role='assistant',
                             content=[ToolResultBlock(id='call-1', name='read', output='file body')])
        await model([question, asked, result_message])

    with caplog.at_level('WARNING'):
        drive(scenario, handler, thinking_mode='enabled')
    assert rounds[0]['thinking'] == {'type': 'enabled'}
    assert rounds[1]['messages'][-2]['reasoning_content'] == REASONING
    assert REASONING not in caplog.text


def test_unsupported_model_input_is_rejected_before_any_http_attempt():
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return reply()

    async def scenario(factory):
        model = factory.create(context(), 'exploration')
        picture = DataBlock(source=URLSource(url='https://offline.example/scan.png', media_type='image/png'))
        with pytest.raises(ModelOutputError, match='unsupported'):
            await model([Msg(name='user', role='user', content=[TextBlock(text='look'), picture])])

    drive(scenario, handler)
    assert not calls


@pytest.mark.parametrize('kind', ['contract', 'verdict'])
@pytest.mark.parametrize('build,code,retryable', [
    (lambda: reply(content='{}', finish='tool_calls', tool_calls=READ_CALL), 'OUTPUT_FILTERED', False),
    (lambda: reply(content='{"partial": ', finish='length'), 'OUTPUT_TRUNCATED', False),
    (lambda: reply(content='', finish='stop'), 'EMPTY_OUTPUT', True)])
def test_structured_requests_reject_tool_calls_truncation_and_empty_body(kind, build, code, retryable):
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return build()

    async def scenario(factory):
        with pytest.raises(ModelOutputError) as failure:
            await AgentScopeModelGateway(factory).complete(ModelRequest(({'role': 'user', 'content': 'hello'},), kind), context())
        assert (failure.value.code, failure.value.retryable) == (code, retryable)

    drive(scenario, handler)
    assert len(calls) == 1


def test_retry_usage_and_response_limits():
    calls, store = [], Store()

    def handler(http_request):
        calls.append(http_request)
        if len(calls) == 1:
            return httpx.Response(429, json={'error': {'message': 'limited'}})
        return reply(usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})

    async def scenario(factory):
        return await AgentScopeModelGateway(factory).complete(
            ModelRequest(({'role': 'user', 'content': 'hello'},), 'contract'), context())

    result = drive(scenario, handler, store, input_cost_per_million=1, output_cost_per_million=1)
    assert len(calls) == 2 and len(store.attempts()) == 2
    assert [attempt['outcome'] for attempt in store.attempts()] == ['http_error', 'completed']
    # A retried call cannot claim a known cost: the failed attempt's tokens are unknown.
    assert result.cost_kind == 'unknown' and result.cost_value is None and result.usage['total_tokens'] == 15

    failed, retried = [], Store()

    def offline(http_request):
        failed.append(http_request)
        raise httpx.ConnectError('offline')

    async def network(factory):
        with pytest.raises(RuntimeError, match='network'):
            await AgentScopeModelGateway(factory).complete(ModelRequest(({'role': 'user', 'content': 'hello'},)), context())

    drive(network, offline, retried)
    assert len(failed) == 3 and len(retried.attempts()) == 3
    assert {attempt['outcome'] for attempt in retried.attempts()} == {'network_error'}
    assert {attempt['cost_kind'] for attempt in retried.attempts()} == {'unknown'}

    truncated, quiet, events = [], Store(), []

    def short(http_request):
        truncated.append(http_request)
        return reply(content='{"cut": ', finish='length', usage={'prompt_tokens': 3, 'completion_tokens': 1})

    async def once(factory):
        with pytest.raises(ModelOutputError):
            await AgentScopeModelGateway(factory).complete(ModelRequest(({'role': 'user', 'content': 'hello'},)), context())

    drive(once, short, quiet, input_cost_per_million=1, output_cost_per_million=1)
    # Truncation is not a transient failure: the same request must not be repeated.
    assert len(truncated) == 1 and quiet.attempts()[0]['outcome'] == 'OUTPUT_TRUNCATED'


def test_response_limits_reject_oversized_redirected_and_compressed_bodies():
    oversized, consumed = [], []

    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(10):
                consumed.append(index)
                yield b'x' * 600000

        async def aclose(self):
            pass

    def large(http_request):
        oversized.append(http_request)
        return httpx.Response(200, stream=LargeStream(), headers={'content-type': 'application/json'})

    async def too_large(factory):
        with pytest.raises(ModelOutputError) as failure:
            await complete_once(factory)
        assert (failure.value.code, failure.value.retryable) == ('RESPONSE_TOO_LARGE', False)

    drive(too_large, large)
    # A real HTTP response takes the streaming path, so it must stop before the cap.
    assert len(oversized) == 1 and len(consumed) == 2

    redirected = []

    def redirect(http_request):
        redirected.append(http_request)
        return httpx.Response(302, headers={'location': 'https://elsewhere.example/v1/chat/completions'})

    async def follows(factory):
        with pytest.raises(RuntimeError, match='302'):
            await complete_once(factory)

    drive(follows, redirect)
    assert len(redirected) == 1

    consumed, closed, encodings = [], [], []

    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield gzip.compress(b'x' * 4096)

        async def aclose(self):
            closed.append(True)

    def gzip_body(http_request):
        encodings.append(http_request.headers.get('accept-encoding'))
        return httpx.Response(200, stream=CompressedStream(),
                              headers={'content-encoding': 'gzip', 'content-type': 'application/json'})

    async def encoded(factory):
        with pytest.raises(ModelOutputError) as failure:
            await complete_once(factory)
        assert failure.value.code == 'INVALID_PROTOCOL'

    drive(encoded, gzip_body)
    # The compressed body is refused before either SDK parser can decompress it.
    assert encodings == ['identity'] and not consumed and closed


def test_lost_response_still_leaves_an_accounted_attempt():
    store = Store()

    def offline(http_request):
        raise httpx.ConnectError('offline')

    async def scenario(factory):
        with pytest.raises(RuntimeError):
            await complete_once(factory)

    drive(scenario, offline, store)
    # Nothing came back, but each attempt still happened and is recorded as unknown.
    assert len(store.attempts()) == 3
    assert all(attempt['usage'] == {} and attempt['cost_kind'] == 'unknown' for attempt in store.attempts())
    assert {attempt['backend'] for attempt in store.attempts()} == {'agentscope'}
    assert {attempt['effective_output_limit'] for attempt in store.attempts()} == {4096}


def test_guard_runs_once_per_logical_call_and_then_authorizes_retries(monkeypatch):
    calls, guarded = [], []

    def handler(http_request):
        calls.append(http_request)
        if len(calls) < 3:
            return httpx.Response(503, json={'error': {'message': 'busy'}})
        return reply()

    async def scenario(factory):
        model = factory.create(context(), 'exploration', guard=lambda: guarded.append(len(calls)))
        return await model([Msg(name='user', role='user', content=[TextBlock(text='hello')])])

    result = drive(scenario, handler)
    assert result.content[0].text == '{}'
    # The guard runs before the HTTP attempt loop; a network retry never re-runs it.
    assert len(calls) == 3 and guarded == [0]


def test_guard_stops_the_call_before_any_http_attempt():
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return reply()

    def refuse():
        raise BudgetStopped('EXHAUSTED', 'phase already finished')

    async def scenario(factory):
        model = factory.create(context(), 'exploration', guard=refuse)
        with pytest.raises(BudgetStopped):
            await model([Msg(name='user', role='user', content=[TextBlock(text='hello')])])

    drive(scenario, handler)
    assert not calls


def test_one_factory_reuses_one_model_per_purpose_and_closes_it():
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return reply()

    async def scenario(factory):
        context_ = context()
        first = factory.create(context_, 'exploration')
        assert factory.create(context_, 'exploration') is first
        assert factory.create(context_, 'verdict') is not first
        with pytest.raises(ValueError):
            factory.create(context_, 'summary')
        await first([Msg(name='user', role='user', content=[TextBlock(text='hello')])])
        await first([Msg(name='user', role='user', content=[TextBlock(text='hello')])])

    drive(scenario, handler)
    assert len(calls) == 2
