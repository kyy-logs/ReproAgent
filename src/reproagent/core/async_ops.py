"""Bounded awaiting: one coroutine under the task's cancel event and deadline."""
import asyncio

from .budget import BudgetStopped


async def bounded(awaitable, context):
    """Await one coroutine, polling the budget, and never leave it running behind.

    The budget and the cancel event are checked between polls so a pending HTTP call
    or process is cancelled promptly instead of being left to finish on its own.
    """
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
