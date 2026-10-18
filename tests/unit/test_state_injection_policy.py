import json
from dataclasses import replace

import pytest

from reproagent.core.models import (CandidateClass, EvidenceLevel, IssueContract, TaskRequest,
    TaskResult, TaskState, Verdict)
from reproagent.core.serialization import decode_record, encode_record
from reproagent.store import TaskStore
from tests.unit.test_task_progress import make_run


def test_legacy_task_config_defaults_to_no_state_injection(tmp_path):
    data = dict(schema_version=1, repo=str(tmp_path), output_dir=str(tmp_path/'out'),
                issue_file=str(tmp_path/'issue.md'))
    request = decode_record('request', data)
    assert getattr(request, 'inject_task_state', None) is False


def test_state_injection_is_explicit_and_recorded(tmp_path):
    data = dict(schema_version=1, repo=str(tmp_path), output_dir=str(tmp_path/'out'),
                issue_file=str(tmp_path/'issue.md'), inject_task_state=True)
    request = decode_record('request', data)
    assert request.inject_task_state is True
    assert encode_record(request)['inject_task_state'] is True


@pytest.mark.parametrize('value', ['false', 0, 1, None])
def test_direct_task_request_rejects_non_boolean_state_switch(tmp_path, value):
    with pytest.raises(ValueError, match='inject_task_state must be a bool'):
        TaskRequest(tmp_path, tmp_path/'out', tmp_path/'issue.md', inject_task_state=value)


def test_failed_replay_keeps_observation_but_never_final_acceptance(tmp_path):
    store = TaskStore(tmp_path)
    store.save_contract(IssueContract('c1'))
    run = make_run(store)
    store.save_record('runs', run.run_id, run)
    provisional = TaskResult('task', TaskState.REPLAYING, accepted_candidate_id='cand',
                             evidence_level=EvidenceLevel.SINGLE_OBSERVATION)
    store.save_record('task', 'task', provisional)
    store.save_record('verdicts', 'replay', Verdict(CandidateClass.INVALID_CANDIDATE,
        'independent replay did not confirm', candidate_id='cand', contract_id='c1',
        run_ids=(run.run_id,)))
    # The Controller can save the same historical observation again after a rejection.
    store.save_record('task', 'task', replace(provisional, status=TaskState.GENERATING))
    state = json.loads((tmp_path/'STATE.json').read_text())
    assert state['latest_candidate']['result'] == 'INVALID_CANDIDATE'
    assert state['completion']['accepted_candidate_id'] is None
    assert state['completion']['observation_candidate_id'] == 'cand'
    assert state['completion']['evidence_level'] == 'SINGLE_OBSERVATION'
    assert state['completion']['final_accepted'] is False
    assert store.progress.view()['completion'] == state['completion']
    assert store.load_record('task', 'task').accepted_candidate_id == 'cand'


@pytest.mark.parametrize('status,level,export,accepted', [
    (TaskState.VERIFYING, EvidenceLevel.SINGLE_OBSERVATION, 'pending', False),
    (TaskState.REPLAYING, EvidenceLevel.REPEATED_OBSERVATION, 'pending', False),
    (TaskState.EXHAUSTED, EvidenceLevel.REPEATED_OBSERVATION, 'published', False),
    (TaskState.FAILED, EvidenceLevel.DIFFERENTIAL_VALIDATED, 'failed', False),
    (TaskState.DONE, EvidenceLevel.SINGLE_OBSERVATION, 'published', False),
    (TaskState.DONE, EvidenceLevel.REPEATED_OBSERVATION, 'published', True),
    (TaskState.DONE, EvidenceLevel.DIFFERENTIAL_VALIDATED, 'published', True),
])
def test_only_completed_accepted_result_is_final(tmp_path, status, level, export, accepted):
    store = TaskStore(tmp_path)
    store.save_record('task', 'task', TaskResult('task', status, accepted_candidate_id='cand',
        evidence_level=level, export_state=export))
    for completion in (store.progress.snapshot()['completion'], TaskStore(tmp_path).progress.snapshot()['completion']):
        assert completion.get('final_accepted') is accepted
        assert completion.get('observation_candidate_id') == 'cand'
        assert completion['accepted_candidate_id'] == ('cand' if accepted else None)
