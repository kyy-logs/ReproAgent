"""Program acceptance of explicit basis claims over real original execution.

All responses are scripted; this does not establish real-model classification quality.
"""
import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateClass
from tests.unit.test_verifier import EvidenceModel, prepare


class BasisModel(EvidenceModel):
    def __init__(self, basis, supported=True, invalid_first=False, missing=False, bad_citation=False):
        super().__init__()
        self.basis, self.supported = basis, supported
        self.invalid_first, self.missing, self.bad_citation = invalid_first, missing, bad_citation

    async def complete(self, request, context):
        response = await super().complete(request, context)
        value = json.loads(response.text)
        for row in value['assertion_reviews']:
            row['basis'] = 'current_requirement'
        first = value['assertion_reviews'][0]
        if self.invalid_first and len(self.calls) > 1:
            # A correction must not be allowed to erase the first negative claim.
            return replace(response, text=json.dumps(value))
        first['basis'] = self.basis
        first['supported'] = self.supported
        if self.missing:
            first.pop('basis')
        if self.invalid_first:
            value['unexpected'] = True
        if self.bad_citation:
            first['evidence_refs'] = [dict(first['evidence_refs'][0], path='unavailable.txt')]
        return replace(response, text=json.dumps(value))


@pytest.mark.parametrize('basis,expected', [
    ('current_requirement', CandidateClass.REPRODUCED),
    ('unaffected_contract', CandidateClass.REPRODUCED),
    ('target_old_behavior', CandidateClass.INVALID_CANDIDATE),
    ('unknown', CandidateClass.INVALID_CANDIDATE),
])
def test_old_or_unknown_basis_blocks_even_a_global_and_per_assertion_yes(
        tmp_path, projects, facts, basis, expected):
    model = BasisModel(basis)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == expected
    assert len(model.calls) == 1
    if expected == CandidateClass.INVALID_CANDIDATE:
        assert verdict.issues[0].code == 'CANDIDATE_ASSERTIONS_UNCONFIRMED'
        assert verdict.issues[0].origin == 'provider_claim'
        assert 'basis=' + basis in verdict.reason
        assert 'tests/test_repro.py:2' in verdict.reason
        assert verdict.issues[0].measurements['assertion_ids'] == ['A1']
    payload = json.loads(model.calls[0].messages[-1]['content'])
    schema = payload['response_schema']['properties']['assertion_reviews']['items']
    assert 'basis' in schema['required']


@pytest.mark.parametrize('basis', ['current_requirement', 'unaffected_contract'])
def test_an_allowed_basis_cannot_override_explicit_unsupported(
        tmp_path, projects, facts, basis):
    model = BasisModel(basis, supported=False)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == CandidateClass.INVALID_CANDIDATE and len(model.calls) == 1


@pytest.mark.parametrize('basis', ['target_old_behavior', 'unknown'])
@pytest.mark.parametrize('problem', ['invalid_shape', 'bad_citation'])
def test_negative_basis_survives_invalid_reply_without_a_correction_to_yes(
        tmp_path, projects, facts, basis, problem):
    model = BasisModel(basis, invalid_first=problem == 'invalid_shape',
                       bad_citation=problem == 'bad_citation')
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification is None and len(model.calls) == 1
    assert verdict.issues[0].code == 'MODEL_PROTOCOL_ERROR'
    assert verdict.issues[0].measurements['protocol_error'] == 'invalid_negative_response'


def test_old_reply_without_basis_never_gets_a_default_approval(tmp_path, projects, facts):
    model = BasisModel('current_requirement', missing=True)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification is None and len(model.calls) == 3
    assert 'basis' in verdict.reason and 'MODEL_PROTOCOL_ERROR' in verdict.uncertainties
