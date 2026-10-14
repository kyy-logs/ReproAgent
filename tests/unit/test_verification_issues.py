import pytest
from dataclasses import replace
from reproagent.core import models
from reproagent.core.serialization import encode_record, decode_record


def issue(**overrides):
    assert hasattr(models, 'VerificationIssue'), 'typed verification issues are missing'
    values = dict(code='COMMAND_TIMEOUT', check='binding_and_integrity_ok', origin='program_check',
        message='执行超时', measurements={'duration_seconds':60.01, 'probe_complete':False}, recovery='narrow_candidate')
    return models.VerificationIssue(**{**values, **overrides})


def test_typed_issue_roundtrip_and_old_record_default():
    item = issue()
    verdict = models.Verdict(None, 'timeout', issues=(item,), run_ids=('run-1',))
    assert decode_record('verdicts', encode_record(verdict)) == verdict
    old = encode_record(models.Verdict(models.CandidateClass.NOT_REPRODUCED, 'passes'))
    old.pop('issues', None)
    assert decode_record('verdicts', old).issues == ()
    assert replace(verdict, reason='changed').issues == (item,)


@pytest.mark.parametrize('overrides', [
    {'code':'MADE_UP'}, {'check':'invented'}, {'origin':'provider_claim'},
    {'recovery':'grant_permission'}, {'measurements':{'made_up':1}},
    {'measurements':{'duration_seconds':True}}, {'measurements':{'duration_seconds':float('inf')}},
    {'measurements':{'duration_seconds':-1}}, {'message':42}, {'evidence_refs':('invented',)},
])
def test_unknown_or_malformed_diagnostics_are_rejected(overrides):
    with pytest.raises((ValueError, TypeError)):
        issue(**overrides)


def test_semantic_issue_stays_a_claim_and_preserves_current_schema():
    item = issue(code='TARGET_TRIGGER_UNCONFIRMED', check='target_triggered', origin='provider_claim',
        message='未确认', measurements={}, recovery='inspect_evidence')
    assert item.origin == 'provider_claim'
    from reproagent.core.protocol import verdict_schema
    assert set(verdict_schema()['required']) == {'classification','reason','evidence_refs',
        'expected_assertion','target_triggered','failure_matches_issue','assertion_reviews'}
