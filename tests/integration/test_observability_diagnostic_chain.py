"""Real SDK, HTTP-boundary fixtures and real pytest; no paid provider requests."""
import asyncio
import json
from dataclasses import replace
import httpx
from reproagent.app import create_controller
from reproagent.core.models import ModelConfig,ModelRequest,BudgetLimits
from reproagent.core.budget import Budget
from reproagent.observability import trace_session,write_trace
from reproagent.observability_decisions import budget_snapshot
from reproagent.trace_rendering import write_trace_html
from reproagent.trace_view_model import build_trace_view
from tests.unit.test_controller import setup,ScriptedModel
from tests.integration.test_agentscope_backends import CANDIDATE,text_response,tool_response


def run_case(tmp_path,projects,facts,*,failure=False,revision=False,trace=True):
    request,_,_,ctx=setup(tmp_path,projects,facts)
    limits=BudgetLimits(agent_steps=3 if failure else 8)
    request=replace(request,limits=limits)
    ctx=replace(ctx,budget=Budget(limits))
    script=ScriptedModel(missing=failure)
    seen=[];rounds=0;read_ref=None
    async def handle(http_request):
        nonlocal rounds,read_ref
        payload=json.loads(http_request.content);seen.append(payload)
        if payload.get('tools'):
            rounds+=1
            if failure and rounds>1:
                frozen=next(e for e in controller.explorer.project.snapshot.files if e.path=='example/parser.py')
                return tool_response('revise_contract',{'source_refs':[{'path':frozen.path,'content_hash':frozen.content_hash,
                     'start_line':1,'end_line':2}],'reason':'cite unread range'},'cite-'+str(rounds))
            if revision and rounds==1:
                path=controller.explorer.project.snapshot.root/'example/parser.py'
                return tool_response('Read',{'file_path':str(path)},'read')
            if revision and rounds==2:
                body=payload['messages'][-1]['content']
                import re
                data=json.loads(re.search(r'<evidence>(.*?)</evidence>',body,re.S).group(1))
                read_ref=data['refs'][0]
                return tool_response('revise_contract',{'source_refs':[read_ref],'reason':'include parser evidence'},'revision')
            return tool_response('write_candidate',{'files':[{'path':'tests/test_repro.py','content':CANDIDATE,'role':'test'}],
                 'hypothesis':'empty input'},'write')
        data=json.loads(payload['messages'][-1]['content'])
        kind='verdict' if 'available_refs' in data else 'contract'
        response=await script.complete(ModelRequest(tuple(payload['messages']),kind),ctx)
        if revision and kind=='contract' and script.kinds.count('contract')>1:
            value=json.loads(response.text);value['source_indices']=[0,1]
            return text_response(json.dumps(value))
        return text_response(response.text)
    model=ModelConfig(base_url='https://offline.example/v1',model='offline',output_limit_field='max_tokens',thinking_mode='disabled')
    controller=create_controller(request,model,transport=httpx.MockTransport(handle))
    async def main():
        with trace_session(enabled=trace) as recorder:
            result=await controller.run(request,ctx)
            doc=recorder.finish(task_id=result.task_id,status=result.status.value,main_duration=result.duration,
                stop_reason=result.stop_reason,budget=budget_snapshot(ctx.budget)) if recorder else None
            return result,doc
    result,doc=asyncio.run(main())
    return request,result,doc,seen,ctx


def test_ungrounded_publish_and_unread_citations_end_in_step_exhaustion(tmp_path,projects,facts):
    request,result,doc,seen,ctx=run_case(tmp_path,projects,facts,failure=True)
    assert result.status.value=='EXHAUSTED' and ctx.budget.steps_used==3
    assert doc['diagnostic_summary']['budget_dimension']=='steps'
    names=[s['name'] for s in doc['spans']]
    assert names.count('publication.rejected')==1 and names.count('citation.rejected')==2
    assert 'runner.execute' not in names and 'verify' not in names and 'contract.revised' not in names
    permissions=[s for s in doc['spans'] if s['name']=='permission']
    assert all(s['attributes']['permission_result']=='ALLOWED' for s in permissions)
    assert all(s['attributes']['phase_id'] for s in permissions)
    assert all(p.get('thinking')=={'type':'disabled'} for p in seen)
    view=build_trace_view(doc)
    assert view['diagnostics'][-1]['stop_reason']=='EXHAUSTED'
    write_trace(request.output_dir,doc);assert write_trace_html(request.output_dir,doc).is_file()


def test_successful_revision_and_candidate_keep_the_business_result(tmp_path,projects,facts):
    request,result,doc,seen,ctx=run_case(tmp_path,projects,facts,revision=True)
    assert result.status.value=='DONE'
    revised=[s for s in doc['spans'] if s['name']=='contract.revised']
    assert len(revised)==1 and revised[0]['attributes']['contract_version']==2
    assert any(s['name']=='verify' for s in doc['spans'])
    assert ctx.budget.steps_used==3
    plain=run_case(tmp_path/'plain',projects,facts,revision=True,trace=False)
    assert plain[1].status==result.status and plain[1].evidence_level==result.evidence_level
    assert plain[4].budget.steps_used==ctx.budget.steps_used
    assert len(plain[3])==len(seen)
