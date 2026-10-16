"""Refusal lessons follow actual product errors into real SDK wire requests."""
import asyncio
import json
from dataclasses import asdict, replace

import pytest

from reproagent import experience as exp
from reproagent.core.models import EvidenceRef
from reproagent.core.serialization import canonical_bytes, bytes_hash
from reproagent.learning_material import capture_exploration
from tests.integration.test_agentscope_experience_triggers import configured, run, injected
from tests.integration.test_agentscope_runtime import tool_reply, write_payload, agent_context
from tests.integration.test_agentscope_domain_tools import call, text_of, candidate_ids

GROUND = {"kind":"tool_error","tool":"write_candidate","code":"GROUNDED_CONTRACT_REQUIRED"}
OVERFLOW = {"kind":"controller_error","tool":"","code":"REVIEW_CONTEXT_TOO_LARGE"}


def learn(env, fact):
    entry = {"id":"E1","kind":"tool_error","text":canonical_bytes(fact).decode()}
    raw = canonical_bytes(entry)+b"\n"
    path = env.store.root/'learning/evidence.jsonl'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(raw)
    material = exp.LearningInput({"evidence":[entry]},
        {"E1":EvidenceRef('learning/evidence.jsonl',bytes_hash(raw),1,1)},'offline@hash',0)
    proposal = {"experience":{"category":"workflow","tags":["pytest"], "name":"Prerequisites", "description":"When a grounded contract is required.", "name":"Prerequisites", "description":"When a grounded contract is required.",
        "summary":"When the corresponding refusal occurs, repair its missing prerequisites.",
        "detail":"Observed pitfall; recovery benefit remains unverified.","evidence_ids":["E1"]}}
    return exp.validate_experience(proposal,material,env.store)


@pytest.mark.parametrize('patch,missing', [({'expected':''},['expected']),
    ({'sources':()},['sources']),({'missing_information':('Which behavior?',)},['missing_information'])])
def test_actual_grounded_refusal_has_precise_code_without_publishing(tmp_path, projects, facts, patch, missing):
    env = configured(tmp_path,projects,facts)
    env.service.bind_contract(replace(env.service.contract,**patch))
    response = call(env,'write_candidate',write_payload())
    assert response.metadata.get('reproagent_error_code') == GROUND['code']
    payload = json.loads(text_of(response))
    assert payload['code'] == GROUND['code'] and payload['missing'] == missing
    assert payload['next_action']
    assert candidate_ids(env) == [] and not env.gate.finished


def test_grounded_capture_learning_and_next_task_wire_injection(tmp_path, projects, facts):
    first = configured(tmp_path/'first',projects,facts)
    first.service.bind_contract(replace(first.service.contract,sources=()))
    first.answers.extend([tool_reply('write_candidate',write_payload(),call_id='ungrounded'),
        tool_reply('request_information',{'question':'missing source'},call_id='stop')])
    run(first)
    error = capture_exploration(first.runtime.messages,())['tool_errors'][0]
    assert error['error_code'] == GROUND['code'] and error['link_state'] == 'matched'
    learned = learn(first,error)
    assert [asdict(t) for t in learned.triggers] == [GROUND]
    library = tmp_path/'library.json'
    assert exp.append_experience(library,learned,None) == 'written'
    frozen = library.read_bytes()
    memory = exp.load_experience_snapshot(library).cards[0]
    second = configured(tmp_path/'second',projects,facts,memory=memory)
    second.service.bind_contract(replace(second.service.contract,sources=()))
    second.answers.extend([tool_reply('write_candidate',write_payload(),call_id='ungrounded'),
        tool_reply('list_experiences',{'category':'workflow'},call_id='catalog'),
        tool_reply('read_experience',{'id':learned.id},call_id='memory'),
        tool_reply('request_information',{'question':'missing source'},call_id='stop')])
    run(second)
    assert second.initial == () and injected(second.requests[0]) == []
    assert injected(second.requests[1]) == []
    assert learned.detail in json.dumps(second.requests[4]["messages"])
    assert second.context.budget.steps_used == len(second.requests) == 5
    assert candidate_ids(second) == [] and library.read_bytes() == frozen


