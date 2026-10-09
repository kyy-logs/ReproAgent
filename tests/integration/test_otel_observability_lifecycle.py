import asyncio
import json
import httpx
import pytest
from opentelemetry import trace
from opentelemetry.trace import Status,StatusCode
from reproagent.observability import trace_session,span,record_check,capture


def test_all_purposes_and_learning_share_one_trace():
    with trace_session() as recorder:
        for name in ('contract','explore','verify','seal','learn'):
            with span(name):
                with span('model.logical',attributes={'purpose':{'explore':'exploration','verify':'verdict','learn':'learning'}.get(name,name)}):
                    with span('model.http_attempt',attributes={'attempt':1}): pass
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1,learning={'status':'failed'})
    assert len(doc['trace_id'])==32 and doc['status']=='DONE'
    assert doc['spans'][0]['span_id']==doc['root_span_id']
    assert {s['parent_span_id'] for s in doc['spans'] if s['name'] in ('contract','explore','verify','seal','learn')}=={doc['root_span_id']}


def test_retry_usage_is_charged_only_at_http_leaves():
    with trace_session() as recorder:
        with trace.get_tracer('agentscope').start_as_current_span('chat offline') as native:
            native.set_attribute('gen_ai.operation.name','chat')
            native.set_attribute('gen_ai.usage.input_tokens',999)
            with span('model.logical'):
                for i in range(3):
                    with span('model.http_attempt',attributes={'usage':{'input_tokens':10,'output_tokens':5},'cost':0.1,'attempt':i+1}): pass
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1)
    assert doc['summary']['logical_calls']==1 and doc['summary']['http_attempts']==3
    assert doc['summary']['usage']['input_tokens']==30
    assert doc['summary']['cost']['amount']==pytest.approx(0.3)


@pytest.mark.parametrize('status,category',[(400,'input'),(401,'authentication'),(403,'authentication'),(429,'rate_limit'),(503,'provider'),(404,'unknown')])
def test_error_categories_preserve_native_and_domain_status(status,category):
    from reproagent.otel_projection import classify_error
    exc=httpx.HTTPStatusError('failed',request=httpx.Request('GET','https://offline.invalid'),response=httpx.Response(status))
    assert classify_error(exc)==dict(error_category=category,classification_source='http_status')


def test_program_checks_keep_short_circuit_and_evidence_isolation():
    seen=[]
    with trace_session() as recorder:
        with span('verify'):
            result=record_check('binding',False) and record_check('hash',seen.append('unwanted'))
            capture('program_artifact',{'reason':'existing-decision'},source='program_artifact')
        doc=recorder.finish(task_id='one',status='FAILED',main_duration=1)
    assert result is False and seen==[]
    checks=[s for s in doc['spans'] if s['name'].startswith('check.')]
    assert len(checks)==1 and checks[0]['kind']=='point'
    assert checks[0]['otel_span_id'] is None
