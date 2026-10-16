"""Real SDK wires, persistence and independent local replay retain scoped unknowns."""
import asyncio
import json
from dataclasses import replace

import httpx

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, TaskState
from tests.integration.test_agentscope_backends import scripted_transport
from tests.unit.test_controller import ScriptedModel, setup


NOTE="Other input variants remain unknown; not required for the current empty-list trigger."


class ScopedModel(ScriptedModel):
    async def complete(self, request, context):
        reply=await super().complete(request,context)
        if request.response_kind == "contract":
            data=json.loads(reply.text)
            data["nonblocking_information"]=[NOTE]
            return replace(reply,text=json.dumps(data))
        return reply


def test_optional_scope_field_reaches_explorer_verifier_and_final_report(tmp_path,projects,facts):
    request,_,_,context=setup(tmp_path,projects,facts)
    wires=[]
    delegate=scripted_transport(ScopedModel(),context,[],[])
    async def handler(http_request):
        wires.append(json.loads(http_request.content))
        return await delegate(http_request)
    model=ModelConfig(base_url="https://offline.example/v1",model="offline",output_limit_field="max_tokens")
    controller=create_controller(request,model,transport=httpx.MockTransport(handler))
    result=asyncio.run(controller.run(request,context))
    assert result.status==TaskState.DONE
    exploration=next(w for w in wires if w.get("tools"))
    system="\n".join(m["content"] for m in exploration["messages"] if m["role"]=="system")
    assert "trace the reported diagnostic" in system and "actual return type" in system
    phase=json.loads(next(m["content"] for m in exploration["messages"] if m["role"]=="user"))
    assert phase["contract"]["nonblocking_information"]==[NOTE]
    assert phase["contract"]["missing_information"]==[]
    verdicts=[json.loads(w["messages"][-1]["content"]) for w in wires
              if not w.get("tools") and "available_refs" in json.loads(w["messages"][-1]["content"])]
    assert len(verdicts)==2
    assert all(v["contract"]["nonblocking_information"]==[NOTE] for v in verdicts)
    candidate=controller.store.load_record("candidates",result.accepted_candidate_id)
    contract=controller.store.load_contract(candidate.contract_id,candidate.contract_version)
    assert contract.nonblocking_information==(NOTE,)
    report=(request.output_dir/"artifacts/reproduction/report.md").read_text(encoding="utf-8")
    assert NOTE in report and "非阻塞未知项" in report
    assert len(list((request.output_dir/"runs").glob("*/execution.json")))==2
