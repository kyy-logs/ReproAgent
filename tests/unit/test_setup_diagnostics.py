import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reproagent.core.models import CandidateClass, VerificationIssue
from reproagent.core.verification_feedback import render_verification_feedback
from tests.unit.test_verifier import prepare

MISSING_RESOURCE = """import pytest
from pathlib import Path
from example.parser import parse
@pytest.fixture
def resource():
    return Path(__file__).with_name('missing-resource.txt').read_text()
def test_target(resource):
    assert parse([]) == []
"""


def test_real_setup_failure_exposes_details_without_semantic_model(tmp_path, projects, facts):
    contract, candidate, run, verifier, model, *_ = prepare(tmp_path, projects, facts, MISSING_RESOURCE)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context()))
    assert verdict.classification == CandidateClass.INVALID_CANDIDATE and not model.calls
    issue = verdict.issues[0]
    assert issue.code == 'TEST_SETUP_FAILED'
    assert issue.origin == 'program_check' and issue.recovery == 'inspect_evidence'
    row = issue.measurements['setup_failures'][0]
    assert row['exception_type'] == 'builtins.FileNotFoundError'
    assert 'missing-resource.txt' in row['message']
    assert str(verifier.store.root) not in row['message']
    assert '<run>' in row['message']
    assert issue.evidence_refs
    for ref in issue.evidence_refs:
        verifier.resolve(ref)
    feedback = json.loads(render_verification_feedback(verdict, result_code='INVALID_CANDIDATE', byte_limit=8192))
    assert feedback['verdict']['issues'][0]['measurements']['setup_failures'] == [row]


def synthetic(phases, root='/task/runs/run-one'):
    return SimpleNamespace(run_id='run-one', observation=SimpleNamespace(tests=tuple(phases),
        framework_details={'run_root':root}))


def test_setup_projection_filters_bounds_normalizes_and_redacts():
    from reproagent.core.setup_diagnostics import setup_failure_details
    root = r'C:\task\runs\run-one'
    phases = [{'when':'setup','outcome':'failed','nodeid':'tests/test_one.py::test_' + str(i),
        'exception_type':'builtins.FileNotFoundError','crash':{
            'message':root.replace('\\','\\\\') + r'\code\private-secret\missing.txt' + 'x'*800}}
        for i in range(6)]
    phases += [{'when':'call','outcome':'failed','crash':{'message':'ignore call'}},
               {'when':'setup','outcome':'skipped','longrepr':'ignore skipped'}]
    data = setup_failure_details(synthetic(phases, root), task_root='/task', secrets=('private-secret',))
    assert len(data['setup_failures']) == 4 and data['omitted_setup_failures'] == 2
    for row in data['setup_failures']:
        assert set(row) == {'nodeid','exception_type','message'}
        assert all(len(value) <= 512 for value in row.values())
        assert '<run>' in row['message'] and 'private-secret' not in row['message']
        assert '[REDACTED]' in row['message']


def test_absent_crash_uses_bounded_observed_longrepr_not_a_guessed_cause():
    from reproagent.core.setup_diagnostics import setup_failure_details
    data = setup_failure_details(synthetic([{'when':'setup','outcome':'failed',
        'nodeid':'tests/x.py::test_x', 'exception_type':'RuntimeError',
        'longrepr':'prefix\nE RuntimeError: setup incomplete'}]), task_root='/task')
    assert 'setup incomplete' in data['setup_failures'][0]['message']


def test_malformed_and_non_setup_records_do_not_invent_setup_failures():
    from reproagent.core.setup_diagnostics import setup_failure_details
    assert setup_failure_details(synthetic([None, 'bad', {'when':'setup','outcome':'skipped'},
        {'when':'call','outcome':'failed'}]), task_root='/task') == {}


@pytest.mark.parametrize('rows', [[{'message':'missing fields'}], [{'nodeid':'n','exception_type':'E','message':'x'*513}]])
def test_setup_measurements_reject_malformed_or_oversized_rows(rows):
    with pytest.raises(ValueError):
        VerificationIssue('TEST_SETUP_FAILED','tests_executed_and_valid','program_check','setup failed',
            measurements={'setup_failures':rows,'omitted_setup_failures':0}, recovery='inspect_evidence')
