"""The configured output ceiling and thinking switch have to reach the wire."""
import asyncio
import json
from dataclasses import FrozenInstanceError

import httpx
import pytest

from reproagent.adapters.models.options import ProviderRequestOptions, resolve_model_options
from reproagent.core.budget import Budget
from reproagent.core.models import BudgetLimits, CallContext, ModelConfig, ModelRequest


def config(**kwargs):
    return ModelConfig(base_url='https://offline.example/v1', model='offline', **kwargs)


def request(**kwargs):
    return ModelRequest(({'role':'user','content':'hello'},), **kwargs)


def context(): return CallContext(Budget(BudgetLimits()))


def gateway(handler, **kwargs):
    import importlib
    module = importlib.import_module('reproagent.adapters.models.provider')
    return module.ChatCompletionGateway(config(**kwargs), transport=httpx.MockTransport(handler))


def recorder():
    calls = []
    def handler(http_request):
        calls.append(json.loads(http_request.content))
        return httpx.Response(200, json={'id':'offline','choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})
    return calls, handler


def test_config_8192_reaches_wire_when_request_limit_unspecified():
    calls, handler = recorder()
    asyncio.run(gateway(handler, max_output_tokens=8192).complete(request(), context()))
    assert calls[0]['max_completion_tokens'] == 8192


def test_explicit_request_1024_remains_capped():
    calls, handler = recorder()
    asyncio.run(gateway(handler, max_output_tokens=8192).complete(request(max_output_tokens=1024), context()))
    assert calls[0]['max_completion_tokens'] == 1024


def test_explicit_request_above_the_configured_ceiling_is_still_capped():
    calls, handler = recorder()
    asyncio.run(gateway(handler, max_output_tokens=8192).complete(request(max_output_tokens=16384), context()))
    assert calls[0]['max_completion_tokens'] == 8192


def test_default_configuration_still_sends_4096():
    calls, handler = recorder()
    asyncio.run(gateway(handler).complete(request(), context()))
    assert calls[0]['max_completion_tokens'] == 4096
    assert resolve_model_options(ModelConfig(), ModelRequest(())).output_limit == 4096


def test_resolved_options_are_frozen():
    options = resolve_model_options(config(max_output_tokens=8192), request(max_output_tokens=512))
    assert options == ProviderRequestOptions(512, {})
    with pytest.raises(FrozenInstanceError):
        options.output_limit = 1


def test_thinking_switch_is_resolved_only_when_configured():
    assert resolve_model_options(config(), request()).extra_body == {}
    assert resolve_model_options(config(thinking_mode='disabled'), request()).extra_body == {'thinking': {'type': 'disabled'}}
    assert resolve_model_options(config(thinking_mode='enabled'), request()).extra_body == {'thinking': {'type': 'enabled'}}


def test_native_request_body_carries_the_thinking_switch_only_when_configured():
    calls, handler = recorder()
    asyncio.run(gateway(handler).complete(request(), context()))
    assert 'thinking' not in calls[0]
    calls, handler = recorder()
    asyncio.run(gateway(handler, thinking_mode='disabled').complete(request(), context()))
    assert calls[0]['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '4096'])
def test_invalid_request_limit_rejected(value):
    with pytest.raises(ValueError):
        request(max_output_tokens=value)


@pytest.mark.parametrize('kwargs', [{'max_output_tokens':0}, {'max_output_tokens':-1}, {'max_output_tokens':True},
                                    {'thinking_mode':'auto'}, {'thinking_mode':''}, {'thinking_mode':'DISABLED'},
                                    {'thinking_mode':True}])
def test_invalid_limits_and_thinking_mode_rejected(kwargs):
    with pytest.raises(ValueError):
        config(**kwargs)
