import json
from importlib.resources import files
from pathlib import Path

from ..observability import span
from .models import CandidateClass, EvidenceRef, ModelRequest, Verdict
from .review_context import RUN_ROOT, ReviewContextTooLarge, _root_spellings, build_review_context
from .serialization import bytes_hash, canonical_bytes, parse_json
from reproagent.store import safe_child
from reproagent.workspace import candidate_hash
from .budget import BudgetStopped
from .protocol import ModelOutputError, validate_verdict


class CandidateFileChanged(ValueError):
    """A stored candidate file no longer matches its published hash."""


class Verifier:
    def __init__(self, store, adapter, gateway, secrets=()):
        self.store, self.adapter, self.gateway, self.secrets = store, adapter, gateway, tuple(s for s in secrets if s)

    def resolve(self, ref):
        path = safe_child(self.store.root, ref.path)
        data = path.read_bytes()
        if bytes_hash(data) != ref.content_hash:
            raise ValueError('evidence hash mismatch')
        if type(ref.start_line) is not int or type(ref.end_line) is not int or not 1 <= ref.start_line <= ref.end_line <= max(1, len(data.splitlines())):
            raise ValueError('evidence line range invalid')
        return data.decode('utf-8')

    def _bound(self, contract, candidate, run):
        return run.candidate_id == candidate.candidate_id and run.manifest_hash == candidate.manifest_hash and run.contract_id == contract.contract_id == candidate.contract_id and run.contract_version == contract.version == candidate.contract_version and run.snapshot_id == candidate.snapshot_id and run.execution_role == 'original'

    async def evaluate(self, contract, candidate, executions, context):
        with span('verify'):
            return await self._evaluate(contract, candidate, executions, context)

    async def _evaluate(self, contract, candidate, executions, context):
        context.budget.check()
        def verdict(classification, reason, refs=(), uncertainty=()):
            return Verdict(classification, reason, refs, uncertainties=uncertainty, candidate_id=candidate.candidate_id,
                manifest_hash=candidate.manifest_hash, contract_id=contract.contract_id, contract_version=contract.version,
                run_ids=tuple(run.run_id for run in executions))
        if not executions:
            return verdict(None, 'no execution evidence')
        run = executions[-1]
        if not self._bound(contract, candidate, run) or candidate_hash(candidate) != candidate.manifest_hash or not run.protection.ok or not run.raw.cleanup_ok or not run.observation.probe_complete or run.raw.log_truncated or run.raw.stop_reason != 'EXITED':
            return verdict(CandidateClass.INVALID_CANDIDATE, 'binding, protection, cleanup or probe integrity failed')
        checks = self.adapter.check_framework(candidate, run.observation)
        non_origin_errors = tuple(error for error in checks.invalid if 'origin' not in error and error != 'no collected tests')
        if non_origin_errors:
            return verdict(CandidateClass.INVALID_CANDIDATE, '; '.join(non_origin_errors))
        if checks.blocked:
            if not run.observation.target_origins:
                return verdict(CandidateClass.INVALID_CANDIDATE, 'candidate import references an unavailable module before target execution')
            if 'ModuleNotFoundError' not in contract.reported_actual and 'import' not in contract.reported_actual.lower():
                return verdict(CandidateClass.ENVIRONMENT_BLOCKED, '; '.join(checks.blocked))
        if checks.invalid or not run.observation.executed:
            return verdict(CandidateClass.INVALID_CANDIDATE, '; '.join(checks.invalid) or 'no executed tests')
        if run.raw.exit_code == 0:
            return verdict(CandidateClass.NOT_REPRODUCED, 'candidate passes on original snapshot')
        if not contract.expected or not contract.sources:
            return verdict(None, 'expected behavior lacks source', uncertainty=('expectation is ungrounded',))
        # Every hard check above ran against the complete ExecutionResult. Only a
        # bounded, citable projection reaches the model, and nothing here may turn a
        # failed hard check or an unusable projection into a success.
        def read_file(entry):
            data = safe_child(candidate.storage_root, entry.path).read_bytes()
            if bytes_hash(data) != entry.content_hash:
                raise CandidateFileChanged('candidate file hash changed')
            return data
        cap = context.budget.limits.tool_response_bytes
        def project(protocol_error=''):
            return build_review_context(contract, candidate, run, read_ref=self.resolve, read_file=read_file,
                max_bytes=cap, protocol_error=protocol_error)
        try:
            review = project()
        except CandidateFileChanged:
            return verdict(CandidateClass.INVALID_CANDIDATE, 'candidate file hash changed')
        except ReviewContextTooLarge:
            return verdict(None, 'semantic evidence exceeds bounded context; narrow candidate', uncertainty=('evidence omitted rather than silently truncated',))
        except (ValueError, OSError, UnicodeError):
            return verdict(None, 'evidence source is unavailable or altered')
        sources = tuple(EvidenceRef(s.path, s.content_hash, s.start_line, s.end_line) for s in contract.sources)
        prompt = files('reproagent').joinpath('prompts/review_evidence.md').read_text(encoding='utf-8')
        error = ''
        for attempt in range(3):
            context.budget.check()
            if context.cancel_event.is_set():
                raise BudgetStopped('CANCELLED')
            text = canonical_bytes(review.payload).decode()
            for secret in self.secrets:
                text = text.replace(secret, '[REDACTED]')
            if len(text.encode()) > cap:
                return verdict(None, 'semantic evidence exceeds bounded context; narrow candidate', uncertainty=('evidence omitted rather than silently truncated',))
            try:
                response = await self.gateway.complete(ModelRequest(({'role':'system','content':prompt}, {'role':'user','content':text}), 'verdict'), context)
            except ModelOutputError as exc:
                if not exc.retryable:
                    # The provider already said why the answer is unusable; a correction
                    # request would be the same request again, so it ends the call here.
                    raise
                error = str(exc)
            else:
                result = {}
                try:
                    result = parse_json(response.text)
                    validate_verdict(result)
                    classification = CandidateClass(result['classification']) if result['classification'] else None
                    refs = tuple(EvidenceRef(**ref) for ref in result['evidence_refs'])
                    if any(ref not in review.available_refs for ref in refs):
                        raise ValueError('unknown evidence citation; copy exact available_refs')
                    for ref in refs:
                        self.resolve(ref)
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    if any(result.get(key) is False for key in ('expected_assertion','target_triggered','failure_matches_issue')):
                        return verdict(None, 'negative semantic check in an invalid verdict; correction cannot promote it to success')
                    error = str(exc)
                else:
                    if classification == CandidateClass.REPRODUCED:
                        if not all(result[key] for key in ('expected_assertion', 'target_triggered', 'failure_matches_issue')):
                            return verdict(None, 'semantic reproduction checks are not all true', refs)
                        if not any(ref in sources for ref in refs) or not any(ref in run.observation.failure_refs for ref in refs):
                            error = 'REPRODUCED requires citations to both expectation source and actual failure'
                        else:
                            return verdict(classification, result['reason'], refs)
                    else:
                        return verdict(classification, result['reason'], refs)
            # A correction re-measures the whole payload, protocol error included: the
            # request that is actually sent has to stay inside the same budget.
            try:
                review = project(error[:512])
            except CandidateFileChanged:
                return verdict(CandidateClass.INVALID_CANDIDATE, 'candidate file hash changed')
            except ReviewContextTooLarge:
                return verdict(None, 'semantic evidence exceeds bounded context; narrow candidate', uncertainty=('evidence omitted rather than silently truncated',))
            except (ValueError, OSError, UnicodeError):
                return verdict(None, 'evidence source is unavailable or altered')
        return verdict(None, 'semantic verdict invalid after 3 attempts: ' + error[:512], uncertainty=('MODEL_PROTOCOL_ERROR',))

    def confirm(self, contract, candidate, first, second, verdicts):
        if len(verdicts) != 2 or any(v.classification != CandidateClass.REPRODUCED or v.candidate_id != candidate.candidate_id or v.manifest_hash != candidate.manifest_hash or v.contract_id != contract.contract_id or v.contract_version != contract.version for v in verdicts):
            return False
        if first.run_id == second.run_id or any(not self._bound(contract, candidate, run) or not run.protection.ok or not run.raw.cleanup_ok or not run.observation.probe_complete for run in (first, second)):
            return False
        if first.observation.framework_details.get('preconditions') != second.observation.framework_details.get('preconditions') or candidate.preconditions:
            # External-state reset cannot yet be demonstrated by this local runner.
            return False
        if first.observation.framework_details.get('collected_nodeids') != second.observation.framework_details.get('collected_nodeids') or first.observation.framework_details.get('completed_nodeids') != second.observation.framework_details.get('completed_nodeids'):
            return False
        def _run_roots(run_root):
            """Every spelling of the directories one run's own paths may name, longest first.

            The run root is the code copy, but the run writes beside it as well: pytest puts
            its basetemp under the run directory, so a tmpdir failure reports ``<run>/tmp/...``
            and not ``<run>/code/...``.  Normalizing the code copy alone left two runs of one
            candidate reporting different messages for a failure that was identical, and the
            independent replay -- whose whole job is to confirm the same failure -- refused
            it.  Only what belongs to this run is replaced; the two runs differ by their run
            directory, and that is exactly the difference the replay is supposed to ignore.
            """
            if not run_root:
                return ()
            workspace = str(Path(run_root).parent)
            if workspace in ('', '.', run_root):
                return _root_spellings(run_root)
            return tuple(sorted(_root_spellings(run_root) + _root_spellings(workspace), key=len, reverse=True))

        def signatures(run):
            from .failure_signature import stable_failure_message
            roots = _run_roots(run.observation.framework_details.get('run_root', ''))
            def normalized(text):
                for spelling in roots:
                    text = text.replace(spelling, RUN_ROOT)
                return text
            result = []
            for phase in run.observation.tests:
                if phase.get('outcome') == 'failed':
                    crash = dict(phase.get('crash', {}))
                    crash['path'] = normalized(str(crash.get('path', '')))
                    crash['message'] = stable_failure_message(normalized(str(crash.get('message', ''))))
                    comparison = phase.get('assertion_comparison')
                    if phase.get('exception_type') == 'builtins.AssertionError' and comparison:
                        # Compare the observed complete values, not pytest's
                        # abbreviated display. Preserve location, type and op.
                        crash['message'] = {'operator': comparison['operator'],
                            **{key:stable_failure_message(normalized(comparison[key]), comparison.get(key + '_object_ids', ()))
                               for key in ('left', 'right')}}
                    nodeid = phase.get('nodeid', '').replace('\\', '/')
                    nodeid = nodeid.split(run.run_id + '/code/', 1)[-1]
                    result.append((nodeid, phase.get('when'), phase.get('exception_type', ''), json.dumps(crash, sort_keys=True)))
            return result
        return bool(signatures(first)) and signatures(first) == signatures(second)
