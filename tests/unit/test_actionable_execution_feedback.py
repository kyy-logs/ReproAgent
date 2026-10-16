import asyncio
import json
from dataclasses import asdict, replace

import pytest

from reproagent.core.models import VerificationIssue
from reproagent.core.review_context import ReviewContextTooLarge, build_review_context
from tests.unit.test_verifier import prepare
from tests.unit.test_review_context import candidate_reader
from tests.unit.test_verification_feedback import render, sample


LARGE_ASSERT = ('from example.parser import parse\ndef test_empty():\n'
    '    assert parse([0]) == [], "HTML-" + "x" * 18000\n')


def test_overflow_reports_dominant_fields_and_failed_test_index(tmp_path,projects,facts):
    contract,candidate,run,verifier,model,*_ = prepare(tmp_path,projects,facts,LARGE_ASSERT)
    before=asdict(run)
    with pytest.raises(ReviewContextTooLarge) as caught:
        build_review_context(contract,candidate,run,read_ref=verifier.resolve,
            read_file=candidate_reader(candidate),max_bytes=32768)
    error=caught.value
    assert error.material_sizes[0]['field']=='failure_evidence'
    assert error.material_sizes[0]['bytes']>32768
    assert len(error.material_sizes)<=4
    row=error.failure_sizes[0]
    assert row['test_index']>=0 and row['message_bytes']>18000
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    issue=verdict.issues[0]
    assert issue.code=='REVIEW_CONTEXT_TOO_LARGE' and verdict.classification is None
    assert issue.measurements['material_sizes']==error.material_sizes
    assert issue.measurements['failure_sizes']==error.failure_sizes
    assert asdict(run)==before and not model.calls


@pytest.mark.parametrize('field,rows', [('material_sizes',[{'field':'candidate','bytes':True}]),
    ('failure_sizes',[{'test_index':0,'message_bytes':-1,'longrepr_bytes':0,'comparison_bytes':0}])])
def test_overflow_size_measurements_reject_invalid_rows(field,rows):
    with pytest.raises(ValueError):
        VerificationIssue('REVIEW_CONTEXT_TOO_LARGE','review_context_fits','program_check','too large',
            measurements={field:rows},recovery='narrow_candidate')


def observation():
    return {'origin':'program_observation','run_id':'run-1','failed_calls':[
        {'test_index':0,'nodeid':'tests/test_repro.py::test_empty','exception_type':'builtins.TypeError',
         'message':"parse() got an unexpected keyword argument 'builddir'",'path':'tests/test_repro.py','lineno':3}],
        'omitted_failed_calls':0}


def test_call_facts_are_separate_from_semantic_claims_and_keep_full_message():
    data=json.loads(render(sample(),byte_limit=8192,extras={'execution_observation':observation()}))
    assert data['execution_observation']==observation()
    assert data['verdict']['issues'][0]['origin']=='provider_claim'
    assert 'API' in data['instruction'] and 'Read' in data['instruction']


def test_call_observation_can_be_omitted_explicitly_at_small_capacity():
    fact=observation();fact['failed_calls']*=4
    for row in fact['failed_calls']:row['message']='long failure '*35
    data=json.loads(render(sample(),byte_limit=1024,extras={'execution_observation':fact}))
    assert data['verdict']['issues'][0]['code']=='TARGET_TRIGGER_UNCONFIRMED'
    assert 'execution_observation'not in data and data['projection']['omitted_details']>0


def test_call_observations_cannot_claim_provider_origin():
    fact=observation();fact['origin']='provider_claim'
    with pytest.raises(ValueError):render(sample(),byte_limit=8192,extras={'execution_observation':fact})



def test_failed_call_projection_prioritizes_overflow_index_without_relabeling():
    from types import SimpleNamespace
    from reproagent.core.setup_diagnostics import call_failure_details
    run=SimpleNamespace(run_id='run-one',observation=SimpleNamespace(framework_details={},tests=tuple(
        {'when':'call','outcome':'failed','nodeid':f'test_{i}','exception_type':'TypeError',
         'crash':{'message':'actual failure','path':'tests/test_repro.py','lineno':i+1}} for i in range(7))))
    details=call_failure_details(run,task_root='/task',prioritized_indices=(6,))
    assert details['failed_calls'][0]['test_index']==6
    assert details['omitted_failed_calls']==3


def test_feedback_keeps_one_call_when_four_do_not_fit():
    fact=observation()
    fact['failed_calls']=[{**fact['failed_calls'][0],'test_index':i,'message':'失败'*250} for i in range(4)]
    data=json.loads(render(sample(),byte_limit=4096,extras={'execution_observation':fact}))
    assert len(data['execution_observation']['failed_calls'])<4
    assert data['execution_observation']['omitted_failed_calls']>0


def test_program_exception_facts_require_matching_record_binding(tmp_path,projects,facts):
    from reproagent.core.execution_feedback import execution_observation
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert execution_observation(verdict,verifier.store)['run_id']==run.run_id
    assert execution_observation(replace(verdict,candidate_id='wrong'),verifier.store) is None
    assert execution_observation(replace(verdict,contract_version=99),verifier.store) is None
    assert execution_observation(replace(verdict,run_ids=('missing-run',)),verifier.store) is None
