"""The actual search capability is checked before a billed contract request."""
import asyncio
from dataclasses import replace
import os
from pathlib import Path

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import ModelConfig, TaskState
from tests.integration.test_agentscope_snapshot_tools import environment, find_ripgrep
from tests.unit.test_controller import setup


def test_configured_rg_is_used_and_real_search_preflight_is_cached(tmp_path,projects,facts,monkeypatch):
    rg=find_ripgrep()
    assert rg, "acceptance requires the real rg executable"
    monkeypatch.setenv('REPROAGENT_RG_PATH',rg)
    monkeypatch.setattr('reproagent.adapters.agentscope.snapshot_backend.shutil.which',lambda _:None)
    env=environment(tmp_path,projects,facts)
    assert env.backend.rg_path==rg
    asyncio.run(env.backend.preflight())
    asyncio.run(env.backend.preflight())
    events=[e for e in env.store.read_events()[0] if e.kind=='tools.preflight']
    assert len(events)==1 and events[0].payload['result_code']=='READY'
    assert events[0].payload['executed'] is True
    assert env.context.budget.steps_used==0


def test_missing_rg_stops_before_any_model_request(tmp_path,projects,facts,monkeypatch):
    monkeypatch.delenv('REPROAGENT_RG_PATH',raising=False)
    monkeypatch.setattr('reproagent.adapters.agentscope.snapshot_backend.shutil.which',lambda _:None)
    request,_,_,context=setup(tmp_path,projects,facts)
    requests=[]
    def handle(req):
        requests.append(req)
        raise AssertionError('missing search must be found before the first model call')
    controller=create_controller(request,ModelConfig(base_url='https://offline.example/v1',model='offline',
        output_limit_field='max_tokens'),transport=httpx.MockTransport(handle))
    result=asyncio.run(controller.run(request,context))
    assert result.status==TaskState.NEEDS_INFORMATION
    assert not requests and context.budget.steps_used==0
    assert any('GREP_UNAVAILABLE' in u for u in result.uncertainties)
    event=next(e for e in controller.store.read_events()[0] if e.kind=='tools.preflight')
    assert event.payload['result_code']=='GREP_UNAVAILABLE'


def test_present_but_broken_rg_cannot_pass_preflight(tmp_path,projects,facts,monkeypatch):
    # A path existing on disk is insufficient: this is deliberately not an executable.
    broken=tmp_path/'broken-rg.exe';broken.write_text('not an executable')
    monkeypatch.setenv('REPROAGENT_RG_PATH',str(broken))
    env=environment(tmp_path,projects,facts)
    with pytest.raises(BudgetStopped,match='GREP_EXECUTION_FAILED'):
        asyncio.run(env.backend.preflight())
    assert env.context.budget.steps_used==0


def test_candidate_warning_feedback_reaches_next_request_and_can_recover(tmp_path,projects,facts):
    import json
    from reproagent.core.models import ModelRequest
    from tests.integration.test_agentscope_runtime import tool_reply
    from tests.unit.test_controller import ScriptedModel
    from tests.unit.test_context_repair_boundaries import preload_target, WARNING_TEST
    preload_target(projects)
    request,_,_,context=setup(tmp_path,projects,facts)
    request=replace(request,inject_task_state=True)
    script,wires,kinds=ScriptedModel(),[],[]
    async def handle(req):
        payload=json.loads(req.content)
        if payload.get('tools'):
            wires.append(payload)
            text=('import pytest\npytestmark=pytest.mark.filterwarnings("error")\n'+WARNING_TEST
                  if len(wires)==1 else 'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n')
            return tool_reply('write_candidate',{'files':[{'path':'tests/test_repro.py','content':text,'role':'test'}],
                'hypothesis':'使用原版代码构造最小复现'},call_id='publish'+str(len(wires)))
        data=json.loads(payload['messages'][-1]['content'])
        kind='verdict' if 'available_refs' in data else 'contract'
        kinds.append(kind)
        reply=await script.complete(ModelRequest(tuple(payload['messages']),kind),context)
        return httpx.Response(200,json={'id':'offline','object':'chat.completion','created':1,'model':'offline',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':reply.text}}]})
    controller=create_controller(request,ModelConfig(base_url='https://offline.example/v1',model='offline',
        output_limit_field='max_tokens'),transport=httpx.MockTransport(handle))
    result=asyncio.run(controller.run(request,context))
    assert result.status==TaskState.DONE and len(wires)==context.budget.steps_used==2
    assert kinds==['contract','verdict','verdict']
    messages=[]
    for message in wires[1]['messages']:
        if message['role']!='user' or not isinstance(message.get('content'),str):continue
        try:value=json.loads(message['content'])
        except ValueError:continue
        if isinstance(value,dict):messages.append(value)
    phase=next(m for m in reversed(messages) if m.get('feedback'))
    feedback=json.loads(phase['feedback'])
    assert feedback['decision']=='continue'
    assert feedback['verdict']['issues'][0]['code']=='CANDIDATE_PRE_TARGET_WARNING'
    assert feedback['verdict']['issues'][0]['recovery']=='revise_candidate'
    state=next(m['task_state'] for m in messages if 'task_state' in m)
    assert state['latest_candidate']['result']=='INVALID_CANDIDATE'
    assert state['completion']['evidence_level']=='NONE'
    assert state['completion']['accepted_candidate_id'] is None
    assert state['budgets']['remaining_steps']==19
