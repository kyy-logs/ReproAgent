"""The SDK's own hooks, observed without being changed.

This middleware only reads what the SDK and the phase's own middleware already made:
which replies were rejected and why, which tools really executed, and how each one
ended.  It never calls a permission check of its own (the phase's decision is
projected instead), never inspects tool payloads, and never returns anything other
than what it was handed.

Registration order matters and is the one thing this module insists on.  The phase's
``ExplorationMiddleware`` answers ``on_model_call`` itself rather than delegating to
the next handler -- it has to, because it is the only place the output ceiling is put
on the wire -- so a middleware registered *after* it would never see a model call at
all, including the rejected ones a trace most needs to explain.  The trace therefore
goes in front.
"""
from __future__ import annotations

from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse

from .dependency import require_agentscope
from .middleware import PhaseEnded
from ...observability import span

#: The span a whole SDK reply sits in: one model round, checked as a unit.
MODEL_ROUND_SPAN = "sdk.model_round"

#: What a round that was turned away because the phase already has its result is called.
PHASE_ENDED_CODE = "PHASE_ENDED"

#: Marks the controlled outcome a tool reported, so a refusal and an execution never
#: look alike.  The values are the SDK's own ``ToolResultState`` names.
INCOMPLETE = "INCOMPLETE"


def tool_state(item) -> str | None:
    """What one streamed item says about the tool, or None while it is still running.

    Only the published terminal response carries a state; intermediate chunks do not.
    Content is never read -- the state is the whole fact this hook needs.
    """
    if isinstance(item, ToolResponse):
        return str(item.state).upper()
    return None


class TraceMiddleware(MiddlewareBase):
    """Records the SDK's model rounds and tool executions into the task's trace."""

    def __init__(self) -> None:
        require_agentscope()

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Time one SDK model round and record how the whole reply was judged."""
        with span(MODEL_ROUND_SPAN, attributes={"purpose": "exploration"},
                  expected=(PhaseEnded,)) as handle:
            try:
                response = await next_handler()
            except PhaseEnded:
                # The phase already has its result, so it refuses the next call.  That is
                # how a finished phase ends -- most often right after it published -- and
                # it must not make a clean ending read as a failed round.
                handle.annotate(result_code=PHASE_ENDED_CODE)
                raise
            except BaseException as failure:
                # A rejected reply is the fact worth keeping: the phase's own check
                # ran inside this call and refused the whole response.
                code = getattr(failure, "code", None)
                if isinstance(code, str):
                    handle.annotate(result_code=code)
                raise
            return response

    async def on_acting(self, agent, input_kwargs, next_handler):
        """Forward the tool's stream unchanged, timing it and recording its outcome."""
        tool_call = input_kwargs.get("tool_call")
        name = getattr(tool_call, "name", "")
        with span(f"tool.{name}", attributes={"tool": name}) as handle:
            terminal = None
            async for item in next_handler():
                state = tool_state(item)
                if state is not None:
                    # Recorded before the final item is handed on: a consumer that stops
                    # the moment a candidate is published must not turn an execution that
                    # already finished into a failure.
                    terminal = state
                    handle.annotate(result_code=state)
                yield item
            if terminal is None:
                handle.annotate(result_code=INCOMPLETE)


__all__ = ["INCOMPLETE", "MODEL_ROUND_SPAN", "TraceMiddleware", "tool_state"]
