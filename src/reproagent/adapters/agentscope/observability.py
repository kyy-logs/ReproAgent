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
from .tools import TOOL_NAMES
from ...core.budget import BudgetStopped
from ...observability import span, tool_call_key

#: The span a whole SDK reply sits in: one model round, checked as a unit.
MODEL_ROUND_SPAN = "sdk.model_round"

#: What a round that was turned away because the phase already has its result is called.
PHASE_ENDED_CODE = "PHASE_ENDED"
#: What a round cut short by the budget, with no reason of its own, is called.
BUDGET_STOPPED_CODE = "BUDGET_STOPPED"

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


def tool_result(response) -> dict:
    """The text a finished tool returned, in the shape the model received it.

    Binary blocks are named, never decoded: the trace says an image came back, it does
    not carry the image.
    """
    blocks = []
    for block in getattr(response, "content", ()) or ():
        text = getattr(block, "text", None)
        blocks.append(text if isinstance(text, str) else {"type": getattr(block, "type", "unknown")})
    return {"content": blocks, "state": str(getattr(response, "state", ""))}


class TraceMiddleware(MiddlewareBase):
    """Records the SDK's model rounds and tool executions into the task's trace."""

    def __init__(self, context, *, allowed_tools=TOOL_NAMES) -> None:
        require_agentscope()
        self.context = context
        self.allowed_tools = tuple(allowed_tools)

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Time one SDK model round and record how the whole reply was judged."""
        with span(MODEL_ROUND_SPAN, attributes={"purpose": "exploration"},
                  expected=(PhaseEnded, BudgetStopped)) as handle:
            try:
                response = await next_handler()
            except (PhaseEnded, BudgetStopped) as ended:
                # Both are how a phase *ends* rather than how it fails: the phase either
                # already has its result, or the task ran out of budget.  An EXHAUSTED
                # task whose last round reads as a failure is exactly the mislabelling
                # this handles.
                handle.annotate(result_code=PHASE_ENDED_CODE if isinstance(ended, PhaseEnded)
                                else str(getattr(ended, "reason", "") or BUDGET_STOPPED_CODE))
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
        raw_name = getattr(tool_call, "name", "")
        # A name the phase never registered is model-controlled text, so only a
        # registered one is written down -- as the domain event already does.
        name = raw_name if raw_name in self.allowed_tools else ""
        # The SDK's own call id, turned into a key: the only thing that pairs this
        # execution with the permission decision that admitted it.  A tool name cannot:
        # the same tool is called many times in one phase.
        key = tool_call_key(sdk_call_id=getattr(tool_call, "id", ""),
                            step_index=self.context.budget.steps_used)
        attributes = {"tool": name}
        if key is not None:
            attributes["tool_call_key"] = key
        # Named from the sanitized name, not the raw one: a span name is written down
        # like any other field, and the raw name is model-controlled text.
        with span(f"tool.{name or 'unknown'}", attributes=attributes) as handle:
            terminal = None
            async for item in next_handler():
                state = tool_state(item)
                if state is not None:
                    # Recorded before the final item is handed on: a consumer that stops
                    # the moment a candidate is published must not turn an execution that
                    # already finished into a failure.
                    terminal = state
                    handle.annotate(result_code=state)
                    handle.capture("sdk_tool_result", tool_result(item), source="sdk_tool_result")
                yield item
            if terminal is None:
                # What arrived was never a result, so the pieces are not reported as one.
                handle.annotate(result_code=INCOMPLETE)
                handle.capture("sdk_tool_result", None, source="sdk_tool_result",
                               availability="incomplete")


__all__ = ["INCOMPLETE", "MODEL_ROUND_SPAN", "TraceMiddleware", "tool_state"]
