import asyncio
import json
import subprocess
import sys

import httpx
import pytest

pytest.importorskip('agentscope')

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, FixValidationRequest
from tests.unit.test_controller import setup, ScriptedModel


@pytest.mark.parametrize('model_backend', ['native','agentscope'])
@pytest.mark.parametrize('agent_backend', ['native','agentscope'])
def test_four_real_backend_combinations_share_validation_and_independent_replay(tmp_path,projects,facts,model_backend,agent_backend):
    request,_,_,ctx=setup(tmp_path,projects,facts)
    script=ScriptedModel(); calls=[]
    async def handle(http_request):
        payload=json.loads(http_request.content)
        data=json.loads(payload['messages'][-1]['content'])
        kind='verdict' if 'available_refs' in data else 'contract' if 'description' in data else 'action'
        response=await script.complete(ModelRequest(tuple(payload['messages']),kind),ctx)
        calls.append(kind)
        return httpx.Response(200,json={'id':'mock','object':'chat.completion','model':'offline','created':1,
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':response.text}}]})
    model=ModelConfig(base_url='https://offline.example/v1',model='offline',output_limit_field='max_tokens')
    controller=create_controller(request,model,model_backend=model_backend,agent_backend=agent_backend)
    controller.gateway.gateway.transport=httpx.MockTransport(handle)
    fixed=projects.fixed(tmp_path/'fixed-hidden')
    result=asyncio.run(controller.run(request,ctx,FixValidationRequest(fixed,sys.executable)))
    assert result.status.value=='DONE' and result.evidence_level.value=='DIFFERENTIAL_VALIDATED', result
    # One decision writes the candidate; the Controller then runs and submits it
    # itself, so the two verdicts are the only further model calls.
    assert calls==['contract','action','verdict','verdict']
    assert ctx.budget.steps_used==3 and str(fixed) not in json.dumps(script.messages)
    root=request.output_dir/'artifacts/reproduction'
    report=json.loads((root/'report.json').read_text(encoding='utf-8'))
    assert report['backends']['model_backend']==model_backend and report['backends']['agent_backend']==agent_backend
    for label,factory,expected in [('buggy',projects.plain,1),('fixed',projects.fixed,0)]:
        fresh=factory(tmp_path/('fresh-'+label))
        replay=subprocess.run([sys.executable,str(root/'replay.py'),'--repo',str(fresh),'--python',sys.executable,'--output',str(tmp_path/('replay-'+label)),'--install'],capture_output=True,timeout=30)
        assert replay.returncode==expected, replay.stderr.decode(errors='replace')
