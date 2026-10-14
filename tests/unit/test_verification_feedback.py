import json
from copy import deepcopy
import pytest
from reproagent.core.models import Verdict, VerificationIssue, EvidenceRef
from reproagent.core.budget import BudgetStopped


def sample(long=False):
    item=VerificationIssue('TARGET_TRIGGER_UNCONFIRMED','target_triggered','provider_claim',
        '需检查目标触发'+('详细内容'*4000 if long else ''),
        tuple(EvidenceRef('failure.log','a'*64,i,i) for i in range(1,90)) if long else (),
        recovery='inspect_evidence')
    return Verdict(None,'尚未确认'+('多轮说明'*4000 if long else ''),
        unmet_checks=('target_triggered',), candidate_id='candidate-1',manifest_hash='manifest-1',
        contract_id='contract-1',contract_version=2,run_ids=('run-1',),issues=(item,item,item) if long else (item,))


def render(verdict, **kwargs):
    from reproagent.core.verification_feedback import render_verification_feedback
    return render_verification_feedback(verdict,result_code='NOT_REPRODUCED',**kwargs)


def test_full_feedback_keeps_original_verdict_binding_and_claim_origin():
    v=sample()
    text=render(v,byte_limit=8192)
    data=json.loads(text)
    assert data['result_code']=='NOT_REPRODUCED' and data['decision']=='continue'
    assert data['verdict']['candidate_id']=='candidate-1'
    assert data['verdict']['issues'][0]['origin']=='provider_claim'
    assert data['verdict']['unmet_checks']==['target_triggered']
    assert data['instruction'] and data['projection']['omitted_issues']==0


@pytest.mark.parametrize('cap',[8192,4096,2048,1024])
def test_chinese_large_details_shrink_explicitly_without_mutating_full_verdict(cap):
    v=sample(True); before=deepcopy(v)
    text=render(v,byte_limit=cap)
    data=json.loads(text)
    assert len(text.encode('utf-8'))<=cap
    assert data['verdict']['issues'][0]['code']=='TARGET_TRIGGER_UNCONFIRMED'
    assert data['verdict']['unmet_checks']==['target_triggered']
    assert data['verdict']['run_ids']==['run-1']
    assert data['projection']['omitted_details']>0
    assert v==before


def test_secret_redaction_precedes_encoding_and_capacity_measurement():
    from dataclasses import replace
    v=sample(); secret='x"\n\\key'; v=replace(v,reason=secret*2000)
    text=render(v,byte_limit=2048,secrets=(secret,))
    assert secret not in text and len(text.encode())<=2048
    assert json.loads(text)['projection']['omitted_details']>0


@pytest.mark.parametrize('cap',[0,1,64,128])
def test_tiny_capacity_refuses_instead_of_emitting_broken_json(cap):
    with pytest.raises(BudgetStopped) as caught: render(sample(),byte_limit=cap)
    assert caught.value.reason=='NEEDS_INFORMATION'


def test_original_execution_and_replay_extras_are_preserved():
    text=render(sample(),byte_limit=8192,extras={'consecutive_command_timeouts':1,
        'configured_command_timeout_seconds':60,'previous_candidate_id':'older','previous_run_ids':['older-run']})
    data=json.loads(text)
    assert data['consecutive_command_timeouts']==1 and data['previous_run_ids']==['older-run']


def test_unknown_decision_and_extras_cannot_grant_new_actions():
    with pytest.raises(ValueError): render(sample(),byte_limit=8192,decision='force_done')
    with pytest.raises(ValueError): render(sample(),byte_limit=8192,extras={'permission':'allow'})


def test_duplicate_context_block_feedback_fits_small_capacity_without_repeating_identity():
    v=Verdict(None,'semantic evidence exceeds bounded context; narrow candidate',
        candidate_id='candidate-'+('c'*32),manifest_hash='h'*64,contract_id='contract-'+('a'*32),
        run_ids=('run-'+('r'*32),),unmet_checks=('review_context_fits',),next_action='narrow_candidate',
        issues=(VerificationIssue('REVIEW_CONTEXT_TOO_LARGE','review_context_fits','program_check',
            'semantic evidence exceeds bounded context; narrow candidate',
            measurements={'needed_bytes':5000,'budget_bytes':1024},recovery='narrow_candidate'),))
    from reproagent.core.verification_feedback import render_verification_feedback
    text=render_verification_feedback(v,result_code='DUPLICATE_CANDIDATE',byte_limit=1024,
        instruction='The same test and execution settings still exceed the review context. Narrow the test or request information; changing the hypothesis alone does not change evidence.',
        extras={'previous_candidate_id':v.candidate_id,'previous_run_ids':list(v.run_ids),
            'prior_result_code':'REVIEW_CONTEXT_TOO_LARGE'})
    data=json.loads(text)
    assert len(text.encode())<=1024
    assert data['verdict']['issues'][0]['code']=='REVIEW_CONTEXT_TOO_LARGE'
    assert data['verdict']['run_ids']==list(v.run_ids)
    assert data['verdict']['issues'][0]['measurements']['needed_bytes']==5000
    assert data['projection']['omitted_details']>0
