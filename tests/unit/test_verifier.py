import asyncio
import importlib
from dataclasses import replace

import pytest
from reproagent.core.models import CandidateClass, EvidenceRef, IssueContract, ModelResponse, SourceRef
from reproagent.core.serialization import bytes_hash
from tests.integration.test_runner import setup_runner, execute


class EvidenceModel:
    def __init__(self, classification='REPRODUCED', references=True):
        self.calls = []; self.classification = classification; self.references = references
    async def complete(self, request, context):
        import json
        self.calls.append(request)
        data = json.loads(request.messages[-1]['content'])
        refs = data['available_refs'] if self.references else [{'path':'unknown','content_hash':'bad'}]
        return ModelResponse(json.dumps({'classification':self.classification, 'reason':'Empty input raises the reported IndexError', 'evidence_refs':refs,
            'expected_assertion':True, 'target_triggered':True, 'failure_matches_issue':True}))


def prepare(tmp_path, projects, facts, text=None, model=None):
    _, request, workspace, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts, text=text)
    source_path = snapshot.root / 'issue.md'
    source = SourceRef(source_path.relative_to(workspace.root).as_posix(), bytes_hash(source_path.read_bytes()))
    contract = IssueContract('contract-1', expected='parse([]) returns []', reported_actual='IndexError', trigger='empty list', sources=(source,))
    execution = execute(request, snapshot, candidate, runner, facts.context())
    model = model or EvidenceModel()
    verifier = importlib.import_module('reproagent.core.verifier').Verifier(workspace.store, runner.adapter, model)
    return contract, candidate, execution, verifier, model, runner, snapshot, request


@pytest.mark.parametrize('change', ['hash','origin','xfail','probe'])
def test_wrong_hash_origin_xfail_or_incomplete_probe_blocks_success_without_model_call(tmp_path, projects, facts, change):
    contract, candidate, execution, verifier, model, *_ = prepare(tmp_path, projects, facts)
    if change == 'hash': execution = replace(execution, manifest_hash='wrong')
    elif change == 'probe': execution = replace(execution, observation=replace(execution.observation, probe_complete=False))
    elif change == 'origin': execution = replace(execution, observation=replace(execution.observation, framework_details={'source_binding_ok':False}))
    else: execution = replace(execution, observation=replace(execution.observation, tests=({'when':'call','outcome':'skipped','wasxfail':'known'},)))
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == CandidateClass.INVALID_CANDIDATE and not model.calls


def test_expected_bug_exception_probe_is_not_final_regression_test(tmp_path, projects, facts):
    text = 'import pytest\nfrom example.parser import parse\ndef test_empty():\n    with pytest.raises(IndexError): parse([])\n'
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, text)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == CandidateClass.NOT_REPRODUCED


@pytest.mark.parametrize('text,expected', [
    ('def test_bad(unknown_fixture): assert False\n', CandidateClass.INVALID_CANDIDATE),
    ('def test_bad(): import dependency_does_not_exist_123\n', CandidateClass.INVALID_CANDIDATE),
    ('from example.parser import parse\ndef test_bad():\n    parse([1])\n    assert False\n', CandidateClass.NOT_REPRODUCED)])
def test_unknown_fixture_unrelated_failure_and_dependency_block_are_distinct(tmp_path, projects, facts, text, expected):
    model = EvidenceModel('NOT_REPRODUCED')
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, text, model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == expected


def test_semantic_verdict_requires_resolvable_evidence_refs(tmp_path, projects, facts):
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=EvidenceModel(references=False))
    assert asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context())).classification is None


def test_replay_compares_target_failure_not_entire_stdout(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))
    assert verifier.confirm(contract, candidate, first, second, verdicts)
    assert not verifier.confirm(contract, candidate, first, replace(second, execution_role='fixed'), verdicts)
    changed = replace(second, observation=replace(second.observation, tests=tuple(dict(t, crash={'path':'other.py','lineno':1,'message':'ValueError'}) if t['outcome']=='failed' else t for t in second.observation.tests)))
    assert not verifier.confirm(contract, candidate, first, changed, verdicts)
