import asyncio
import importlib.util
from pathlib import Path
import pytest
from agentscope.middleware import TracingMiddleware
from opentelemetry import trace

spec=importlib.util.spec_from_file_location("trace_cases",Path(__file__).with_name("test_agentscope_observability.py"))
cases=importlib.util.module_from_spec(spec);spec.loader.exec_module(cases)


def test_native_agent_model_tool_spans_are_real_sdk_spans(tmp_path, projects, facts):
    with cases.traced(tmp_path,projects,facts,answers=[cases.read_reply(None,"call-1"),cases.text_reply()]) as env:
        env.answers[0]=cases.read_reply(env.module,"call-1")
        cases.explore(env)
        doc=cases.document(env)
    native=[s for s in doc['spans'] if s.get('instrumentation_scope')=='agentscope']
    assert {s['name'] for s in native} >= {'sdk.agent_reply','sdk.model_call','tool.Read'}
    assert any(s['name']=='agent.reasoning' for s in doc['spans'])
    assert doc['schema_version']==2
    assert doc['summary']['tool_executions']==1
    assert doc['summary']['http_attempts']==2
    assert doc['summary']['usage']['input_tokens']==20
    tool=next(s for s in native if s['name']=='tool.Read')
    permission=next(s for s in doc['spans'] if s['name']=='permission')
    assert tool['attributes']['tool_call_key']==permission['attributes']['tool_call_key']
    assert tool['content_refs']


def test_native_observer_failure_never_replays_handler():
    from reproagent.adapters.agentscope.observability import SafeTracingMiddleware
    from reproagent.observability import trace_session
    from types import SimpleNamespace
    calls=[]
    async def handler(**kwargs):
        calls.append(1)
        return 'domain-result'
    async def run():
        with trace_session() as recorder:
            # Invalid observation surface: native tracing expects agent.state.
            result=await SafeTracingMiddleware().on_model_call(
                SimpleNamespace(),{'current_model':NativeBadModel()},handler)
            doc=recorder.finish(task_id='t',status='DONE',main_duration=1)
            return result,doc
    from agentscope.model import ChatModelBase
    class NativeBadModel(ChatModelBase):
        def __init__(self): pass
        @property
        def model_name(self): raise ValueError('observer-only')
        async def __call__(self,*args,**kwargs): raise AssertionError('must not call model')
    result,doc=asyncio.run(run())
    assert result=='domain-result' and calls==[1]
    assert doc['partial']


def test_native_observer_failure_after_handler_returns_keeps_result():
    from reproagent.adapters.agentscope.observability import SafeTracingMiddleware
    from reproagent.observability import trace_session
    from agentscope.model import OpenAIChatModel
    from agentscope.credential import OpenAICredential
    from types import SimpleNamespace
    model=OpenAIChatModel(model='offline',credential=OpenAICredential(api_key='offline'))
    calls=[]
    class BadResult:
        @property
        def usage(self): raise ValueError('observer-only')
    result=BadResult() # native extraction fails AFTER handler.
    async def handler(**kwargs): calls.append(1);return result
    async def run():
        with trace_session() as recorder:
            actual=await SafeTracingMiddleware().on_model_call(
                SimpleNamespace(state=SimpleNamespace(session_id='x')),
                {'current_model':model,'messages':[]},handler)
            doc=recorder.finish(task_id='t',status='DONE',main_duration=1)
            return actual,doc
    actual,doc=asyncio.run(run())
    assert actual is result and calls==[1] and doc['partial']
