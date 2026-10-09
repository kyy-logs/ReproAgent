"""The one guarded entry point for AgentScope model traffic.

Every logical model call goes through a model this factory created: the guard runs
once, before the bounded HTTP attempt loop, so a network retry never spends a second
exploration step, and every real HTTP attempt is bounded and accounted. The provider's
own ``finish_reason``, usage and body size are read before the SDK converts the reply,
and the purpose decides what a usable reply is.
"""
import asyncio
import os

import httpx

from .dependency import require_agentscope
from ..models.options import resolve_model_options
from ...core.async_ops import bounded
from ...core.budget import BudgetStopped
from ...core.models import ModelRequest
from ...core.protocol import ModelOutputError, attempt_payload, classify_output
from ...core.serialization import parse_json
from ...observability import HTTP_ATTEMPT_SPAN, LOGICAL_SPAN, capture, span

#: The most of a rejected response body the trace will keep, so a preview cannot itself
#: become the reason a run is unreadable.
INVALID_PREVIEW_CHARACTERS = 512

PURPOSES = frozenset({'exploration', 'contract', 'verdict', 'learning'})
# A logical request may cost at most this many real HTTP attempts; transient failures
# inside the loop are retried, everything else is decided by the caller.
HTTP_ATTEMPTS = 3
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
RETRY_DELAY_SECONDS = 0.1
REQUEST_TIMEOUT_SECONDS = 60
RESPONSE_LIMIT_BYTES = 1048576
# Block types the provider protocol carries. Anything else would be dropped by the
# SDK formatter, so it has to fail before a request is sent.
SUPPORTED_BLOCK_TYPES = frozenset({'text', 'thinking', 'hint', 'tool_call', 'tool_result'})

# Returned by one attempt to say "this failure may be repeated".
_RETRY = object()
_SDK_CLASSES = None


def _reject_unsupported_blocks(messages):
    for message in messages:
        for block in message.get_content_blocks():
            if block.type not in SUPPORTED_BLOCK_TYPES:
                raise ModelOutputError(f'unsupported model input block: {block.type}', 'INVALID_PROTOCOL', False)


def classify_provider_output(purpose, finish_reason, text, tool_calls):
    """Map one provider choice onto the fixed failure classes; None means usable output.

    Exploration may answer with a tool call, with text, or with reasoning only, so the
    protocol level only refuses a reply the provider itself said was cut short or
    filtered. Contract and verdict requests have to be a complete structured answer.
    """
    if finish_reason == 'length':
        return ModelOutputError('provider stopped at the output limit; the response is incomplete',
                                'OUTPUT_TRUNCATED', False)
    if purpose == 'exploration':
        if finish_reason in ('stop', 'tool_calls'):
            return None
        return ModelOutputError('provider stopped for a reason other than a complete answer',
                                'OUTPUT_FILTERED', False)
    return classify_output(finish_reason, text, tool_calls)


