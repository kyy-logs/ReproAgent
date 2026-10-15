import json

from reproagent.core.models import CandidateClass, Verdict, VerificationIssue
from reproagent.core.verification_feedback import render_verification_feedback


def passing_verdict():
    return Verdict(CandidateClass.NOT_REPRODUCED, 'candidate passes on original snapshot',
        candidate_id='candidate-'+'c'*32, manifest_hash='h'*64, contract_id='contract-'+'a'*32,
        run_ids=('run-'+'r'*32,), unmet_checks=('candidate_reproduced',),
        issues=(VerificationIssue('CANDIDATE_NOT_REPRODUCED', 'candidate_reproduced', 'program_check',
            'candidate passes on original snapshot', recovery='revise_candidate'),))


def test_original_pass_low_capacity_fallback_still_advises_checking_input_contamination():
    text = render_verification_feedback(passing_verdict(), result_code='NOT_REPRODUCED',
        byte_limit=1024, instruction='long '*1000, secrets=('回显输入',))
    data = json.loads(text)
    assert len(text.encode('utf-8')) <= 1024
    assert data['verdict']['issues'][0]['code'] == 'CANDIDATE_NOT_REPRODUCED'
    assert '原版' in data['instruction'] and '名称' in data['instruction']
    assert '回显输入' not in data['instruction'] and '[REDACTED]' in data['instruction']


def test_explicit_caller_instruction_has_priority_over_original_pass_default():
    text = render_verification_feedback(passing_verdict(), result_code='NOT_REPRODUCED',
        byte_limit=8192, instruction='检查已经绑定的实际输出。')
    assert json.loads(text)['instruction'] == '检查已经绑定的实际输出。'


def test_framework_error_does_not_claim_original_passed_or_diagnose_contamination():
    verdict = Verdict(CandidateClass.INVALID_CANDIDATE, 'fixture error',
        issues=(VerificationIssue('FRAMEWORK_CHECK_FAILED','no_non_origin_framework_errors',
            'program_check','fixture error',recovery='revise_candidate'),))
    data = json.loads(render_verification_feedback(verdict,result_code='INVALID_CANDIDATE',byte_limit=8192))
    assert '原版' not in data['instruction'] and '回显输入' not in data['instruction']
