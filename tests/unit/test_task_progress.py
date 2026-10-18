import json
from dataclasses import replace
from pathlib import Path

from reproagent.core.models import (TaskResult, TaskState, IssueContract, EvidenceLevel,
    RawExecution, ExecutionResult, TestObservation as Observation, ProtectionCheck, EvidenceRef)
from reproagent.core.serialization import bytes_hash
from reproagent.store import TaskStore


def test_records_drive_state_and_contract_change_invalidates_current_evidence(tmp_path):
    store = TaskStore(tmp_path)
    store.save_contract(IssueContract('c1', trigger='parse empty', version=1))
    store.save_record('task', 'task', TaskResult('task', TaskState.VERIFYING,
        accepted_candidate_id='candidate-1', evidence_level=EvidenceLevel.SINGLE_OBSERVATION))
    data = json.loads((tmp_path/'STATE.json').read_text())
    assert data['completion']['evidence_level'] == 'SINGLE_OBSERVATION'
    assert data['status'] == 'VERIFYING'
    before = data['revision']
    store.save_contract(IssueContract('c1', trigger='new trigger', version=2))
    data = store.progress.snapshot()
    assert data['revision'] > before and data['contract']['version'] == 2
    assert data['completion']['accepted_candidate_id'] is None
    assert data['facts'] == [] and data['latest_candidate'] is None


def make_run(store, role='original'):
    path = store.root/'output.log';path.write_text('original log' if role=='original' else 'HIDDEN_FIX')
    ref = EvidenceRef('output.log', bytes_hash(path.read_bytes()))
    raw = RawExecution('spec', None, 60, 'COMMAND_TIMEOUT', ref, ref, True)
    return ExecutionResult('r-'+role,'spec','snap','env','cand','hash','c1',1,raw,
        Observation(collected=True,executed=False,probe_complete=False),ProtectionCheck(),role)


def test_original_run_has_honest_failure_summary_and_fixed_is_not_loaded(tmp_path):
    store=TaskStore(tmp_path)
    store.save_contract(IssueContract('c1',version=1))
    original=make_run(store)
    store.save_record('runs',original.run_id,original)
    state=store.progress.snapshot()
    assert state['facts'][-1]['stop_reason']=='COMMAND_TIMEOUT'
    assert state['facts'][-1]['executed'] is False
    assert state['blockers'][0]['code']=='COMMAND_TIMEOUT'
    assert state['materials']
    identifiers={m['id'] for m in store.materials.catalog()}
    fixed=make_run(store,'fixed')
    store.save_record('runs',fixed.run_id,fixed)
    assert {m['id'] for m in store.materials.catalog()}==identifiers
    assert 'HIDDEN_FIX' not in json.dumps(store.progress.snapshot())


def test_tool_refusal_updates_blocker_and_budget_is_live(tmp_path, facts):
    store=TaskStore(tmp_path)
    store.append_event('exploration.action',(),{'action':'Read','result_code':'RESERVED_FOR_PUBLISHING'})
    budget=facts.context().budget
    before=store.progress.snapshot(budget)['budgets']['remaining_steps']
    budget.take_step()
    data=store.progress.snapshot(budget)
    assert data['budgets']['remaining_steps']==before-1
    assert data['blockers'][0]['code']=='RESERVED_FOR_PUBLISHING'
    assert len(json.dumps(store.progress.view(),ensure_ascii=False).encode()) < 4096


def test_corrupt_snapshot_cannot_invent_completion(tmp_path):
    (tmp_path/'STATE.json').write_text('{broken')
    store=TaskStore(tmp_path)
    data=store.progress.snapshot()
    assert data['status']=='UNKNOWN'
    assert data['completion']['evidence_level']=='NONE'


def test_state_write_failure_leaves_old_snapshot_and_marks_unavailable(tmp_path,monkeypatch):
    store=TaskStore(tmp_path)
    store.save_contract(IssueContract('c1'))
    old=(tmp_path/'STATE.json').read_bytes()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr('reproagent.task_progress.atomic_write',fail)
    store.save_contract(IssueContract('c1',version=2))
    assert (tmp_path/'STATE.json').read_bytes()==old
    assert store.progress.last_error=='state_store_error'
