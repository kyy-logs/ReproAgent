"""Scripted semantic judgments verify wire policy and the existing negative gate.

They do not prove real-model recognition of the B-3 contradiction.
"""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from reproagent.core.models import CandidateClass
from tests.assertion_support import approved_assertions
from tests.unit.test_model_gateway import gateway
from tests.unit.test_verifier import prepare


OLD = 'Operation refused: use legacy mode'
TEXT = f'''from example.parser import parse
def test_diagnostic():
    diagnostic = parse([])
    assert {OLD!r} in diagnostic
    assert "recovery_option" in diagnostic
'''


@pytest.mark.parametrize('explicit_requirement', [False, True])
def test_observed_wording_does_not_justify_an_assertion_but_explicit_requirement_can(
        tmp_path, projects, facts, explicit_requirement):
    class DiagnosticProject:
        def plain(self, root):
            repo = projects.plain(root)
            (repo / 'example/parser.py').write_text(
                f'def parse(values):\n    return {OLD!r}\n', encoding='utf-8')
            (repo / 'issue.md').write_text(
                f'The reported old diagnostic is {OLD!r}. '
                'The diagnostic must explain recovery_option. '
                + ('The exact old sentence must also remain for compatibility.' if explicit_requirement
                   else 'The old wording need not remain.'), encoding='utf-8')
            return repo

    calls = []
    def handle(request):
        wire = json.loads(request.content)
        calls.append(wire)
        data = json.loads(wire['messages'][-1]['content'])
        assert data['candidate'][0]['content'] == TEXT
        assert len(data['assertion_sites']) == 2
        row_schema = data['response_schema']['properties']['assertion_reviews']['items']
        assert 'basis' in row_schema['required']
        assert 'original output must retain' in data['contract']['observable_checks'][0]
        rows = approved_assertions(data)
        if not explicit_requirement:
            # Even contradictory supported=true cannot override this negative
            # classification on the actual SDK/provider boundary.
            rows[0].update(basis='target_old_behavior',
                reason='旧现象和契约中的矛盾条目不能证明必须保留旧句子')
        answer = {'classification': 'REPRODUCED', 'reason': '诊断没有恢复选项',
            'evidence_refs': data['available_refs'], 'expected_assertion': True,
            'target_triggered': True, 'failure_matches_issue': True, 'assertion_reviews': rows}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(answer)}}]})

    model = gateway(handle)
    contract, candidate, execution, verifier, *_ = prepare(
        tmp_path, DiagnosticProject(), facts, text=TEXT, model=model)
    contract = replace(contract, expected='diagnostic explains recovery_option', reported_actual=OLD,
        observable_checks=(f'original output must retain {OLD!r}', 'diagnostic mentions recovery_option'),
        assumptions=(() if explicit_requirement else ('Exact old wording is not required.',)))
    assert execution.raw.exit_code == 1 and execution.observation.probe_complete

    async def run():
        try:
            return await verifier.evaluate(contract, candidate, (execution,), facts.context())
        finally:
            await model.aclose()

    verdict = asyncio.run(run())
    assert len(calls) == 1, 'a negative audit must not be retried until it turns positive'
    assert verdict.classification == (CandidateClass.REPRODUCED if explicit_requirement
                                      else CandidateClass.INVALID_CANDIDATE)
    if not explicit_requirement:
        assert verdict.issues[0].code == 'CANDIDATE_ASSERTIONS_UNCONFIRMED'
        assert verdict.issues[0].origin == 'provider_claim'
        assert 'tests/test_repro.py:4' in verdict.reason
    system = calls[0]['messages'][0]['content']
    assert 'observed old behavior' in system
    assert 'contradictory contract' in system
    assert 'partly supported' in system
    assert 'explicitly requires' in system