class ObservedAttempt:
    """What one provider reply left behind, read before the SDK erases it."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.usage = {}
        self.error = None
        self.outcome = 'unknown'
        self.finish_reason = ''
        self.content_bytes = 0


def _failed(observation, error):
    observation.error, observation.outcome = error, error.code
    return error


def effective_output_limit(config, kwargs) -> int:
    """The ceiling this request really carried, resolved the same way the wire did."""
    limit = kwargs.get(config.output_limit_field)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        limit = config.max_output_tokens
    return limit


def usage_totals(usage: dict) -> dict:
    """The provider's usage, in the trace's own two-token vocabulary.

    A figure the provider did not report stays absent, so the summary can call it
    unknown instead of counting it as a confident zero.
    """
    return {'input_tokens': usage.get('prompt_tokens'), 'output_tokens': usage.get('completion_tokens')}


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
            if size > RESPONSE_LIMIT_BYTES:
                # This is the path a real HTTP response takes, so it has to classify the
                # size failure identically to the post-read check below.
                raise ModelOutputError('provider response exceeds protocol limit', 'RESPONSE_TOO_LARGE', False)
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


def _response_hook(observation, context, purpose):
    """Read the raw reply without keeping or logging any of it."""
    async def observe(response):
        if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
            # This client always asks for identity, so asking again changes nothing.
            raise _failed(observation, ModelOutputError(
                'unsupported provider content encoding; identity is required', 'INVALID_PROTOCOL', False))
        if not response.is_stream_consumed:
            response.stream = LimitedResponseStream(response.stream, context)
        try:
            await response.aread()
        except ModelOutputError as error:
            observation.error, observation.outcome = error, error.code
            await response.aclose()
            raise
        if len(response.content) > RESPONSE_LIMIT_BYTES:
            raise _failed(observation, ModelOutputError('provider response exceeds protocol limit',
                                                        'RESPONSE_TOO_LARGE', False))
        if response.status_code >= 300:
            # The transport reports any non-success status; a redirect is never
            # followed and its body is not a provider reply.
            return
        try:
            data = parse_json(response.text)
            usage = data.get('usage') or {}
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                    for k in ('prompt_tokens', 'completion_tokens', 'total_tokens') if k in usage):
                raise ValueError('invalid provider usage')
            observation.usage = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                 if k in usage}
            choice = data['choices'][0]
            message = choice['message']
            if not isinstance(message, dict):
                raise ValueError('invalid provider response structure')
            observation.finish_reason = choice.get('finish_reason', '')
            # Captured before the phase judges any of it, so a response that is refused
            # whole is still reviewable rather than merely rejected.
            _capture_response(message)
            text = message.get('content')
            observation.content_bytes = len(text.encode('utf-8')) if isinstance(text, str) else 0
            failure = classify_provider_output(purpose, observation.finish_reason, text,
                                               bool(message.get('tool_calls')))
            if failure is not None:
                raise _failed(observation, failure)
            observation.outcome = 'completed'
        except ModelOutputError:
            raise
        except (ValueError, KeyError, IndexError, TypeError):
            # A body that is not the documented shape is worth keeping a bounded look at,
            # because "the provider answered something else" is a fact about the provider.
            try:
                capture('invalid_response_preview', response.text[:INVALID_PREVIEW_CHARACTERS],
                        source='invalid_response_preview')
            except Exception:  # noqa: BLE001 - observation never escapes
                pass
            raise _failed(observation, ModelOutputError(
                'invalid, truncated or incompatible provider response')) from None
    return observe


def _capture_response(message: dict) -> None:
    """Keep the provider's own content, tool calls and reasoning, as it returned them."""
    capture('provider_response',
            {'content': message.get('content'), 'tool_calls': message.get('tool_calls')},
            source='provider_response')
    reasoning = message.get('reasoning_content')
    # An absent reasoning field is the provider's normal behaviour, not a recorder fault.
    capture('provider_reasoning', reasoning, source='provider_reasoning',
            availability='captured' if isinstance(reasoning, str) and reasoning else 'not_returned')


def _request_hook(purpose: str):
    """Keep the request the provider would actually receive, once per HTTP attempt.

    Only the body httpx has already buffered is read: consuming a stream here would
    change what is sent, and re-issuing the request would change what is paid for.
    """
    async def observe(request):
        try:
            body = request.content
            if len(body) > RESPONSE_LIMIT_BYTES:
                capture('wire_request', None, source='wire_request', availability='unsupported')
                return
            capture('wire_request', parse_json(body.decode('utf-8')), source='wire_request')
        except Exception:  # noqa: BLE001 - observation never escapes
            capture('wire_request', None, source='wire_request', availability='unsupported')
    return observe


class _StrictInput:
    """Refuse model input the provider protocol cannot carry.

    ``DeepSeekChatFormatter`` keeps ``reasoning_content`` and tool calls, but it skips
    blocks it does not know. Dropping part of a request would change what the model
    saw, so an unsupported block has to fail instead of passing silently.
    """
    async def format(self, msgs):
        _reject_unsupported_blocks(msgs)
        return await super().format(msgs)


