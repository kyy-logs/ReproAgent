"""AgentScope model transport under ReproAgent's per-attempt budget."""
import asyncio
import os
import time

import httpx

from .dependency import require_agentscope
from ..models.provider import bounded
from ...core.budget import BudgetStopped
from ...core.models import ModelResponse
from ...core.protocol import ModelOutputError
from ...core.serialization import parse_json


class LimitedResponseStream(httpx.AsyncByteStream):
    """Bound decoded HTTP bytes before either SDK JSON parser can run."""
    def __init__(self, stream, context):
        self.stream, self.context = stream, context

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            self.context.budget.check()
            if self.context.cancel_event.is_set():
                raise BudgetStopped('CANCELLED')
            size += len(chunk)
            if size > 1048576:
                raise ModelOutputError('provider response exceeds protocol limit')
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class AgentScopeModelGateway:
    supports_hard_cost_limit = False
    handles_attempt_accounting = True

    def __init__(self, config, transport=None):
        require_agentscope()
        if not config.base_url.startswith(('https://', 'http://')) or not config.model:
            raise ValueError('model/base_url must be configured')
        self.config, self.transport = config, transport
        self.attempt_store = None

    async def complete(self, request, context):
        from agentscope.credential import OpenAICredential
        from agentscope.message import Msg, TextBlock, ThinkingBlock
        from agentscope.model import OpenAIChatModel
        from agentscope.formatter import OpenAIChatFormatter
        import openai

        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped('CANCELLED')
        if context.budget.limits.model_cost_limit is not None:
            raise ValueError('this provider cannot guarantee a hard model cost limit')
        key = os.environ.get(self.config.api_key_env, '')
        if not key and self.transport is None:
            raise ValueError('API key environment variable is missing')
        started = time.monotonic()
        class TextFormatter(OpenAIChatFormatter):
            async def format(self, messages):
                encoded = await super().format(messages)
                for message in encoded:
                    content = message['content']
                    if not isinstance(content, list) or any(part.get('type') != 'text' for part in content):
                        raise ModelOutputError('only text model inputs are supported')
                    message['content'] = ''.join(part['text'] for part in content)
                    message.pop('name', None)
                return encoded
        messages = [Msg(name=message['role'], role=message['role'], content=[TextBlock(text=message['content'])]) for message in request.messages]
        for attempt in range(3):
            context.budget.check()
            observed = {'usage':{}, 'error':'', 'finish_reason':''}
            reservation = context.budget.reserve_cost(None)
            cost = None
            async def observe(response):
                # The SDK erases the provider's finish_reason during conversion.
                # Inspect it before conversion, without saving response bodies.
                if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
                    observed['error'] = 'unsupported provider content encoding; identity is required'
                    await response.aclose()
                    raise ModelOutputError(observed['error'])
                if not response.is_stream_consumed:
                    response.stream = LimitedResponseStream(response.stream, context)
                try:
                    await response.aread()
                except ModelOutputError:
                    observed['error'] = 'provider response exceeds protocol limit'
                    await response.aclose()
                    raise
                if len(response.content) > 1048576:
                    observed['error'] = 'provider response exceeds protocol limit'
                    raise ModelOutputError(observed['error'])
                if response.status_code >= 400:
                    return
                try:
                    data = parse_json(response.text)
                    usage = data.get('usage') or {}
                    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens') if k in usage):
                        raise ValueError('invalid provider usage')
                    observed['usage'] = {k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens') if k in usage}
                    choice = data['choices'][0]
                    observed['finish_reason'] = choice.get('finish_reason', '')
                    text = choice['message']['content']
                    if choice.get('finish_reason') != 'stop' or not isinstance(text, str) or not text or choice['message'].get('tool_calls'):
                        raise ValueError('truncated, empty or incompatible provider response')
                except (ValueError, KeyError, IndexError, TypeError):
                    observed['error'] = 'invalid, truncated or incompatible provider response'
                    raise ModelOutputError(observed['error']) from None

            try:
                async with httpx.AsyncClient(transport=self.transport,
                        timeout=min(60, context.budget.deadline-context.budget.clock()),
                        headers={'Accept-Encoding':'identity'}, follow_redirects=False,
                        trust_env=False, event_hooks={'response':[observe]}) as client:
                    sdk = OpenAIChatModel(
                        credential=OpenAICredential(api_key=key or 'offline-test', base_url=self.config.base_url),
                        model=self.config.model, stream=False, max_retries=0,
                        formatter=TextFormatter(),
                        client_kwargs={'http_client':client, 'max_retries':0})
                    try:
                        result = await bounded(sdk(messages, response_format={'type':'json_object'},
                            **{self.config.output_limit_field:min(request.max_output_tokens, self.config.max_output_tokens)}), context)
                    except (asyncio.CancelledError, BudgetStopped):
                        raise
                    except Exception as error:
                        if observed['error']:
                            raise ModelOutputError(observed['error']) from None
                        if isinstance(error, openai.APIStatusError):
                            status = error.status_code
                            if status in (408,429,500,502,503,504) and attempt < 2:
                                await bounded(asyncio.sleep(0.1*(attempt+1)), context)
                                continue
                            raise RuntimeError(f'provider HTTP {status}') from None
                        if isinstance(error, openai.APIConnectionError):
                            if attempt < 2:
                                await bounded(asyncio.sleep(0.1*(attempt+1)), context)
                                continue
                            raise RuntimeError('provider network failure after 3 attempts') from None
                        raise ModelOutputError('incompatible AgentScope provider response') from None
                    context.budget.check()
                    if context.cancel_event.is_set() or str(result.finished_reason) == 'interrupted':
                        raise BudgetStopped('CANCELLED')
                    if observed['error']:
                        raise ModelOutputError(observed['error'])
                    if not result.is_last or observed['finish_reason'] != 'stop' or any(not isinstance(block, (TextBlock, ThinkingBlock)) for block in result.content):
                        raise ModelOutputError('incomplete or non-text AgentScope response')
                    text = ''.join(block.text for block in result.content if isinstance(block, TextBlock))
                    if not text:
                        raise ModelOutputError('empty AgentScope response')
                    usage = observed['usage']
                    if all(k in usage for k in ('prompt_tokens','completion_tokens')) and self.config.input_cost_per_million is not None and self.config.output_cost_per_million is not None:
                        cost = (usage['prompt_tokens']*self.config.input_cost_per_million+usage['completion_tokens']*self.config.output_cost_per_million)/1000000
                    return ModelResponse(text, str(result.id), usage, 'estimated' if cost is not None and attempt == 0 else 'unknown',
                        cost if attempt == 0 else None, time.monotonic()-started)
            finally:
                usage = observed['usage']
                if all(k in usage for k in ('prompt_tokens','completion_tokens')) and self.config.input_cost_per_million is not None and self.config.output_cost_per_million is not None:
                    cost = (usage['prompt_tokens']*self.config.input_cost_per_million+usage['completion_tokens']*self.config.output_cost_per_million)/1000000
                context.budget.settle_cost(reservation, cost)
                if self.attempt_store:
                    self.attempt_store.append_event('model.attempt', (), {'attempt':attempt+1, 'backend':'agentscope',
                        'usage':usage, 'cost_kind':'estimated' if cost is not None else 'unknown', 'cost_value':cost})
        raise RuntimeError('provider produced no response')
