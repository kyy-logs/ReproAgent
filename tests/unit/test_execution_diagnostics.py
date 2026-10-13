import asyncio
from dataclasses import replace

import pytest

from reproagent.core.models import (ExecutionResult, RawExecution, TestObservation as Observation,
    ProtectionCheck, EvidenceRef, IssueContract, Verdict, CandidateClass)
from reproagent.core.serialization import encode_record, decode_record
from tests.integration.test_runner import setup_runner
from tests.unit.test_verifier import EvidenceModel


@pytest.fixture
def execution_facts(tmp_path, projects, facts):
    _, _, workspace, snapshot, candidate, _ = setup_runner(tmp_path, projects, facts)
    contract = IssueContract(candidate.contract_id, version=candidate.contract_version)
    raw = RawExecution('spec', 1, 1.0, 'EXITED', EvidenceRef('stdout', '0' * 64),
                       EvidenceRef('stderr', '0' * 64), True)
    run = ExecutionResult('run', 'spec', snapshot.snapshot_id, 'env', candidate.candidate_id,
        candidate.manifest_hash, candidate.contract_id, candidate.contract_version, raw,
        Observation(True, True, True, tests=({'when': 'setup', 'outcome': 'passed'},)), ProtectionCheck())
    return contract, candidate, run, workspace.store


@pytest.mark.parametrize('stop,code', [('COMMAND_TIMEOUT', 'COMMAND_TIMEOUT'), ('LOG_LIMIT', 'LOG_LIMIT'),
                                    ('UNEXPECTED', 'EXECUTION_NOT_EXITED')])
def test_incomplete_execution_has_specific_diagnostic_and_never_calls_model(execution_facts, facts, stop, code):
    from reproagent.core.verifier import Verifier
    contract, candidate, run, store = execution_facts
    run = replace(run, raw=replace(run.raw, stop_reason=stop, exit_code=None, duration=60.01),
        observation=replace(run.observation, probe_complete=False, executed=False),
        protection=ProtectionCheck(candidate_ok=False))
    model = EvidenceModel()
    result = asyncio.run(Verifier(store, None, model).evaluate(contract, candidate, (run,), facts.context()))
    assert result.classification is None
    assert result.execution_diagnostic.code == code
    assert result.execution_diagnostic.protection_checked is False
    assert result.execution_diagnostic.last_test_stage == 'setup:passed'
    assert 'execution_exited' in result.unmet_checks and 'probe_complete' in result.unmet_checks
    assert not model.calls


@pytest.mark.parametrize('change,code', [('binding', 'RUN_BINDING_MISMATCH'),
    ('hash', 'CANDIDATE_HASH_MISMATCH'), ('cleanup', 'CLEANUP_FAILED'),
    ('probe', 'PROBE_INCOMPLETE'), ('protection', 'SOURCE_PROTECTION_FAILED'), ('logs', 'LOG_LIMIT')])
def test_diagnosis_distinguishes_hard_failures(execution_facts, change, code):
    from reproagent.core.execution_diagnostics import diagnose_execution
    contract, candidate, run, _ = execution_facts
    if change == 'binding': run = replace(run, candidate_id='wrong', raw=replace(run.raw, stop_reason='COMMAND_TIMEOUT'))
    if change == 'hash':
        candidate = replace(candidate, hypothesis='altered')
    if change == 'cleanup': run = replace(run, raw=replace(run.raw, cleanup_ok=False))
    if change == 'probe': run = replace(run, observation=replace(run.observation, probe_complete=False))
    if change == 'protection': run = replace(run, protection=ProtectionCheck(modified=('example/parser.py',)))
    if change == 'logs': run = replace(run, raw=replace(run.raw, log_truncated=True))
    diagnostic, failed = diagnose_execution(contract, candidate, run)
    assert diagnostic.code == code and failed


def test_old_verdict_and_new_diagnostic_roundtrip(execution_facts):
    from reproagent.core.execution_diagnostics import diagnose_execution
    contract, candidate, run, _ = execution_facts
    old = encode_record(Verdict(CandidateClass.NOT_REPRODUCED, 'passes'))
    old.pop('execution_diagnostic', None)
    assert decode_record('verdicts', old).execution_diagnostic is None
    diagnostic, failed = diagnose_execution(contract, candidate,
        replace(run, raw=replace(run.raw, stop_reason='COMMAND_TIMEOUT')))
    verdict = Verdict(None, 'execution did not finish', unmet_checks=failed, execution_diagnostic=diagnostic)
    assert decode_record('verdicts', encode_record(verdict)) == verdict


def test_completed_runtime_and_unknown_stage(execution_facts):
    from reproagent.core.execution_diagnostics import diagnose_execution, diagnose_runtime
    contract, candidate, run, _ = execution_facts
    assert diagnose_execution(contract, candidate, run) == (None, ())
    diagnostic, _ = diagnose_runtime(replace(run, raw=replace(run.raw, stop_reason='COMMAND_TIMEOUT'),
                                             observation=replace(run.observation, tests=())))
    assert diagnostic.last_test_stage == 'unknown'
