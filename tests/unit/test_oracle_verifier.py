import asyncio
import json
from reproagent.core.models import ModelResponse, CandidateClass
from tests.unit.test_verifier import prepare
from tests.assertion_support import approved_assertions

RISKY=('from example.parser import parse\nfrom _pytest.pytester import LineMatcher\n'
       'def test_target():\n    parse([])\n'
       '    LineMatcher([]).fnmatch_lines(["*Old sentence*new_option=True*"])\n')


class AuditModel:
    def __init__(self,fault=None): self.calls=[];self.fault=fault
    async def complete(self,request,context):
        data=json.loads(request.messages[-1]['content']);self.calls.append(data)
        source=data['expectation_sources'][0]
        ref={k:source[k] for k in ('path','content_hash','start_line','end_line')}
        rows=[{'check_id':item['id'],'role':'regression','supported':True,
            'basis':'desired_behavior','reason':'scripted review','evidence_refs':[ref],
            'source_quote':source['text'][:200]} for item in data['oracle_checks']]
        if self.fault=='old_fragment':
            item=next(item for item in data['oracle_checks'] if item['kind']=='pattern_fragment' and 'Old sentence' in item['details']['text'])
            row=next(row for row in rows if row['check_id']==item['id'])
            row.update(supported=False,basis='reported_actual',reason='historical wording is not required')
        if self.fault=='forged_quote': rows[0]['source_quote']='NOT IN THE SUPPLIED SOURCE'
        if self.fault=='failure_ref': rows[0]['evidence_refs']=[next(ref for ref in data['available_refs'] if 'probe' in ref['path'])]
        if self.fault=='missing': rows.pop()
        if self.fault=='negative_and_malformed': rows[0].update(supported=False,unsupported_field=True)
        if self.fault=='control': rows[0].update(role='execution_control',basis='existing_semantics')
        answer={'classification':'REPRODUCED','reason':'scripted review',
            'evidence_refs':data['available_refs'],'expected_assertion':True,'target_triggered':True,
            'failure_matches_issue':True,'assertion_reviews':approved_assertions(data),'oracle_reviews':rows}
        return ModelResponse(json.dumps(answer))


def test_one_unsupported_matcher_fragment_blocks_whole_candidate(tmp_path,projects,facts):
    model=AuditModel('old_fragment')
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,text=RISKY,model=model)
    result=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert result.classification==CandidateClass.INVALID_CANDIDATE
    assert result.issues[0].code=='CANDIDATE_ORACLES_UNCONFIRMED'
    assert result.issues[0].measurements['oracle_ids']
    assert len(model.calls)==1


def test_controls_can_have_existing_semantic_basis(tmp_path,projects,facts):
    model=AuditModel('control')
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,text=RISKY,model=model)
    result=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert result.classification==CandidateClass.REPRODUCED
    assert len(model.calls)==1


import pytest
@pytest.mark.parametrize('fault',['forged_quote','failure_ref','missing','negative_and_malformed'])
def test_invalid_or_negative_oracle_review_cannot_promote_to_success(tmp_path,projects,facts,fault):
    model=AuditModel(fault)
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,text=RISKY,model=model)
    result=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert result.classification is None
    assert result.issues[0].code=='MODEL_PROTOCOL_ERROR'
    if fault=='negative_and_malformed': assert len(model.calls)==1


def test_passing_candidate_reports_supplied_text_overlap_without_model_call(tmp_path,projects,facts):
    embedded = 'def fx():\n    """module scoped fixture"""\n'
    text = 'from example.parser import parse\ndef test_target():\n    generated = ' + repr(embedded) + '\n    parse([1])\n    assert \"module\" in generated\n'
    model=AuditModel()
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,text=text,model=model)
    result=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert result.classification==CandidateClass.NOT_REPRODUCED
    assert not model.calls
    assert any('candidate_text_overlap' in line for line in result.issues[0].measurements['audit_notes'])


def test_malformed_negative_review_retains_actionable_unvalidated_objections(tmp_path, projects, facts):
    model = AuditModel('negative_and_malformed')
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts, text=RISKY, model=model)
    result = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context()))
    assert result.classification is None and len(model.calls) == 1
    issue = result.issues[0]
    assert issue.code == 'MODEL_PROTOCOL_ERROR' and issue.origin == 'program_check'
    assert issue.measurements['protocol_error'] == 'invalid_negative_response'
    assert issue.measurements['validation_error']
    assert any('scripted review' in note and 'O1' in note for note in issue.measurements['negative_checks'])
    assert issue.measurements['provider_reason'] == 'scripted review'


def test_unvalidated_negative_diagnostics_are_bounded_and_redacted(tmp_path, projects, facts):
    from dataclasses import asdict
    model = AuditModel('negative_and_malformed')
    original = model.complete
    secret = 'diagnostic-secret'

    async def sensitive(request, context):
        response = await original(request, context)
        value = json.loads(response.text)
        value['reason'] = secret + 'x' * 2000
        value['oracle_reviews'][0]['reason'] = secret + 'y' * 2000
        return ModelResponse(json.dumps(value))

    model.complete = sensitive
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts, text=RISKY, model=model)
    verifier.secrets = (secret,)
    result = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context()))
    assert result.classification is None and len(model.calls) == 1
    assert secret not in json.dumps(asdict(result))
    measurements = result.issues[0].measurements
    assert len(measurements['provider_reason']) <= 512
    assert '[REDACTED]' in measurements['provider_reason']
    assert all(len(note) <= 512 for note in measurements['negative_checks'])
