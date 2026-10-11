"""Structured domain requests over the shared guarded SDK model factory.

The gateway only shapes a contract/verdict request and reads the reply: cancellation,
the 1MiB response limit, the retry layer and per-attempt accounting all belong to
``AgentScopeModelFactory``, which every SDK model in the product is built from.
"""
import time

from .dependency import require_agentscope
from .model_factory import purpose_for
from ..models.options import resolve_model_options
from ...core.budget import BudgetStopped
from ...core.models import ModelResponse


class AgentScopeModelGateway:
    supports_hard_cost_limit = False
    handles_attempt_accounting = True

    def __init__(self, factory):
        require_agentscope()
        self.factory = factory

    @property
    def transport(self):
        """The mock transport a test installs before the first request."""
        return self.factory.transport

    @transport.setter
    def transport(self, transport):
        self.factory.transport = transport

    @property
    def attempt_store(self):
        """Attempt records go to the factory's store: one accounting owner."""
        return self.factory.store

    @attempt_store.setter
    def attempt_store(self, store):
        self.factory.store = store

    async def aclose(self):
        """Release the factory's clients; the factory owns every client this gateway used."""
        await self.factory.aclose()

    async def complete(self, request, context):
        from agentscope.message import Msg, TextBlock

        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped('CANCELLED', dimension='cancelled')
        if context.budget.limits.model_cost_limit is not None:
            raise ValueError('this provider cannot guarantee a hard model cost limit')
        started = time.monotonic()
        options = resolve_model_options(self.factory.config, request)
        messages = [Msg(name=message['role'], role=message['role'], content=[TextBlock(text=message['content'])])
                    for message in request.messages]
        model = self.factory.create(context, purpose_for(request))
        reply = await model(messages, response_format={'type': 'json_object'},
                            **{self.factory.config.output_limit_field: options.output_limit})
        text = ''.join(block.text for block in reply.content if isinstance(block, TextBlock))
        return ModelResponse(text, str(reply.id), model.observation.usage, model.cost_kind, model.cost_value,
                             time.monotonic() - started)
