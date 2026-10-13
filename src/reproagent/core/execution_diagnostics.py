"""Controlled execution facts, independent of model claims and failure prose."""
from .models import ExecutionDiagnostic
from reproagent.workspace import candidate_hash

INVALID_INTEGRITY_CODES = frozenset({'RUN_BINDING_MISMATCH', 'CANDIDATE_HASH_MISMATCH',
                                   'SOURCE_PROTECTION_FAILED'})


def execution_bound(contract, candidate, run):
    return (run.candidate_id == candidate.candidate_id and run.manifest_hash == candidate.manifest_hash
            and run.contract_id == contract.contract_id == candidate.contract_id
            and run.contract_version == contract.version == candidate.contract_version
            and run.snapshot_id == candidate.snapshot_id and run.execution_role == 'original')


def _diagnostic(run, code):
    stage = 'unknown'
    if run.observation.tests:
        last = run.observation.tests[-1]
        when, outcome = last.get('when'), last.get('outcome')
        if when in ('setup', 'call', 'teardown') and outcome in ('passed', 'failed', 'skipped'):
            stage = when + ':' + outcome
    return ExecutionDiagnostic(code, run.raw.stop_reason, run.raw.duration,
        run.observation.probe_complete, run.raw.stop_reason == 'EXITED' and run.raw.cleanup_ok, stage)


def diagnose_runtime(run):
    raw, observation = run.raw, run.observation
    failed = tuple(name for name, ok in (
        ('cleanup_ok', raw.cleanup_ok), ('execution_exited', raw.stop_reason == 'EXITED'),
        ('logs_complete', not raw.log_truncated), ('probe_complete', observation.probe_complete),
        ('source_protection', run.protection.ok if raw.stop_reason == 'EXITED' and raw.cleanup_ok else True)) if not ok)
    if not failed:
        return None, ()
    if not raw.cleanup_ok:
        code = 'CLEANUP_FAILED'
    elif raw.stop_reason != 'EXITED':
        code = raw.stop_reason if raw.stop_reason in ('COMMAND_TIMEOUT', 'LOG_LIMIT', 'CANCELLED', 'EXHAUSTED') else 'EXECUTION_NOT_EXITED'
    elif raw.log_truncated:
        code = 'LOG_LIMIT'
    elif not observation.probe_complete:
        code = 'PROBE_INCOMPLETE'
    else:
        code = 'SOURCE_PROTECTION_FAILED'
    return _diagnostic(run, code), failed


def diagnose_execution(contract, candidate, run):
    if not execution_bound(contract, candidate, run):
        return _diagnostic(run, 'RUN_BINDING_MISMATCH'), ('run_binding',)
    if candidate_hash(candidate) != candidate.manifest_hash:
        return _diagnostic(run, 'CANDIDATE_HASH_MISMATCH'), ('candidate_manifest_hash',)
    return diagnose_runtime(run)


def diagnostic_reason(diagnostic, failed):
    return (f'{diagnostic.code}: execution stop={diagnostic.stop_reason}; '
            f'duration={diagnostic.duration_seconds:.3f}s; probe_complete={diagnostic.probe_complete}; '
            f'protection_checked={diagnostic.protection_checked}; last_test_stage={diagnostic.last_test_stage}; '
            f'failed checks={", ".join(failed)}')
