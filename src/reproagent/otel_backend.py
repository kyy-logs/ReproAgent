"""Process-owned OTel infrastructure; no exporters, queues or network access."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock, get_ident
import asyncio
from opentelemetry import context, trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor, SpanLimits
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision

_active_sink = ContextVar("reproagent_otel_sink", default=None)
_lock = RLock()
_provider = None
_routes = {}
WARNING_PROVIDER = "ReproAgent tracing is unavailable: an external tracer provider is already configured."

@dataclass(frozen=True)
class ProviderActivation:
    available: bool
    warning: str | None = None

class SessionSampler(Sampler):
    def should_sample(self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None):
        sink = _active_sink.get()
        enabled = sink is not None and sink._active and not sink._frozen
        return SamplingResult(Decision.RECORD_AND_SAMPLE if enabled else Decision.DROP, attributes, trace_state)
    def get_description(self):
        return "ReproAgent active local session only"

class TaskSpanProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        sink = _active_sink.get()
        if sink is None or not sink._active or sink._frozen:
            return
        try:
            with _lock:
                _routes[span.context.trace_id] = sink
            sink.started(span)
        except Exception:
            sink._fault()
    def on_end(self, span):
        with _lock:
            sink = _routes.get(span.context.trace_id)
        if sink is not None and not sink._frozen:
            try:
                from .otel_projection import project_native_span
                project_native_span(span, sink=sink)
            except Exception:
                sink._fault()
    def force_flush(self, timeout_millis=30000):
        return True
    def shutdown(self):
        pass

def ensure_local_provider():
    global _provider
    with _lock:
        current = trace.get_tracer_provider()
        if _provider is not None and current is _provider:
            return ProviderActivation(True)
        if not isinstance(current, trace.ProxyTracerProvider):
            return ProviderActivation(False, WARNING_PROVIDER)
        provider = TracerProvider(sampler=SessionSampler(), span_limits=SpanLimits(
            max_attributes=64, max_events=32, max_links=0, max_attribute_length=65536))
        provider.add_span_processor(TaskSpanProcessor())
        trace.set_tracer_provider(provider)
        if trace.get_tracer_provider() is not provider:
            provider.shutdown()
            return ProviderActivation(False, WARNING_PROVIDER)
        _provider = provider
        return ProviderActivation(True)

def context_owner():
    try: task=asyncio.current_task()
    except RuntimeError: task=None
    return get_ident(),task

class TaskTraceSession:
    def __init__(self, sink):
        self.sink = sink
        self.closed = False
        self.token = None
        self.root = None
        self.owner = context_owner()
    def close(self):
        if self.closed:
            self.detach_context()
            return
        self.closed = True
        if self.root is not None:
            self.root.end()
            with _lock:
                _routes.pop(self.root.get_span_context().trace_id, None)
        self.sink._active = False
        self.detach_context()
    def detach_context(self):
        if self.token is not None and context_owner() == self.owner:
            try:
                context.detach(self.token)
            except (ValueError, RuntimeError):
                self.sink._fault()
            self.token = None

@contextmanager
def open_task_trace(sink):
    activation = ensure_local_provider()
    if not activation.available:
        raise RuntimeError(activation.warning)
    sink._active = True
    sink._started_clock = sink._clock()
    sink._started_at = sink._wall()
    token = _active_sink.set(sink)
    session = TaskTraceSession(sink)
    try:
        session.root = trace.get_tracer("reproagent").start_span("reproagent.task", context=Context())
        sink.trace_id = format(session.root.get_span_context().trace_id, "032x")
        sink.root_span_id = format(session.root.get_span_context().span_id, "016x")
        session.token = context.attach(trace.set_span_in_context(session.root, Context()))
        sink._session = session
        yield session
    finally:
        session.close()
        _active_sink.reset(token)

def current_sink():
    sink = _active_sink.get()
    return sink if sink is not None and sink._active and not sink._frozen else None
