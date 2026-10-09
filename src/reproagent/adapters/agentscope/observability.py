"""Native AgentScope spans plus controlled domain observations.

SafeTracingMiddleware delegates to the real public SDK hooks. The wrapper isolates
observation faults without replaying an already executed handler.
"""
import asyncio
from collections import deque
from agentscope.middleware import MiddlewareBase, TracingMiddleware
from agentscope.tool import ToolResponse
from opentelemetry import trace, context as otel_context
from .middleware import PhaseEnded
from .tools import TOOL_NAMES
from ...core.budget import BudgetStopped
from ...observability import current_handle, span, tool_call_key
from ...otel_backend import current_sink

MODEL_ROUND_SPAN='sdk.model_call'
INCOMPLETE='INCOMPLETE'

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


class SafeTracingMiddleware(TracingMiddleware):
    """Public-hook guard around native tracing; native code creates the spans."""
    async def on_model_call(self,agent,input_kwargs,next_handler):
        called=False;result=None;domain_error=None
        async def tracked(**kwargs):
            nonlocal called,result,domain_error
            called=True
            try:
                result=await next_handler(**kwargs)
                return result
            except BaseException as exc:
                domain_error=exc
                raise
        try:
            return await super().on_model_call(agent,input_kwargs,tracked)
        except BaseException:
            if domain_error is not None: raise domain_error
            sink=current_sink()
            if sink is not None: sink._fault()
            if not called: return await tracked(**input_kwargs)
            return result

    async def _stream(self,native,agent,input_kwargs,next_handler):
        pending=deque();source=None;domain_error=None;exhausted=False
        async def tracked(**kwargs):
            nonlocal source,domain_error,exhausted
            source=next_handler(**kwargs)
            try:
                async for item in source:
                    pending.append(item)
                    yield item
                exhausted=True
            except BaseException as exc:
                domain_error=exc
                raise
        gen=native(agent,input_kwargs,tracked)
        try:
            try:
                async for item in gen:
                    if pending and pending[0] is item: pending.popleft()
                    yield item
            except (GeneratorExit,asyncio.CancelledError):
                raise
            except BaseException:
                if domain_error is not None: raise domain_error
                sink=current_sink()
                if sink is not None: sink._fault()
                while pending: yield pending.popleft()
                if not exhausted:
                    if source is None: source=next_handler(**input_kwargs)
                    async for item in source: yield item
        finally:
            # Always close the domain source, even if native observation fails on close.
            try: await gen.aclose()
            except BaseException:
                sink=current_sink()
                if sink is not None: sink._fault()
            try:
                if source is not None: await source.aclose()
            except BaseException:
                if domain_error is not None and not isinstance(domain_error,(GeneratorExit,asyncio.CancelledError)):
                    raise domain_error
                raise

    async def on_reply(self,agent,input_kwargs,next_handler):
        gen=self._stream(super().on_reply,agent,input_kwargs,next_handler)
        try:
            async for item in gen: yield item
        finally: await gen.aclose()
    async def on_acting(self,agent,input_kwargs,next_handler):
        gen=self._stream(super().on_acting,agent,input_kwargs,next_handler)
        try:
            async for item in gen: yield item
        finally: await gen.aclose()

class ReproTraceMiddleware(MiddlewareBase):
    def __init__(self,context,*,allowed_tools=TOOL_NAMES):
        self.context=context;self.allowed_tools=tuple(allowed_tools)
    async def on_model_call(self,agent,input_kwargs,next_handler):
        handle=current_handle()
        handle.annotate(purpose='exploration')
        try: return await next_handler()
        except (PhaseEnded,BudgetStopped) as exc:
            handle.annotate(result_code='PHASE_ENDED' if isinstance(exc,PhaseEnded) else str(exc.reason))
            if hasattr(handle,'_entry'): handle._entry['_display_status']='ok'
            raise
        except BaseException as exc:
            from ...otel_projection import classify_error
            handle.annotate(**classify_error(exc))
            code=getattr(exc,'code',None)
            if isinstance(code,str): handle.annotate(result_code=code)
            raise
    async def on_reasoning(self,agent,input_kwargs,next_handler):
        # OTel's context cannot remain attached across a yielded stream item.
        sink=current_sink()
        if sink is None:
            async for item in next_handler(): yield item
            return
        parent=otel_context.get_current()
        active=trace.get_tracer('reproagent').start_span('agent.reasoning',context=parent)
        gen=next_handler()
        try:
            while True:
                token=otel_context.attach(trace.set_span_in_context(active,parent))
                try: item=await anext(gen)
                except StopAsyncIteration: break
                finally: otel_context.detach(token)
                yield item
        finally:
            try: await gen.aclose()
            finally: active.end()
    async def on_acting(self,agent,input_kwargs,next_handler):
        tool_call=input_kwargs.get('tool_call')
        raw=getattr(tool_call,'name','')
        name=raw if raw in self.allowed_tools else ''
        handle=current_handle()
        handle.annotate(tool=name,tool_call_key=tool_call_key(sdk_call_id=getattr(tool_call,'id',''),
            step_index=self.context.budget.steps_used))
        terminal=None
        gen=next_handler()
        try:
            async for item in gen:
                state=tool_state(item)
                if state is not None:
                    terminal=state;handle.annotate(result_code=state)
                    handle.capture('sdk_tool_result',tool_result(item),source='sdk_tool_result')
                yield item
        except asyncio.CancelledError:
            if hasattr(handle,"_entry"): handle._entry["_display_status"]="cancelled"
            raise
        except GeneratorExit:
            if terminal is not None and hasattr(handle,'_entry'): handle._entry['_display_status']='ok'
            raise
        finally:
            await gen.aclose()
            if terminal is None:
                handle.annotate(result_code=INCOMPLETE)
                handle.capture('sdk_tool_result',None,source='sdk_tool_result',availability='incomplete')

# Temporary import compatibility for test helpers; no second engine or spans.
TraceMiddleware=ReproTraceMiddleware
