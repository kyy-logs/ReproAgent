from __future__ import annotations

import time
import uuid
from decimal import Decimal

from .models import BudgetLimits


class BudgetStopped(Exception):
    def __init__(self, reason: str, message: str = "", *, dimension: str | None = None):
        self.reason = reason
        self.dimension = dimension
        super().__init__(message or reason)


class Budget:
    def __init__(self, limits: BudgetLimits, clock=time.monotonic):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + limits.task_timeout_seconds
        self._steps = 0
        self._cancelled = False
        self._reservations = {}
        self.cost_spent = Decimal("0")
        self.unknown_cost_calls = 0

    @property
    def steps_used(self):
        return self._steps

    def cancel(self):
        self._cancelled = True

    def check(self):
        if self._cancelled:
            raise BudgetStopped("CANCELLED", dimension="cancelled")
        if self.clock() >= self.deadline:
            raise BudgetStopped("EXHAUSTED", "task time limit reached", dimension="time")
        if self.limits.model_cost_limit is not None and self.cost_spent >= Decimal(str(self.limits.model_cost_limit)):
            raise BudgetStopped("EXHAUSTED", "model cost limit reached", dimension="cost")

    def take_step(self):
        self.check()
        if self._steps >= self.limits.agent_steps:
            raise BudgetStopped("EXHAUSTED", "agent step limit reached", dimension="steps")
        self._steps += 1

    def command_timeout(self):
        self.check()
        return min(self.limits.command_timeout_seconds, self.deadline - self.clock())

    def reserve_cost(self, upper_bound):
        self.check()
        limit = self.limits.model_cost_limit
        if limit is not None and upper_bound is None:
            raise ValueError("provider cannot guarantee configured cost limit")
        value = Decimal(str(upper_bound)) if upper_bound is not None else None
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("invalid cost upper bound")
        held = sum((v for v in self._reservations.values() if v is not None), Decimal("0"))
        if limit is not None and self.cost_spent + held + value > Decimal(str(limit)):
            raise BudgetStopped("EXHAUSTED", "insufficient model cost budget", dimension="cost")
        identifier = uuid.uuid4().hex
        self._reservations[identifier] = value
        return identifier

    def settle_cost(self, reservation_id, actual):
        reserved = self._reservations[reservation_id]
        if actual is None:
            self.unknown_cost_calls += 1
            # An unresolved reservation cannot be reused for another request.
            return
        value = Decimal(str(actual))
        if not value.is_finite() or value < 0:
            raise ValueError("invalid actual cost")
        self._reservations.pop(reservation_id)
        self.cost_spent += value
        if reserved is not None and value > reserved:
            raise BudgetStopped("EXHAUSTED", "provider exceeded reserved cost", dimension="cost")


class BudgetedGateway:
    def __init__(self, gateway, store=None, secrets=()):
        self.gateway, self.store = gateway, store
        self.secrets = tuple(secret for secret in secrets if secret)
        if getattr(gateway, 'handles_attempt_accounting', False):
            gateway.attempt_store = store

    async def aclose(self):
        """Close the wrapped provider when it owns clients; one that owns none is left alone.

        The wrapper exists to account for and redact calls, not to own the provider's
        resources, so an injected test model without a close path stays as it was.
        """
        close = getattr(self.gateway, 'aclose', None)
        if close is not None:
            await close()

    async def complete(self, request, context):
        from dataclasses import replace
        messages = []
        for message in request.messages:
            message = dict(message)
            for secret in self.secrets:
                message['content'] = message['content'].replace(secret, '[REDACTED]')
            messages.append(message)
        request = replace(request, messages=tuple(messages))
        reservation = None if getattr(self.gateway, 'handles_attempt_accounting', False) else context.budget.reserve_cost(None)
        try:
            response = await self.gateway.complete(request, context)
        except BaseException:
            if reservation is not None:
                context.budget.settle_cost(reservation, None)
            raise
        if reservation is not None:
            context.budget.settle_cost(reservation, response.cost_value)
        if self.store:
            # Record usage/latency only; prompt, authorization and raw HTTP response stay out.
            self.store.append_event('model.completed', (), {'response_kind': request.response_kind, 'usage': response.usage,
                'cost_kind': response.cost_kind, 'cost_value': response.cost_value, 'duration': response.duration})
        return response
