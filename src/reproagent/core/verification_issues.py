"""Closed diagnostic vocabulary. It never grants permission or changes acceptance."""
import math

RECOVERIES = frozenset({'revise_candidate', 'revise_contract', 'narrow_candidate',
                       'request_information', 'inspect_evidence'})
# code -> (allowed check identifiers, claim origin, recommended recovery)
ISSUE_SPECS = {
    'EXECUTION_EVIDENCE_MISSING': (('execution_evidence_present',), 'program_check', 'inspect_evidence'),
    'RUN_BINDING_MISMATCH': (('binding_and_integrity_ok','run_binding'), 'program_check','inspect_evidence'),
    'CANDIDATE_HASH_MISMATCH': (('binding_and_integrity_ok','candidate_manifest_hash','candidate_file_hash'), 'program_check','inspect_evidence'),
    'SOURCE_PROTECTION_FAILED': (('binding_and_integrity_ok','source_protection'), 'program_check','inspect_evidence'),
    'CLEANUP_FAILED': (('binding_and_integrity_ok','cleanup_ok'), 'program_check','inspect_evidence'),
    'COMMAND_TIMEOUT': (('binding_and_integrity_ok','execution_exited'), 'program_check','narrow_candidate'),
    'LOG_LIMIT': (('binding_and_integrity_ok','logs_complete','execution_exited'), 'program_check','narrow_candidate'),
    'PROBE_INCOMPLETE': (('binding_and_integrity_ok','probe_complete'), 'program_check','inspect_evidence'),
    'EXECUTION_NOT_EXITED': (('binding_and_integrity_ok','execution_exited'), 'program_check','inspect_evidence'),
    'CANCELLED': (('binding_and_integrity_ok','execution_exited'), 'program_check','inspect_evidence'),
    'EXHAUSTED': (('binding_and_integrity_ok','execution_exited'), 'program_check','inspect_evidence'),
    'FRAMEWORK_CHECK_FAILED': (('no_non_origin_framework_errors',), 'program_check','revise_candidate'),
    'CANDIDATE_IMPORT_BLOCKED': (('framework_not_blocked',), 'program_check','inspect_evidence'),
    'ENVIRONMENT_BLOCKED': (('framework_not_blocked','provider_semantic_claims'), None,'request_information'),
    'TEST_EXECUTION_INVALID': (('tests_executed_and_valid',), 'program_check','inspect_evidence'),
    'CANDIDATE_NOT_REPRODUCED': (('candidate_reproduced',), 'program_check','revise_candidate'),
    'EXPECTATION_UNGROUNDED': (('expectation_is_grounded',), 'program_check','revise_contract'),
    'REVIEW_CONTEXT_TOO_LARGE': (('review_context_fits',), 'program_check','narrow_candidate'),
    'EVIDENCE_UNAVAILABLE': (('evidence_sources_available',), 'program_check','inspect_evidence'),
    'EXPECTED_ASSERTION_UNCONFIRMED': (('expected_assertion',), 'provider_claim','inspect_evidence'),
    'TARGET_TRIGGER_UNCONFIRMED': (('target_triggered',), 'provider_claim','inspect_evidence'),
    'ISSUE_FAILURE_MATCH_UNCONFIRMED': (('failure_matches_issue',), 'provider_claim','inspect_evidence'),
    'SEMANTIC_REPRODUCTION_UNCONFIRMED': (('provider_semantic_claims',), 'provider_claim','inspect_evidence'),
    'SEMANTIC_CITATIONS_INCOMPLETE': (('citations_cover_expectation_and_failure',), 'program_check','inspect_evidence'),
    'MODEL_PROTOCOL_ERROR': (('semantic_response_valid',), 'program_check','inspect_evidence'),
}
_RUNTIME = frozenset({'RUN_BINDING_MISMATCH','CANDIDATE_HASH_MISMATCH','SOURCE_PROTECTION_FAILED',
    'CLEANUP_FAILED','COMMAND_TIMEOUT','LOG_LIMIT','PROBE_INCOMPLETE','EXECUTION_NOT_EXITED','CANCELLED','EXHAUSTED'})
_ALLOWED = {code: frozenset() for code in ISSUE_SPECS}
for code in _RUNTIME:
    _ALLOWED[code] = frozenset({'stop_reason','duration_seconds','probe_complete','protection_checked','last_test_stage'})
for code in ('FRAMEWORK_CHECK_FAILED','ENVIRONMENT_BLOCKED','TEST_EXECUTION_INVALID','CANDIDATE_IMPORT_BLOCKED'):
    _ALLOWED[code] = frozenset({'framework_errors','collected','executed'})
_ALLOWED['REVIEW_CONTEXT_TOO_LARGE'] = frozenset({'needed_bytes','budget_bytes','stage'})
_ALLOWED['MODEL_PROTOCOL_ERROR'] = frozenset({'attempts','needed_bytes','budget_bytes','stage','protocol_error'})


def validate_issue(code, check, origin, recovery, message, measurements):
    if type(code) is not str or code not in ISSUE_SPECS:
        raise ValueError('unknown verification diagnostic code')
    checks, expected_origin, _ = ISSUE_SPECS[code]
    if check not in checks or origin not in ('program_check','provider_claim'):
        raise ValueError('invalid diagnostic check or origin')
    if expected_origin is not None and origin != expected_origin:
        raise ValueError('diagnostic origin mismatch')
    if code == 'ENVIRONMENT_BLOCKED' and origin != ('program_check' if check == 'framework_not_blocked' else 'provider_claim'):
        raise ValueError('environment diagnostic origin mismatch')
    if type(recovery) is not str or recovery not in RECOVERIES or type(message) is not str:
        raise ValueError('invalid diagnostic recovery or message')
    if not isinstance(measurements, dict) or not set(measurements) <= _ALLOWED[code]:
        raise ValueError('invalid diagnostic measurements')
    for key, value in measurements.items():
        if key in ('probe_complete','protection_checked','collected','executed'):
            ok = type(value) is bool
        elif key in ('needed_bytes','budget_bytes','attempts'):
            ok = type(value) is int and value >= 0
        elif key == 'duration_seconds':
            ok = type(value) in (int,float) and math.isfinite(value) and value >= 0
        elif key == 'framework_errors':
            ok = isinstance(value, (list,tuple)) and len(value) <= 16 and all(type(v) is str for v in value)
        else:
            ok = type(value) is str and len(value) <= 512
        if not ok:
            raise ValueError('invalid diagnostic measurement type')
