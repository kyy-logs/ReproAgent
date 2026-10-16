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


def test_capacity_fallback_instruction_cannot_reintroduce_redacted_secret():
    secret='未确认不等于已证明错误'
    item=VerificationIssue('EXECUTION_EVIDENCE_MISSING','execution_evidence_present',
        'program_check','no execution')
    v=Verdict(None,'no execution',candidate_id='candidate-'+('c'*32),
        manifest_hash='h'*64,contract_id='contract-'+('a'*32),run_ids=('run-'+('r'*32),),
        unmet_checks=('execution_evidence_present',),issues=(item,))
    from reproagent.core.verification_feedback import render_verification_feedback
    text=render_verification_feedback(v,result_code='NOT_REPRODUCED',byte_limit=1024,
        instruction='long '*1000,secrets=(secret,))
    data=json.loads(text)
    assert len(text.encode())<=1024
    assert secret not in data['instruction']
    assert '[REDACTED]' in data['instruction']


# ── cited evidence travels with its text ─────────────────────────────────────
# A verdict cites a failure by path and line range.  The exploration phase reads
# the frozen snapshot only, so an execution path such as runs/<id>/probe.jsonl is
# a reference the model can never follow.  The cited lines travel in the feedback
# instead, so the reference is not the only way to know what it says.

def test_cited_evidence_text_travels_with_the_reference_it_belongs_to():
    from reproagent.core.verification_feedback import evidence_excerpts
    body = 'first line\nsecond line\nthird line\n'
    ref = EvidenceRef('runs/run-1/probe.jsonl', 'a'*64, 2, 3)
    excerpts = evidence_excerpts((ref,), read_ref=lambda item: body, max_segment_bytes=4096)
    assert excerpts == ({'path':'runs/run-1/probe.jsonl', 'content_hash':'a'*64,
        'start_line':2, 'end_line':3, 'text':'second line\nthird line'},)


def test_evidence_excerpts_drop_what_cannot_be_read_or_does_not_fit():
    from reproagent.core.verification_feedback import evidence_excerpts
    good = EvidenceRef('runs/run-1/probe.jsonl', 'a'*64, 1, 1)
    missing = EvidenceRef('runs/run-2/probe.jsonl', 'b'*64, 1, 1)
    def read(ref):
        if ref.path == missing.path: raise FileNotFoundError(ref.path)
        return 'x'*5000
    # A segment over the per-segment ceiling is dropped whole, never cut in half.
    assert evidence_excerpts((good,), read_ref=read, max_segment_bytes=4096) == ()
    assert evidence_excerpts((missing,), read_ref=read, max_segment_bytes=4096) == ()
    # The same reference twice is one excerpt.
    short = EvidenceRef('runs/run-1/probe.jsonl', 'a'*64, 1, 1)
    two = evidence_excerpts((short, short), read_ref=lambda ref: 'kept', max_segment_bytes=4096)
    assert len(two) == 1 and two[0]['text'] == 'kept'


def test_feedback_carries_the_evidence_text_beside_its_references():
    from dataclasses import replace
    from reproagent.core.verification_feedback import evidence_excerpts
    v = sample()
    excerpt = {'path':'runs/run-1/probe.jsonl','content_hash':'a'*64,'start_line':1,'end_line':1,
        'text':'IndexError: list index out of range'}
    text = render(v, byte_limit=8192, evidence=(excerpt,))
    data = json.loads(text)
    assert data['verdict']['evidence_text'] == [excerpt]
    assert data['projection']['omitted_evidence'] == 0
    # No evidence means no field at all, so an older shape stays byte-identical.
    assert 'evidence_text' not in json.loads(render(v, byte_limit=8192))['verdict']


@pytest.mark.parametrize('cap',[8192,4096,2048,1024])
def test_evidence_is_shed_whole_and_counted_before_any_diagnostic_is_cut(cap):
    # Evidence is the largest optional payload, so it goes first -- and a segment is
    # either shown complete or not shown, because half a failure report reads as the
    # whole one and nothing in the response would say otherwise.
    excerpts = tuple({'path':'runs/run-%d/probe.jsonl'%i,'content_hash':'a'*64,
        'start_line':1,'end_line':1,'text':'failure detail %d '%i + 'x'*400} for i in range(6))
    text = render(sample(), byte_limit=cap, evidence=excerpts)
    data = json.loads(text)
    assert len(text.encode('utf-8')) <= cap
    kept = data['verdict'].get('evidence_text', [])
    assert all(item['text'].endswith('x'*400) or 'x' not in item['text'] for item in kept)
    assert len(kept) + data['projection']['omitted_evidence'] == len(excerpts)
    assert data['verdict']['issues'][0]['code'] == 'TARGET_TRIGGER_UNCONFIRMED'


def test_secret_redaction_covers_evidence_text_too():
    secret = 'sk-live-0123456789'
    excerpt = {'path':'runs/run-1/probe.jsonl','content_hash':'a'*64,'start_line':1,'end_line':1,
        'text':'failure with %s inside' % secret}
    text = render(sample(), byte_limit=8192, evidence=(excerpt,), secrets=(secret,))
    assert secret not in text and '[REDACTED]' in json.loads(text)['verdict']['evidence_text'][0]['text']


def test_malformed_evidence_cannot_reach_the_model():
    with pytest.raises(ValueError): render(sample(), byte_limit=8192, evidence=('not a mapping',))
    with pytest.raises(ValueError): render(sample(), byte_limit=8192, evidence=({'path':'p'},))
    with pytest.raises(ValueError): render(sample(), byte_limit=8192,
        evidence=({'path':'p','content_hash':'h','start_line':1,'end_line':1,'text':12},))