def test_generic_error_text_cannot_be_promoted_to_grounded_code(tmp_path, projects, facts, monkeypatch):
    env = configured(tmp_path,projects,facts)
    def unrelated(draft):
        raise ValueError('candidate requires a grounded contract but this is an unrelated failure')
    monkeypatch.setattr(env.service,'publish',unrelated)
    response = call(env,'write_candidate',write_payload())
    assert response.metadata.get('reproagent_error_code') is None


def test_controller_overflow_fact_is_citable_and_reaches_next_sdk_phase(tmp_path, projects, facts):
    from reproagent.core.models import BudgetLimits
    from tests.unit.test_controller import controller_for, ScriptedModel, publish, ask
    request, controller, context = controller_for(tmp_path/'control',projects,facts,ScriptedModel(),
        BudgetLimits(tool_response_bytes=1024, review_context_bytes=1024),plan=[publish(),publish(hypothesis='same'),ask('stop')])
    result = asyncio.run(controller.run(request,context))
    phases = controller.explorer.contexts
    assert phases[0].experience_events == ()
    assert phases[1].experience_events == (OVERFLOW,)
    assert phases[2].experience_events == ()  # duplicate outcome cannot replay the old overflow
    material = exp.build_learning_input(controller.store,result,facts.context())
    assert material is not None
    entry = next(e for e in material.payload['evidence'] if e['kind']=='tool_error'
        and json.loads(e['text']).get('code')=='REVIEW_CONTEXT_TOO_LARGE'
        and json.loads(e['text']).get('kind')=='action.rejected')
    assert json.loads(entry['text'])['action'] == 'run_candidate'
    proposal = {'experience':{'category':'workflow','tags':['pytest'], 'name':'Review overflow', 'description':'When program review evidence exceeds bounds.', 'name':'Review overflow', 'description':'When program review evidence exceeds bounds.',
        'summary':'When review evidence overflows, narrow the candidate.',
        'detail':'Actual program overflow; advice not yet validated.','evidence_ids':[entry['id']]}}
    learned = exp.validate_experience(proposal,material,controller.store)
    assert [asdict(t) for t in learned.triggers] == [OVERFLOW]
    library = tmp_path/'overflow-library.json'
    assert exp.append_experience(library,learned,None) == 'written'
    frozen = library.read_bytes()
    env = configured(tmp_path/'sdk',projects,facts,memory=exp.load_experience_snapshot(library).cards[0])
    env.answers.extend([tool_reply('write_candidate',write_payload(),call_id='candidate'),
        tool_reply('list_experiences',{'category':'workflow'},call_id='catalog'),
        tool_reply('read_experience',{'id':learned.id},call_id='memory'),
        tool_reply('request_information',{'question':'narrow candidate'},call_id='stop')])
    async def go():
        try:
            assert (await env.runtime.explore(agent_context(env))).kind == 'candidate'
            phase = replace(agent_context(env),experience_events=phases[1].experience_events)
            assert (await env.runtime.explore(phase)).kind == 'request_information'
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    asyncio.run(go())
    assert env.initial == () and injected(env.requests[0]) == []
    assert injected(env.requests[1]) == []
    assert learned.detail in json.dumps(env.requests[4]["messages"])
    assert env.context.budget.steps_used == len(env.requests) == 5
    assert library.read_bytes() == frozen


def test_feedback_prose_does_not_create_controller_trigger(tmp_path, projects, facts):
    from reproagent.experience_triggers import ExperienceTrigger
    from tests.unit.test_experience_trigger_view import triggered
    memory = triggered('overflow lesson','UNIQUE_OVERFLOW',condition=ExperienceTrigger(**OVERFLOW))
    env = configured(tmp_path,projects,facts,memory=memory)
    env.answers.append(tool_reply('request_information',{'question':'stop'},call_id='stop'))
    async def go():
        try:
            return await env.runtime.explore(agent_context(env,feedback='REVIEW_CONTEXT_TOO_LARGE'))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    asyncio.run(go())
    assert injected(env.requests[0]) == []
