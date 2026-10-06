import asyncio
import os
import time

import httpx

from .options import resolve_model_options
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import ModelResponse
from reproagent.core.protocol import MODEL_OUTPUT_CODES, ModelOutputError

FINISH_REASONS = frozenset({'stop', 'length', 'content_filter', 'tool_calls', 'function_call'})

# Closed set for the outcome an attempt records; anything else is projected to 'unknown'.
ATTEMPT_OUTCOMES = frozenset({'completed', 'unknown', 'network_error', 'http_error'}) | MODEL_OUTPUT_CODES


def classify_finish_reason(value):
    """Reduce a provider's finish_reason to a closed set; raw values never enter records."""
    if not isinstance(value, str) or not value: return 'unknown'
    return value if value in FINISH_REASONS else 'other'


def classify_output(finish_reason, text, tool_calls=False):
    """Map a provider choice onto the fixed failure classes; None means usable text."""
    if finish_reason == 'length':
        return ModelOutputError('provider stopped at the output limit; the response is incomplete',
                                'OUTPUT_TRUNCATED', False)
    if finish_reason != 'stop' or tool_calls:
        return ModelOutputError('provider stopped for a reason other than a complete answer',
                                'OUTPUT_FILTERED', False)
    if not isinstance(text, str) or not text:
        return ModelOutputError('provider returned no text; return exactly one complete JSON object',
                                'EMPTY_OUTPUT')
    return None


def attempt_payload(attempt, usage, cost_kind, cost_value, *, response_kind, effective_output_limit,
                    outcome='unknown', finish_reason='', content_bytes=0, backend=None):
    """Bound what a model attempt records: no prompt, raw response, reasoning text or header."""
    record = {'attempt':attempt, 'usage':usage, 'cost_kind':cost_kind, 'cost_value':cost_value,
        'outcome':outcome if outcome in ATTEMPT_OUTCOMES else 'unknown',
        'response_kind':response_kind, 'effective_output_limit':effective_output_limit,
        'finish_reason':classify_finish_reason(finish_reason), 'content_bytes':content_bytes}
    if backend is not None: record['backend'] = backend
    return record


async def bounded(awaitable, context):
    context.budget.check()
    if context.cancel_event.is_set():
        if hasattr(awaitable, 'close'):
            awaitable.close()
        raise BudgetStopped('CANCELLED')
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            if context.cancel_event.is_set():
                raise BudgetStopped('CANCELLED')
            context.budget.check()
            await asyncio.wait((task,), timeout=0.05)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class ChatCompletionGateway:
    supports_hard_cost_limit = False
    handles_attempt_accounting = True

    def __init__(self, config, transport=None):
        self.config, self.transport = config, transport
        self.attempt_store = None
        if not config.base_url.startswith(('https://', 'http://')) or not config.model:
            raise ValueError('model/base_url must be configured')

    async def complete(self, request, context):
        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped('CANCELLED')
        if context.budget.limits.model_cost_limit is not None:
            raise ValueError('this provider cannot guarantee a hard model cost limit')
        key = os.environ.get(self.config.api_key_env, '')
        if not key and self.transport is None:
            raise ValueError('API key environment variable is missing')
        started = time.monotonic()
        options = resolve_model_options(self.config, request)
        effective_limit = options.output_limit
        payload = {'model': self.config.model, 'messages': list(request.messages),
            self.config.output_limit_field: effective_limit,
            'response_format': {'type': 'json_object'}, **options.extra_body}
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        async with httpx.AsyncClient(transport=self.transport, timeout=min(60, context.budget.deadline - context.budget.clock()), follow_redirects=False, trust_env=False) as client:
            for attempt in range(3):
                reservation = context.budget.reserve_cost(None)
                settled = False
                attempt_usage = {}
                attempt_cost = None
                attempt_outcome, attempt_finish, attempt_bytes = 'unknown', '', 0
                try:
                    response = await bounded(client.post(self.config.base_url.rstrip('/') + '/chat/completions', json=payload, headers=headers), context)
                    if response.status_code in (408, 429, 500, 502, 503, 504):
                        attempt_outcome = 'http_error'
                        if attempt == 2:
                            raise RuntimeError('provider temporarily unavailable after 3 attempts')
                        await bounded(asyncio.sleep(0.1 * (attempt + 1)), context)
                        continue
                    if response.status_code >= 400:
                        attempt_outcome = 'http_error'
                        raise RuntimeError(f'provider HTTP {response.status_code}')
                    if len(response.content) > 1048576:
                        raise ModelOutputError('provider response exceeds protocol limit', 'RESPONSE_TOO_LARGE', False)
                    from reproagent.core.serialization import parse_json
                    try:
                        data = parse_json(response.text)
                    except ValueError:
                        raise ModelOutputError('provider response is not a JSON object') from None
                    usage = data.get('usage') or {}
                    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens') if k in usage):
                        raise ModelOutputError('invalid provider usage')
                    usage = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens') if k in usage and type(usage[k]) is int}
                    attempt_usage = usage
                    cost = None
                    if all(k in usage for k in ('prompt_tokens', 'completion_tokens')) and self.config.input_cost_per_million is not None and self.config.output_cost_per_million is not None:
                        cost = (usage['prompt_tokens'] * self.config.input_cost_per_million + usage['completion_tokens'] * self.config.output_cost_per_million) / 1000000
                    attempt_cost = cost
                    context.budget.settle_cost(reservation, cost)
                    settled = True
                    choice = data['choices'][0]
                    message = choice['message']
                    if not isinstance(message, dict):
                        raise ModelOutputError('invalid provider response structure')
                    text = message.get('content')
                    attempt_finish = choice.get('finish_reason', '')
                    attempt_bytes = len(text.encode('utf-8')) if isinstance(text, str) else 0
                    failure = classify_output(attempt_finish, text, bool(message.get('tool_calls')))
                    if failure is not None:
                        raise failure
                    attempt_outcome = 'completed'
                    total_cost = cost if attempt == 0 else None
                    return ModelResponse(text, str(data.get('id', '')), usage, 'estimated' if total_cost is not None else 'unknown', total_cost, time.monotonic() - started)
                except ModelOutputError as exc:
                    attempt_outcome = exc.code
                    raise
                except (KeyError, IndexError, TypeError) as exc:
                    attempt_outcome = 'INVALID_PROTOCOL'
                    raise ModelOutputError('invalid provider response structure') from exc
                except (httpx.TimeoutException, httpx.NetworkError):
                    attempt_outcome = 'network_error'
                    if attempt == 2:
                        raise RuntimeError('provider network failure after 3 attempts') from None
                    await bounded(asyncio.sleep(0.1 * (attempt + 1)), context)
                finally:
                    if not settled:
                        context.budget.settle_cost(reservation, None)
                    if self.attempt_store:
                        self.attempt_store.append_event('model.attempt', (), attempt_payload(attempt + 1, attempt_usage,
                            'estimated' if attempt_cost is not None else 'unknown', attempt_cost,
                            outcome=attempt_outcome,
                            response_kind=request.response_kind, effective_output_limit=effective_limit,
                            finish_reason=attempt_finish, content_bytes=attempt_bytes))
        raise RuntimeError('provider produced no response')
