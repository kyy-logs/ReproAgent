import asyncio
import json

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, TaskState
from tests.integration.test_agentscope_runtime import tool_reply
from tests.unit.test_controller import setup, ScriptedModel


BAD_API="from example.parser import parse\ndef test_empty():\n    assert parse([], builddir='out') == []\n"


@pytest.mark.parametrize('overflow',[False,True])
def test_actual_call_error_reaches_next_sdk_request_without_overriding_review(tmp_path,projects,facts,overflow):
    request,_,_,ctx=setup(tmp_path,projects,facts)
    model=ScriptedModel(classification='NOT_REPRODUCED');wires=[]
    async def handle(req):
        p=json.loads(req.content)
        if p.get('tools'):
            wires.append(p)
            if len(wires)==1:
                return tool_reply('write_candidate',{'files':[{'path':'tests/test_repro.py',
                    'content':("from example.parser import parse\ndef test_empty():\n    assert parse([0]) == [], 'HTML-' + 'x'*18000\n" if overflow else BAD_API),'role':'test'}],'hypothesis':'empty input'},call_id='publish')
            return tool_reply('request_information',{'question':'need original signature'},call_id='stop')
        data=json.loads(p['messages'][-1]['content'])
        reply=await model.complete(ModelRequest(tuple(p['messages']),'verdict' if 'available_refs'in data else 'contract'),ctx)
        return httpx.Response(200,json={'id':'mock','object':'chat.completion','created':1,'model':'offline',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':reply.text}}]})
    controller=create_controller(request,ModelConfig(base_url='https://offline.example/v1',model='offline',
        output_limit_field='max_tokens'),transport=httpx.MockTransport(handle))
    result=asyncio.run(controller.run(request,ctx))
    assert result.status==TaskState.NEEDS_INFORMATION and len(wires)==2
    phases=[]
    for m in wires[1]['messages']:
        if m['role']!='user' or not isinstance(m.get('content'),str):continue
        try:d=json.loads(m['content'])
        except ValueError:continue
        if isinstance(d,dict)and'feedback'in d:phases.append(d)
    feedback=json.loads(phases[-1]['feedback'])
    observed=feedback['execution_observation']
    assert observed['origin']=='program_observation'
    failure=observed['failed_calls'][0]
    assert failure['exception_type']==('builtins.AssertionError' if overflow else 'builtins.TypeError')
    assert ('HTML-' if overflow else 'builddir')in failure['message']
    assert failure['path'].replace('\\','/').endswith('tests/test_repro.py') and failure['lineno']==3
    assert observed['run_id']in feedback['verdict']['run_ids']
    assert feedback['verdict']['issues'][0]['origin']==('program_check' if overflow else 'provider_claim')
    assert feedback['verdict']['issues'][0]['code']==('REVIEW_CONTEXT_TOO_LARGE' if overflow else 'TARGET_TRIGGER_UNCONFIRMED')
    if overflow:
        assert feedback['verdict']['issues'][0]['measurements']['material_sizes'][0]['field']=='failure_evidence'
        assert model.kinds==['contract']
    else:assert model.kinds==['contract','verdict']
