"""Revision analysis must see the previous interpretation, not treat it as evidence."""
import asyncio
import json
from dataclasses import asdict

import httpx
import pytest

from reproagent.core.agent import ReproAgent
from reproagent.core.models import EvidenceContext, IssueContract, IssueDescription
from tests.unit.test_model_gateway import gateway


OLD = 'Using pytest.skip outside of a test is not allowed'


@pytest.mark.parametrize('correct_format', [True, False])
def test_revision_sends_previous_constraints_on_every_sdk_attempt_without_citing_them(facts, correct_format):
    from reproagent.core.models import SourceRef
    issue = SourceRef('input/issue.md', 'issue-hash', 1, 2, 'issue')
    observed = SourceRef('snapshot/testing/test_skipping.py', 'source-hash', 20, 23, 'repository')
    previous = IssueContract('c', version=1, trigger='module-level skip without flag',
        expected='The diagnostic must mention allow_module_level.', reported_actual=OLD,
        observable_checks=('Actual diagnostic contains allow_module_level.',), sources=(issue,),
        assumptions=('The old sentence need not be retained verbatim.',))
    sources = (issue, observed)
    texts = ('The diagnostic must explain allow_module_level; exact wording may change.',
             f'assert {OLD!r} in diagnostic  # existing original test')
    calls = []

    def handle(request):
        data = json.loads(request.content)
        calls.append(data)
        answer = {'trigger': previous.trigger, 'expected': previous.expected,
            'reported_actual': previous.reported_actual,
            'observable_checks': list(previous.observable_checks),
            'assumptions': list(previous.assumptions), 'missing_information': [], 'source_indices': [0, 1]}
        if not correct_format and len(calls) == 1:
            answer['expected'] = {'wrong': 'not a string'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(answer)}}]})

    async def run():
        model = gateway(handle)
        try:
            return await ReproAgent(model, facts.context()).analyze(
                IssueDescription(texts[0], 'issue-hash'), EvidenceContext(sources, texts, previous))
        finally:
            await model.aclose()

    revised = asyncio.run(run())
    assert len(calls) == (1 if correct_format else 2)
    assert revised.contract_id == previous.contract_id and revised.version == 2
    assert revised.sources == sources and revised.observable_checks == previous.observable_checks
    for payload in calls:
        data = json.loads(payload['messages'][-1]['content'])
        assert data['previous_contract']['version'] == 1
        assert data['previous_contract']['expected'] == previous.expected
        assert data['previous_contract']['observable_checks'] == list(previous.observable_checks)
        assert data['previous_contract']['assumptions'] == list(previous.assumptions)
        assert 'sources' not in data['previous_contract'], 'interpretation is not another citable source'
        assert data['sources'] == [asdict(source) for source in sources]
        assert data['texts'] == list(texts)
        assert data['response_schema']['properties']['source_indices']['items']['maximum'] == 1
        system = payload['messages'][0]['content']
        assert 'previous_contract is an interpretation' in system
        assert 'observed old behavior' in system
        assert 'mutually consistent' in system
    if not correct_format:
        assert 'protocol_error' in json.loads(calls[1]['messages'][-1]['content'])


def test_initial_analysis_does_not_add_a_previous_contract(facts):
    from tests.unit.test_agent import agent
    from reproagent.core.models import SourceRef
    strategy, model, _ = agent({'trigger': 'empty', 'expected': 'return []', 'reported_actual': 'error',
        'source_indices': [0]}, facts)
    asyncio.run(strategy.analyze(IssueDescription('empty returns []', 'h'),
        EvidenceContext((SourceRef('issue.md', 'h'),), ('empty returns []',))))
    assert 'previous_contract' not in json.loads(model.messages[-1]['content'])