class _GuardedModel:
    """One logical model call: one guard, bounded HTTP attempts, full accounting.

    The guard runs once, before the attempt loop, so a network retry never re-runs the
    caller's phase check. Accounting counts real HTTP attempts, not logical requests.
    """
    def __init__(self, *, purpose, config, store, context, observation, guard=None, **kwargs):
        self.purpose, self.config, self.store = purpose, config, store
        self.context, self.observation, self.guard = context, observation, guard
        self.attempts = 0
        self.cost_kind, self.cost_value = 'unknown', None
        super().__init__(**kwargs)

    def bind(self, guard):
        """Install the caller's guard; each ``create`` states the current one."""
        self.guard = guard

    async def __call__(self, messages, tools=None, tool_choice=None, **kwargs):
        if tools and self.purpose != 'exploration':
            raise ValueError(f'{self.purpose} requests cannot use tools')
        _reject_unsupported_blocks(messages)
        if self.guard is not None:
            self.guard()
        self.attempts, self.cost_kind, self.cost_value = 0, 'unknown', None
        # One logical call, however many attempts it takes: the retries below belong to
        # this span, so a trace shows a retried request as one request that struggled.
        with span(LOGICAL_SPAN, attributes={'purpose': self.purpose}):
            for attempt in range(HTTP_ATTEMPTS):
                self.attempts = attempt + 1
                reply = await self._attempt(messages, tools, tool_choice, kwargs, attempt)
                if reply is not _RETRY:
                    return reply
                await bounded(asyncio.sleep(RETRY_DELAY_SECONDS * self.attempts), self.context)
            raise RuntimeError('provider produced no response')

    async def _attempt(self, messages, tools, tool_choice, kwargs, attempt):
        # The only place HTTP, token and cost are attributed: the leaves of the trace.
        with span(HTTP_ATTEMPT_SPAN, attributes={'attempt': attempt + 1}) as http:
            self.observation.reset()
            reservation = self.context.budget.reserve_cost(None)
            try:
                reply = await bounded(super().__call__(messages, tools=tools, tool_choice=tool_choice, **kwargs),
                                      self.context)
                self.context.budget.check()
                if self.context.cancel_event.is_set() or str(reply.finished_reason) == 'interrupted':
                    raise BudgetStopped('CANCELLED')
                if self.observation.error is not None:
                    raise self.observation.error
                failure = self._validate_reply(reply)
                if failure is not None:
                    self.observation.outcome = failure.code
                    raise failure
                if attempt == 0:
                    # A retried call cannot claim a known cost: the failed attempt's
                    # tokens are unknown, so the record stays honest.
                    self.cost_value = self._cost()
                    self.cost_kind = 'estimated' if self.cost_value is not None else 'unknown'
                return reply
            except (asyncio.CancelledError, BudgetStopped):
                raise
            except ModelOutputError as failure:
                if self.observation.error is None:
                    self.observation.outcome = failure.code
                raise
            except Exception as failure:
                return self._retry_or_raise(failure, attempt)
            finally:
                # The cost is computed once and shared with the attempt record: tracing
                # must not make the untraced path do the work twice.
                cost = self._account(reservation, kwargs)
                http.annotate(result_code=self.observation.outcome,
                              output_limit=effective_output_limit(self.config, kwargs),
                              usage=usage_totals(self.observation.usage),
                              cost=cost)

    def _validate_reply(self, reply):
        """A structured request is only usable as complete text; other purposes decide.

        The SDK maps every finished reply onto its own two-value enum, so the provider's
        original ``stop`` is read from the observation instead.
        """
        if self.purpose == 'exploration':
            return None
        from agentscope.message import TextBlock, ThinkingBlock
        if not reply.is_last or self.observation.finish_reason != 'stop' or any(
                not isinstance(block, (TextBlock, ThinkingBlock)) for block in reply.content):
            return ModelOutputError('incomplete or non-text AgentScope response', 'OUTPUT_FILTERED', False)
        if not ''.join(block.text for block in reply.content if isinstance(block, TextBlock)):
            return ModelOutputError('empty AgentScope response', 'EMPTY_OUTPUT')
        return None

    def _retry_or_raise(self, failure, attempt):
        """Whether one failed HTTP attempt may be repeated; otherwise raise its class."""
        import openai
        if isinstance(failure, openai.APIStatusError):
            self.observation.outcome = 'http_error'
            if failure.status_code in RETRYABLE_STATUS and attempt + 1 < HTTP_ATTEMPTS:
                return _RETRY
            raise RuntimeError(f'provider HTTP {failure.status_code}') from None
        if isinstance(failure, openai.APIConnectionError):
            self.observation.outcome = 'network_error'
            if attempt + 1 < HTTP_ATTEMPTS:
                return _RETRY
            raise RuntimeError('provider network failure after 3 attempts') from None
        self.observation.outcome = 'INVALID_PROTOCOL'
        raise ModelOutputError('incompatible AgentScope provider response') from None

    def _cost(self):
        usage = self.observation.usage
        if all(k in usage for k in ('prompt_tokens', 'completion_tokens')) \
                and self.config.input_cost_per_million is not None \
                and self.config.output_cost_per_million is not None:
            return (usage['prompt_tokens'] * self.config.input_cost_per_million
                    + usage['completion_tokens'] * self.config.output_cost_per_million) / 1000000
        return None

    def _account(self, reservation, kwargs):
        cost = self._cost()
        self.context.budget.settle_cost(reservation, cost)
        if self.store is None:
            return cost
        limit = effective_output_limit(self.config, kwargs)
        self.store.append_event('model.attempt', (), attempt_payload(
            self.attempts, self.observation.usage, 'estimated' if cost is not None else 'unknown', cost,
            response_kind=self.purpose, effective_output_limit=limit, outcome=self.observation.outcome,
            finish_reason=self.observation.finish_reason, content_bytes=self.observation.content_bytes,
            backend='agentscope'))
        return cost


