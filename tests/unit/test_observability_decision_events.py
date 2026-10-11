import asyncio
from dataclasses import replace
from reproagent.observability import TraceRecorder
from tests.unit.test_controller import controller_for, ScriptedModel, PhasePlan, revise, source_ref, ask
from tests.integration.test_agentscope_domain_tools import environment, call, write_payload


def test_contract_change_requires_successful_save(tmp_path,projects,facts):
    class RevisedModel(ScriptedModel):
        async def complete(self,request,context):
            import json
            reply=await super().complete(request,context)
            if request.response_kind=='contract' and self.kinds.count('contract')>1:
                value=json.loads(reply.text);value['expected']='empty input returns an empty list without exceptions';value['source_indices']=[0,1]
                return replace(reply,text=json.dumps(value))
            return reply
    request,controller,ctx=controller_for(tmp_path,projects,facts,RevisedModel())
    plan=controller.explorer_factory
    plan.plan=[revise(lambda:[source_ref(plan.explorer)]),ask()]
    with TraceRecorder() as recorder:
        asyncio.run(controller.run(request,ctx))
        doc=recorder.finish(task_id="one",status="DONE",main_duration=1)
    initial=[s for s in doc["spans"] if s["name"]=="contract.established"]
    revised=[s for s in doc["spans"] if s["name"]=="contract.revised"]
    assert len(initial)==len(revised)==1
    assert revised[0]["attributes"]["previous_contract_version"]==1
    assert revised[0]["attributes"]["contract_version"]==2
    assert "expected" in revised[0]["attributes"]["changed_fields"]


def test_publication_rejection_keeps_actual_contract(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts,expected="")
    with TraceRecorder() as recorder:
        result=call(env,"write_candidate",write_payload())
        doc=recorder.finish(task_id="one",status="DONE",main_duration=1)
    refused=[s for s in doc["spans"] if s["name"]=="publication.rejected"]
    assert result.state.value=="error"
    assert len(refused)==1
    assert refused[0]["attributes"]["decision_code"]=="CONTRACT_UNGROUNDED"
    assert refused[0]["attributes"]["contract_version"]==1
    assert not list((env.task/"candidates").glob("*/manifest.json"))


def test_citation_rejection_records_the_checked_range(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts)
    frozen=next(f for f in env.snapshot.files if f.path=="example/中文模块.py")
    with TraceRecorder() as recorder:
        for hash_ in ("0"*64,frozen.content_hash):
            call(env,"revise_contract",{"source_refs":[{"path":frozen.path,"content_hash":hash_,
                 "start_line":1,"end_line":1}],"reason":"need source"})
        doc=recorder.finish(task_id="one",status="DONE",main_duration=1)
    refused=[s for s in doc["spans"] if s["name"]=="citation.rejected"]
    assert [s["attributes"]["decision_code"] for s in refused]==["CITATION_HASH_MISMATCH","CITATION_NOT_DISPLAYED"]
    assert all(s["attributes"]["source_ref"]["start_line"]==1 for s in refused)
    assert not any(s["name"]=="contract.revised" for s in doc["spans"])


def test_contract_diff_does_not_mutate_or_infer_changes():
    from reproagent.observability_decisions import contract_diff
    from reproagent.core.models import IssueContract
    before=IssueContract("one",expected="old",missing_information=("question",))
    after=replace(before,version=2,expected="new",missing_information=())
    diff=contract_diff(before,after)
    assert diff["changed_fields"]==["expected","missing_information"]
    assert diff["changes"]["expected"]=={"before":"old","after":"new"}
    assert before.expected=="old"
