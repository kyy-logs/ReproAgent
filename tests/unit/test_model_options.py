"""The configured output ceiling and thinking switch have to reach the wire."""
import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest

from reproagent.adapters.agentscope.gateway import AgentScopeModelGateway
from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
from reproagent.adapters.models.options import ProviderRequestOptions, resolve_model_options
from reproagent.core.budget import Budget
from reproagent.core.models import BudgetLimits, CallContext, ModelConfig, ModelRequest


def config(**kwargs):
    return ModelConfig(base_url='https://offline.example/v1', model='offline', **kwargs)


def request(**kwargs):
    return ModelRequest(({'role':'user','content':'hello'},), **kwargs)


def context(): return CallContext(Budget(BudgetLimits()))


def gateway(handler, **kwargs):
    """The product's model boundary over a mock transport."""
    return AgentScopeModelGateway(AgentScopeModelFactory(config(**kwargs), None, transport=httpx.MockTransport(handler)))


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


def test_shipped_non_thinking_example_round_trips_into_model_config():
    examples = Path(__file__).resolve().parents[2] / 'examples'
    shipped = ModelConfig(**json.loads((examples / 'model.deepseek.json').read_text(encoding='utf-8')))
    non_thinking = ModelConfig(**json.loads((examples / 'model.deepseek.non-thinking.json').read_text(encoding='utf-8')))
    assert non_thinking.thinking_mode == 'disabled'
    assert (non_thinking.base_url, non_thinking.model, non_thinking.api_key_env, non_thinking.max_output_tokens,
            non_thinking.output_limit_field) == (shipped.base_url, shipped.model, shipped.api_key_env,
            shipped.max_output_tokens, shipped.output_limit_field)


def test_thinking_switch_is_resolved_only_when_configured():
    assert resolve_model_options(config(), request()).extra_body == {}
    assert resolve_model_options(config(thinking_mode='disabled'), request()).extra_body == {'thinking': {'type': 'disabled'}}
    assert resolve_model_options(config(thinking_mode='enabled'), request()).extra_body == {'thinking': {'type': 'enabled'}}


def test_request_body_carries_the_thinking_switch_only_when_configured():
    calls, handler = recorder()
    asyncio.run(gateway(handler).complete(request(), context()))
    assert 'thinking' not in calls[0]
    calls, handler = recorder()
    asyncio.run(gateway(handler, thinking_mode='disabled').complete(request(), context()))
    assert calls[0]['thinking'] == {'type': 'disabled'}


def test_sampling_temperature_reaches_the_wire_only_when_configured():
    """A run that does not fix the temperature is not a measurement to compare.

    Three rounds of the same configuration over the same cases produced three different
    outcome sets because the provider's own default applied, so a comparison of two
    architectures could not be read at all.
    """
    calls, handler = recorder()
    asyncio.run(gateway(handler).complete(request(), context()))
    assert 'temperature' not in calls[0]
    calls, handler = recorder()
    asyncio.run(gateway(handler, temperature=0).complete(request(), context()))
    assert calls[0]['temperature'] == 0


def test_temperature_is_resolved_next_to_the_thinking_switch():
    assert resolve_model_options(config(temperature=0.0), request()).extra_body == {'temperature': 0.0}
    assert resolve_model_options(config(temperature=0.0, thinking_mode='disabled'), request()).extra_body == {
        'temperature': 0.0, 'thinking': {'type': 'disabled'}}


def test_shipped_deterministic_example_round_trips_into_model_config():
    examples = Path(__file__).resolve().parents[2] / 'examples'
    non_thinking = ModelConfig(**json.loads((examples / 'model.deepseek.non-thinking.json').read_text(encoding='utf-8')))
    deterministic = ModelConfig(**json.loads((examples / 'model.deepseek.deterministic.json').read_text(encoding='utf-8')))
    assert deterministic.temperature == 0
    assert (deterministic.base_url, deterministic.model, deterministic.api_key_env, deterministic.max_output_tokens,
            deterministic.output_limit_field, deterministic.thinking_mode) == (
            non_thinking.base_url, non_thinking.model, non_thinking.api_key_env, non_thinking.max_output_tokens,
            non_thinking.output_limit_field, non_thinking.thinking_mode)


@pytest.mark.parametrize('kwargs', [{'temperature':-0.1}, {'temperature':2.1}, {'temperature':True}, {'temperature':'0'}])
def test_invalid_temperature_rejected(kwargs):
    with pytest.raises(ValueError):
        config(**kwargs)


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