def _sdk_classes():
    """The SDK-bound classes, built on first use so AgentScope stays optional."""
    global _SDK_CLASSES
    if _SDK_CLASSES is None:
        from agentscope.formatter import DeepSeekChatFormatter
        from agentscope.model import OpenAIChatModel

        class ProtocolFormatter(_StrictInput, DeepSeekChatFormatter):
            """The provider's own format, refusing any block it would silently drop."""

        class GuardedChatModel(_GuardedModel, OpenAIChatModel):
            """One guarded, bounded and accounted logical model call."""

        _SDK_CLASSES = (GuardedChatModel, ProtocolFormatter)
    return _SDK_CLASSES


class AgentScopeModelFactory:
    """Creates every SDK model from one configuration, guard and accounting point.

    One factory belongs to one task: ``create`` returns the same model per purpose and
    context, so a phase keeps one client and one message history, while every logical
    call still runs the current guard and its own bounded HTTP attempt loop.
    """
    def __init__(self, config, store, *, transport=None):
        require_agentscope()
        if not config.base_url.startswith(('https://', 'http://')) or not config.model:
            raise ValueError('model/base_url must be configured')
        self.config, self.store, self.transport = config, store, transport
        # The thinking switch is a configuration, not a request option, so it resolves
        # the same way for every request; only the output ceiling varies per request.
        self.extra_body = resolve_model_options(config, ModelRequest(())).extra_body
        self._models, self._clients = {}, []

    def create(self, context, purpose, *, guard=None):
        """The model for one purpose; the context is part of its identity."""
        if purpose not in PURPOSES:
            raise ValueError(f'unsupported model purpose: {purpose}')
        key = (purpose, id(context))
        model = self._models.get(key)
        if model is None:
            model = self._build(context, purpose)
            self._models[key] = model
        model.bind(guard)
        return model

    async def aclose(self):
        """Release every client this factory opened."""
        clients, self._clients = self._clients, []
        self._models.clear()
        for client in clients:
            await client.aclose()

    def _build(self, context, purpose):
        from agentscope.credential import OpenAICredential
        model_class, formatter_class = _sdk_classes()
        context.budget.check()
        key = os.environ.get(self.config.api_key_env, '')
        if not key and self.transport is None:
            raise ValueError('API key environment variable is missing')
        observation = ObservedAttempt()
        client = httpx.AsyncClient(transport=self.transport,
            timeout=min(REQUEST_TIMEOUT_SECONDS, context.budget.deadline - context.budget.clock()),
            headers={'Accept-Encoding': 'identity'}, follow_redirects=False, trust_env=False,
            event_hooks={'request': [_request_hook(purpose)],
                         'response': [_response_hook(observation, context, purpose)]})
        self._clients.append(client)
        return model_class(purpose=purpose, config=self.config, store=self.store, context=context,
            observation=observation, formatter=formatter_class(), stream=False, max_retries=0,
            extra_body=self.extra_body or None, client_kwargs={'http_client': client, 'max_retries': 0},
            credential=OpenAICredential(api_key=key or 'offline-test', base_url=self.config.base_url),
            model=self.config.model)


def purpose_for(request):
    """Domain requests are structured: contract and verdict share one reply contract.

    A legacy ``action`` request is a single structured answer too, so it runs and is
    recorded as a contract request.
    """
    if request.response_kind in ('verdict', 'learning'):
        return request.response_kind
    if request.response_kind in ('contract', 'action'):
        return 'contract'
    raise ValueError('unsupported structured request purpose')
