import asyncio
import json
from dataclasses import replace

import pytest

pytest.importorskip('agentscope')

from reproagent.adapters.agentscope.explorer import AgentScopeExplorer
from reproagent.core.models import BudgetLimits, FixValidationRequest, ModelConfig, ModelResponse
from reproagent.app import create_controller
from tests.unit.test_controller import setup, ScriptedModel


def test_real_sdk_explorer_preserves_contract_and_requires_real_replay(tmp_path, projects, facts):
    request, model, _, ctx = setup(tmp_path, projects, facts)
    controller = create_controller(request, ModelConfig(), gateway=model, agent_backend='agentscope')
    fixed = projects.fixed(tmp_path/'fixed-private')
    result=asyncio.run(controller.run(request,ctx,FixValidationRequest(fixed,request.language.python)))
    assert result.status.value=='DONE', result
    assert result.evidence_level.value=='DIFFERENTIAL_VALIDATED'
    assert ctx.budget.steps_used==3
    assert len(list((request.output_dir/'runs').glob('*/execution.json')))==3
    assert str(fixed) not in json.dumps(model.messages)
    assert (request.output_dir/'artifacts/reproduction/report.md').is_file()
    backend=next(event for event in controller.store.read_events()[0] if event.kind=='backend.selected')
    assert backend.payload['agent_backend']=='agentscope' and backend.payload['agentscope_version']=='2.0.9'


def test_sdk_explorer_uses_only_one_model_call_per_decision_and_no_state_leak(tmp_path, projects, facts):
    request, model, _, ctx=setup(tmp_path,projects,facts)
    explorer=AgentScopeExplorer(model,ctx,'tests')
    from reproagent.core.models import IssueDescription, EvidenceContext, SourceRef
    evidence=EvidenceContext((SourceRef('input/issue.md','hash'),),('first original text',))
    first=asyncio.run(explorer.analyze(IssueDescription('first original text','hash'),evidence))
    second=asyncio.run(explorer.analyze(IssueDescription('second original text','hash'),replace(evidence,texts=('second original text',))))
    assert first.expected==second.expected
    assert len([message for message in model.messages if message['role']=='user'])==2
    assert 'first original text' not in model.messages[-1]['content']


def test_sdk_explorer_rejects_extra_actions_through_existing_protocol(tmp_path, projects, facts):
    request, _, _, ctx=setup(tmp_path,projects,facts, limits=BudgetLimits(agent_steps=3))
    class WrongModel(ScriptedModel):
        async def complete(self,request,context):
            if request.response_kind=='action':
                return ModelResponse('[{"name":"request_information","parameters":{"question":"x"}}]')
            return await super().complete(request,context)
    controller=create_controller(request,ModelConfig(),gateway=WrongModel(),agent_backend='agentscope')
    result=asyncio.run(controller.run(request,ctx))
    assert result.status.value=='EXHAUSTED' and ctx.budget.steps_used==3


def test_sdk_explorer_cannot_bypass_candidate_parent(tmp_path,projects,facts):
    request,_,_,ctx=setup(tmp_path,projects,facts, limits=BudgetLimits(agent_steps=3))
    class BadPath(ScriptedModel):
        async def complete(self,request,context):
            if request.response_kind=='action':
                return ModelResponse(json.dumps({'name':'write_candidate','parameters':{'files':[{'path':'outside/test.py','content':'def test_x(): pass','role':'test'}],'hypothesis':'x'}}))
            return await super().complete(request,context)
    controller=create_controller(request,ModelConfig(),gateway=BadPath(),agent_backend='agentscope')
    result=asyncio.run(controller.run(request,ctx))
    assert result.status.value=='EXHAUSTED' and ctx.budget.steps_used==3
    assert not (request.output_dir/'outside').exists()


def test_real_sdk_explorer_cancellation_does_not_trigger_an_extra_model_call(tmp_path,projects,facts):
    from reproagent.adapters.agentscope.explorer import DecisionGateway
    from reproagent.core.budget import BudgetStopped
    from reproagent.core.models import ModelRequest
    calls,closed=[],[]
    class WaitingModel:
        async def complete(self,request,context):
            calls.append(request)
            try: await asyncio.sleep(30)
            finally: closed.append(True)
    async def run():
        _,_,_,ctx=setup(tmp_path,projects,facts)
        pending=asyncio.create_task(DecisionGateway(WaitingModel()).complete(ModelRequest(({'role':'user','content':'{}'},)),ctx))
        while not calls: await asyncio.sleep(0)
        ctx.cancel_event.set()
        with pytest.raises(BudgetStopped): await pending
    asyncio.run(run())
    assert len(calls)==1 and closed
