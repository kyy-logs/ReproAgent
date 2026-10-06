"""Regressions from the first three historical live-model failures."""
import asyncio
import json

import pytest
import httpx
from dataclasses import replace

from reproagent.core.agent import ReproAgent
from reproagent.core.budget import BudgetStopped, BudgetedGateway
from reproagent.core.models import AgentContext, EvidenceContext, IssueContract, IssueDescription, ModelResponse, ProjectView, SourceRef, TaskState
from reproagent.core.tools import validate_action
from tests.integration.test_runner import setup_runner
from tests.unit.test_controller import setup


VALID_CONTRACT = {'trigger':'parse([])', 'expected':'return []', 'reported_actual':'IndexError',
                  'observable_checks':['parse([]) == []'], 'assumptions':[], 'missing_information':[], 'source_indices':[0]}


class Responses:
    def __init__(self, *responses):
        self.responses = responses
        self.requests = []

    async def complete(self, request, context):
        self.requests.append(request)
        value = self.responses[min(len(self.requests)-1, len(self.responses)-1)]
        return ModelResponse(value if isinstance(value,str) else json.dumps(value))


def analyze(model, facts):
    context = facts.context()
    gateway = BudgetedGateway(model)
    agent = ReproAgent(gateway, context)
    result = asyncio.run(agent.analyze(IssueDescription('parse([]) must return []','hash'),
                                     EvidenceContext((SourceRef('input/issue.md','hash'),),('parse([]) must return []',))))
    return result, context


@pytest.mark.parametrize('invalid', ['   ', '{}\n{}', {}, {k:v for k,v in VALID_CONTRACT.items() if k != 'trigger'},
                                   {**VALID_CONTRACT,'unsupported':'extra'}, {**VALID_CONTRACT, 'trigger':{'function':'parse'}},
                                   {**VALID_CONTRACT,'source_indices':[9]}])
def test_invalid_contract_is_corrected_without_losing_original_sources(invalid, facts):
    model = Responses(invalid, VALID_CONTRACT)
    result, context = analyze(model, facts)
    assert result.expected == 'return []'
    assert result.sources == (SourceRef('input/issue.md','hash'),)
    assert len(model.requests) == 2
    assert context.budget.unknown_cost_calls == 2
    repair = json.loads(model.requests[1].messages[-1]['content'])
    assert repair['protocol_error'] and repair['description'] == 'parse([]) must return []'
    assert repair['sources'][0]['path'] == 'input/issue.md'


def test_repeated_invalid_contract_stops_after_three_accounted_calls(facts):
    model = Responses({**VALID_CONTRACT, 'expected':[]})
    context = facts.context()
    agent = ReproAgent(BudgetedGateway(model), context)
    with pytest.raises(Exception) as error:
        asyncio.run(agent.analyze(IssueDescription('bug','hash'), EvidenceContext((SourceRef('issue','hash'),),('bug',))))
    assert type(error.value).__name__ == 'ModelProtocolError'
    assert len(model.requests) == 3 and context.budget.unknown_cost_calls == 3


def test_cancel_during_protocol_correction_sends_no_second_request(facts):
    context = facts.context()
    class CancellingModel(Responses):
        async def complete(self, request, call_context):
            result = await super().complete(request, call_context)
            call_context.cancel_event.set()
            return result
    model = CancellingModel(' ')
    agent = ReproAgent(BudgetedGateway(model), context)
    with pytest.raises(BudgetStopped) as error:
        asyncio.run(agent.analyze(IssueDescription('bug','hash'), EvidenceContext((SourceRef('issue','hash'),),('bug',))))
    assert error.value.reason == 'CANCELLED' and len(model.requests) == 1


def test_protocol_failure_is_failed_not_a_request_for_user_information(tmp_path, projects, facts):
    request, model, controller, context = setup(tmp_path, projects, facts)
    invalid = Responses({**VALID_CONTRACT,'expected':{'value':[]}})
    controller.gateway = BudgetedGateway(invalid, controller.store)
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.FAILED
    assert result.stop_reason == 'MODEL_PROTOCOL_ERROR'
    assert len(invalid.requests) == 3
    assert (request.output_dir/'artifacts/diagnostic/report.json').is_file()


