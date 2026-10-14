"""A genuine first failure must not excuse unsupported later expectations."""
import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateClass
from tests.unit.test_verifier import EvidenceModel, prepare


def checks(data):
    sources = [{key: source[key] for key in ('path', 'content_hash', 'start_line', 'end_line')}
               for source in data['expectation_sources']]
    return [{'assertion_id': site['id'], 'supported': True,
             'reason': 'The source states this expectation.', 'evidence_refs': sources}
            for site in data.get('assertion_sites', [])]


def test_inventory_includes_unreached_asserts_and_pytest_output_matchers():
    from reproagent.core.assertion_audit import assertion_sites
    content = ('import pytest\n'
        'def test_report(result, tree):\n'
        '    assert "heading" in result\n'
        '    assert tree.children[0].tagname == "section"\n'
        '    result.stdout.fnmatch_lines(["*old wording*required_option*"])\n'
        '    with pytest.raises(ValueError):\n'
        '        result()\n'
        '    result.assert_outcomes(passed=1)\n'
        '    text = "assert False"\n')
    entries = [{'path': 'tests/test_repro.py', 'role': 'test', 'content': content}]
    sites = assertion_sites(entries)
    assert [site['start_line'] for site in sites] == [3, 4, 5, 6, 8]
    assert len({site['id'] for site in sites}) == 5
    assert sites == assertion_sites(entries)
    assert all(site['path'] == entries[0]['path'] for site in sites)


def test_invalid_python_fixture_is_data_for_the_test_not_an_inventory_error():
    from reproagent.core.assertion_audit import assertion_sites
    sites = assertion_sites([
        {'path': 'tests/test_repro.py', 'role': 'test', 'content': 'def test_case(): assert True\n'},
        {'path': 'tests/bad_syntax.py', 'role': 'data', 'content': 'def broken(:\n'},
    ])
    assert len(sites) == 1 and sites[0]['path'] == 'tests/test_repro.py'


class AuditModel(EvidenceModel):
    def __init__(self, mutate):
        super().__init__()
        self.mutate = mutate

    async def complete(self, request, context):
        response = await super().complete(request, context)
        data = json.loads(request.messages[-1]['content'])
        answer = json.loads(response.text)
        answer['assertion_reviews'] = checks(data)
        self.mutate(answer, data)
        return replace(response, text=json.dumps(answer))


def test_old_global_approval_without_assertion_review_cannot_pass(tmp_path, projects, facts):
    class OldApproval(EvidenceModel):
        async def complete(self, request, context):
            response = await super().complete(request, context)
            answer = json.loads(response.text)
            answer.pop('assertion_reviews', None)
            return replace(response, text=json.dumps(answer))
    model = OldApproval()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification is None
    assert len(model.calls) == 3
    assert 'assertion_reviews' in verdict.reason


def test_unsupported_unreached_assertion_blocks_primary_failure(tmp_path, projects, facts):
    def unsupported(answer, data):
        if answer['assertion_reviews']:
            answer['assertion_reviews'][-1].update(supported=False,
                reason='The source does not require a particular first node.', evidence_refs=[])
    model = AuditModel(unsupported)
    text = ('from example.parser import parse\n'
        'def test_empty():\n'
        '    assert parse([]) == []\n'
        '    assert "first node" == "section"\n')
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, text, model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == CandidateClass.INVALID_CANDIDATE
    assert 'tests/test_repro.py:4' in verdict.reason
    assert 'first node' in verdict.reason
    assert len(model.calls) == 1


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'unknown', 'failure_only'])
def test_assertion_audit_coverage_and_source_citations_are_checked(tmp_path, projects, facts, problem):
    def mutate(answer, data):
        rows = answer['assertion_reviews']
        if problem == 'missing':
            answer['assertion_reviews'] = []
        elif problem == 'duplicate':
            answer['assertion_reviews'] = rows + rows
        elif problem == 'unknown' and rows:
            rows[0]['assertion_id'] = 'not-in-candidate'
        elif problem == 'failure_only' and rows:
            rows[0]['evidence_refs'] = [data['available_refs'][-1]]
    model = AuditModel(mutate)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification is None
    assert len(model.calls) == 3


def test_bad_negative_audit_is_not_retried_until_model_agrees(tmp_path, projects, facts):
    def mutate(answer, data):
        if answer['assertion_reviews']:
            answer['assertion_reviews'][0]['supported'] = False
        answer['evidence_refs'] = []  # Invalid protocol, but still an explicit negative.
    model = AuditModel(mutate)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification is None
    assert len(model.calls) == 1