def test_model_can_choose_search_scope_from_supplied_enum(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    class SchemaReadingModel:
        async def complete(self, request, context):
            payload = json.loads(request.messages[-1]['content'])
            scope = payload['tools']['search_code']['properties']['scope']['enum'][0]
            return ModelResponse(json.dumps({'name':'search_code','parameters':{'query':'parse','scope':scope}}))
    agent = ReproAgent(SchemaReadingModel(), facts.context())
    action = asyncio.run(agent.next_action(AgentContext(IssueContract('c'),ProjectView(snapshot))))
    assert action.parameters['scope'] in ('snapshot','candidates','all')


def test_invalid_search_scope_reports_the_allowed_values():
    with pytest.raises(ValueError) as error:
        validate_action({'name':'search_code','parameters':{'query':'parse','scope':'src/parser.py'}})
    assert all(scope in str(error.value) for scope in ('snapshot','candidates','all'))


def test_contract_schema_explains_required_text_fields(facts):
    class SchemaReadingModel:
        async def complete(self, request, context):
            payload=json.loads(request.messages[-1]['content'])
            properties=payload['response_schema']['properties']
            value=dict(VALID_CONTRACT)
            for name in ('trigger','expected','reported_actual'):
                if properties[name]['type'] != 'string':
                    value[name]={'incorrect':'structured object'}
            return ModelResponse(json.dumps(value))
    result, _ = analyze(SchemaReadingModel(),facts)
    assert result.trigger == 'parse([])' and result.expected == 'return []'


@pytest.mark.parametrize('content,finish', [('', 'stop'), ('{}', 'stop'), ('{"trigger":', 'stop')])
def test_provider_rejected_output_gets_bounded_contract_correction(content, finish, facts):
    from tests.unit.test_model_gateway import gateway
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        value, reason = (content, finish) if len(calls) == 1 else (json.dumps(VALID_CONTRACT), 'stop')
        return httpx.Response(200,json={'choices':[{'finish_reason':reason,'message':{'content':value}}],
                                      'usage':{'prompt_tokens':10,'completion_tokens':5}})
    result, context = analyze(gateway(handler),facts)
    assert result.expected == 'return []' and len(calls) == 2
    assert context.budget.unknown_cost_calls == 2
    assert json.loads(calls[1]['messages'][-1]['content'])['protocol_error']


def test_length_is_not_retried_with_identical_request(facts):
    """A truncation at the configured ceiling cannot be corrected by asking again."""
    from tests.unit.test_model_gateway import gateway
    calls, events = [], []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"trigger":'}}],
                                      'usage':{'prompt_tokens':10,'completion_tokens':5}})
    class Store:
        def append_event(self, kind, refs, payload): events.append(payload)
    context = facts.context()
    agent = ReproAgent(BudgetedGateway(gateway(handler), Store()), context)
    with pytest.raises(Exception) as error:
        asyncio.run(agent.analyze(IssueDescription('bug','hash'), EvidenceContext((SourceRef('issue','hash'),),('bug',))))
    assert type(error.value).__name__ == 'ModelProtocolError'
    assert 'OUTPUT_TRUNCATED' in str(error.value)
    assert len(calls) == 1 and calls[0]['max_completion_tokens'] == 4096
    # The truncated attempt is still billed and timed even though it is not retried.
    assert events[0]['outcome'] == 'OUTPUT_TRUNCATED' and events[0]['finish_reason'] == 'length'
    assert events[0]['usage'] == {'prompt_tokens':10,'completion_tokens':5}
    assert context.budget.unknown_cost_calls == 1


def test_provider_empty_response_is_failed_not_invalid_user_input(tmp_path, projects, facts):
    from tests.unit.test_model_gateway import gateway
    request, _, controller, context = setup(tmp_path,projects,facts)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':''}}]})
    controller.gateway = BudgetedGateway(gateway(handler),controller.store)
    result = asyncio.run(controller.run(request,context))
    assert result.status == TaskState.FAILED and result.stop_reason == 'MODEL_PROTOCOL_ERROR'
    assert len(calls) == 3 and context.budget.unknown_cost_calls == 3


def test_invalid_provider_configuration_is_not_retried_as_model_output(facts):
    from tests.unit.test_model_gateway import gateway
    from reproagent.core.models import BudgetLimits
    calls=[]
    model=gateway(lambda request: calls.append(request))
    context=facts.context(limits=BudgetLimits(model_cost_limit=1))
    agent=ReproAgent(BudgetedGateway(model),context)
    with pytest.raises(ValueError,match='hard model cost limit'):
        asyncio.run(agent.analyze(IssueDescription('bug','hash'),EvidenceContext()))
    assert not calls and context.budget.unknown_cost_calls == 0


def test_generated_candidate_cannot_become_expectation_source(tmp_path,projects,facts):
    from tests.unit.test_controller import ScriptedModel
    request,_,controller,context = setup(tmp_path,projects,facts)
    class SelfCitingModel(ScriptedModel):
        def __init__(self): super().__init__(); self.actions=0; self.analyses=0
        async def complete(self, request, context):
            data = json.loads(request.messages[-1]['content'])
            if request.response_kind == 'contract':
                self.analyses += 1
                if self.analyses > 1:
                    return ModelResponse(json.dumps({**VALID_CONTRACT,'expected':'invented behavior','source_indices':[1]}))
            if request.response_kind == 'action':
                self.actions += 1
                if self.actions == 2:
                    return ModelResponse(json.dumps({'name':'read_file','parameters':{'path':'tests/test_repro.py','start':1,'end':2}}))
                if self.actions == 3:
                    return ModelResponse(json.dumps({'name':'revise_contract','parameters':{'source_refs':json.loads(data['feedback'])['evidence_refs'],'reason':'self-written expectation'}}))
                if self.actions == 4:
                    assert 'original project evidence' in data['feedback']
                    return ModelResponse(json.dumps({'name':'request_information','parameters':{'question':'stop'}}))
            return await super().complete(request,context)
    model = SelfCitingModel()
    controller.gateway = BudgetedGateway(model,controller.store)
    result = asyncio.run(controller.run(request,context))
    assert result.status == TaskState.NEEDS_INFORMATION and model.analyses == 1
    assert len(list((request.output_dir/'contracts').glob('*.json'))) == 1


def test_verdict_text_check_is_corrected_to_boolean_with_original_evidence(tmp_path,projects,facts):
    from tests.unit.test_verifier import EvidenceModel, prepare
    class TextCheckModel(EvidenceModel):
        async def complete(self,request,context):
            response = await super().complete(request,context)
            data=json.loads(request.messages[-1]['content'])
            if len(self.calls) == 1:
                return replace(response,text=json.dumps({**json.loads(response.text),'expected_assertion':'parse([]) == []'}))
            assert 'expected_assertion' in data['protocol_error']
            assert data['response_schema']['properties']['expected_assertion']['type'] == 'boolean'
            return response
    model=TextCheckModel()
    contract,candidate,execution,verifier,*_ = prepare(tmp_path,projects,facts,model=model)
    context=facts.context(); verifier.gateway=BudgetedGateway(model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(execution,),context))
    assert verdict.classification.value == 'REPRODUCED'
    assert len(model.calls) == 2 and context.budget.unknown_cost_calls == 2
    assert json.loads(model.calls[0].messages[-1]['content'])['available_refs'] == json.loads(model.calls[1].messages[-1]['content'])['available_refs']


def test_complete_invalid_json_corrects_at_most_three_times(tmp_path,projects,facts):
    """A complete but structurally wrong verdict is corrected, and only three times."""
    from tests.unit.test_model_gateway import gateway
    from tests.unit.test_verifier import prepare
    contract,candidate,execution,verifier,*_ = prepare(tmp_path,projects,facts)
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
                                      'usage':{'prompt_tokens':10,'completion_tokens':5}})
    context = facts.context()
    verifier.gateway = BudgetedGateway(gateway(handler), None)
    verdict = asyncio.run(verifier.evaluate(contract,candidate,(execution,),context))
    assert verdict.classification is None and 'MODEL_PROTOCOL_ERROR' in verdict.uncertainties
    assert len(calls) == 3 and context.budget.unknown_cost_calls == 3
    assert all(call['max_completion_tokens'] == 4096 for call in calls)
    assert 'protocol_error' not in json.loads(calls[0]['messages'][-1]['content'])
    assert json.loads(calls[1]['messages'][-1]['content'])['protocol_error']
    assert json.loads(calls[2]['messages'][-1]['content'])['protocol_error']


def test_negative_semantics_never_promoted(tmp_path,projects,facts):
    """An explicit negative check cannot be corrected into a success."""
    from tests.unit.test_model_gateway import gateway
    from tests.unit.test_verifier import prepare
    contract,candidate,execution,verifier,*_ = prepare(tmp_path,projects,facts)
    calls = []
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][-1]['content'])
        calls.append(data)
        text = json.dumps({'classification':'REPRODUCED', 'reason':'a later answer would affirm this',
            'evidence_refs':data['available_refs'] if len(calls)>1 else [{'path':'unknown','content_hash':'bad','start_line':1,'end_line':1}],
            'expected_assertion':True, 'target_triggered':True, 'failure_matches_issue':len(calls)>1})
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':text}}]})
    verifier.gateway = gateway(handler)
    verdict = asyncio.run(verifier.evaluate(contract,candidate,(execution,),facts.context()))
    assert verdict.classification is None and len(calls) == 1


def test_invalid_verdict_citations_stop_after_three_accounted_calls(tmp_path,projects,facts):
    from tests.unit.test_verifier import EvidenceModel, prepare
    model=EvidenceModel(references=False)
    contract,candidate,execution,verifier,*_ = prepare(tmp_path,projects,facts,model=model)
    context=facts.context(); verifier.gateway=BudgetedGateway(model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(execution,),context))
    assert verdict.classification is None and 'MODEL_PROTOCOL_ERROR' in verdict.uncertainties
    assert len(model.calls) == 3 and context.budget.unknown_cost_calls == 3


def test_false_semantic_check_is_not_retried_until_model_agrees(tmp_path,projects,facts):
    from tests.unit.test_verifier import EvidenceModel, prepare
    class FalseCheckModel(EvidenceModel):
        async def complete(self,request,context):
            response=await super().complete(request,context)
            return replace(response,text=json.dumps({**json.loads(response.text),'failure_matches_issue':False}))
    model=FalseCheckModel()
    contract,candidate,execution,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(execution,),facts.context()))
    assert verdict.classification is None and len(model.calls) == 1


def test_false_check_with_bad_citations_cannot_flip_to_success_during_correction(tmp_path,projects,facts):
    from tests.unit.test_verifier import EvidenceModel, prepare
    class FalseBadCitationModel(EvidenceModel):
        async def complete(self,request,context):
            response=await super().complete(request,context)
            if len(self.calls) == 1:
                return replace(response,text=json.dumps({**json.loads(response.text),'failure_matches_issue':False,
                    'evidence_refs':[{'path':'unknown','content_hash':'bad','start_line':1,'end_line':1}]}))
            return response
    model=FalseBadCitationModel()
    contract,candidate,execution,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(execution,),facts.context()))
    assert verdict.classification is None and len(model.calls) == 1


def test_cancel_during_verdict_correction_sends_no_second_request(tmp_path,projects,facts):
    from tests.unit.test_verifier import EvidenceModel, prepare
    class CancelModel(EvidenceModel):
        async def complete(self,request,context):
            response=await super().complete(request,context)
            context.cancel_event.set()
            return replace(response,text=' ')
    model=CancelModel()
    contract,candidate,execution,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    with pytest.raises(BudgetStopped) as error:
        asyncio.run(verifier.evaluate(contract,candidate,(execution,),facts.context()))
    assert error.value.reason == 'CANCELLED' and len(model.calls) == 1
