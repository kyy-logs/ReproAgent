import json
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from ..observability import mark, record_check, span
from .models import CandidateClass, EvidenceRef, ModelRequest, Verdict, VerificationIssue

#: The hard checks, in the order the program reaches them.  A trace shows which ran:
#: everything after the first short-circuit has no record at all, which is the only way
#: it can avoid reading as a check that passed.
VERIFIER_CHECKS = ("execution_evidence_present", "binding_and_integrity_ok",
                   "no_non_origin_framework_errors", "framework_not_blocked",
                   "tests_executed_and_valid", "candidate_reproduced",
                   "expectation_is_grounded", "review_context_fits", "candidate_assertions_supported",
                   "provider_semantic_claims", "citations_cover_expectation_and_failure")
from .review_context import RUN_ROOT, ReviewContextTooLarge, _root_spellings, build_review_context
from .serialization import bytes_hash, canonical_bytes, parse_json
from reproagent.store import safe_child
from reproagent.workspace import candidate_hash
from .verification_issues import ISSUE_SPECS
from .budget import BudgetStopped
from .semantic_reuse import SemanticApproval, rebound_refs, review_key
from .serialization import canonical_hash
from .protocol import ModelOutputError, validate_verdict
from .execution_diagnostics import diagnose_execution, execution_bound, diagnostic_reason, INVALID_INTEGRITY_CODES


class CandidateFileChanged(ValueError):
    """A stored candidate file no longer matches its published hash."""


def _overflow_reason(needed_bytes: int, budget_bytes: int) -> str:
    """A refusal that cannot be acted on is a defect: it has to carry its own magnitude.

    The magnitude remains in the readable reason and the typed issue measurements.
    Program decisions use the controlled code, never parse this prose.
    """
    return (f'semantic evidence exceeds bounded context (review evidence needs {needed_bytes} bytes; '
            f'budget is {budget_bytes} bytes); narrow candidate')


class Verifier:
    def __init__(self, store, adapter, gateway, secrets=()):
        self.store, self.adapter, self.gateway, self.secrets = store, adapter, gateway, tuple(s for s in secrets if s)
        self._semantic_approval = None

    def resolve(self, ref):
        path = safe_child(self.store.root, ref.path)
        data = path.read_bytes()
        if bytes_hash(data) != ref.content_hash:
            raise ValueError('evidence hash mismatch')
        if type(ref.start_line) is not int or type(ref.end_line) is not int or not 1 <= ref.start_line <= ref.end_line <= max(1, len(data.splitlines())):
            raise ValueError('evidence line range invalid')
        return data.decode('utf-8')

    def _issue(self, code, message, *, check=None, refs=(), measurements=None, origin=None):
        checks, default_origin, recovery = ISSUE_SPECS[code]
        check = check or checks[0]
        origin = origin or default_origin or ('program_check' if check == 'framework_not_blocked' else 'provider_claim')
        verified = []
        for ref in refs:
            try:
                self.resolve(ref)
            except (ValueError, OSError, UnicodeError):
                continue
            if ref not in verified:
                verified.append(ref)
        return VerificationIssue(code, check, origin, message, tuple(verified), measurements or {}, recovery)

    def _integrity_ok(self, contract, candidate, run) -> bool:
        """The positive form of the compound integrity check, so it can be recorded.

        Same condition, same short-circuit as the negation it replaces: a run has to be
        bound to this candidate and contract, protected, cleanly reaped, fully probed,
        untruncated and exited of its own accord.
        """
        return diagnose_execution(contract, candidate, run)[0] is None

    def _bound(self, contract, candidate, run):
        return execution_bound(contract, candidate, run)

    async def evaluate(self, contract, candidate, executions, context):
        # The full ordered list goes on the span before any check runs, so a reader can
        # see which of them the program never reached rather than inferring it from the
        # ones that happen to be there.
        with span('verify', attributes={'checks': list(VERIFIER_CHECKS)}):
            return await self._evaluate(contract, candidate, executions, context)

    async def _evaluate(self, contract, candidate, executions, context):
        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped('CANCELLED')
        previous, self._semantic_approval = self._semantic_approval, None
        def verdict(classification, reason, refs=(), uncertainty=(), origin='program', next_action='explore', diagnostic=None, unmet=(), code=None, check=None, measurements=None, issues=()):
            # Controller treats this classification as terminal, for both program
            # and provider findings. Persist the same action in the verdict.
            if classification == CandidateClass.ENVIRONMENT_BLOCKED:
                next_action = 'stop'
            if code is not None:
                issues = (self._issue(code, reason, check=check, refs=refs, measurements=measurements), *issues)
            if issues:
                # Diagnostic evidence is freshly verified. Do not leave discarded,
                # altered references in the top-level failure feedback either.
                refs = tuple(dict.fromkeys(ref for item in issues for ref in item.evidence_refs))
            if issues and not unmet:
                unmet = tuple(dict.fromkeys(item.check for item in issues))
            for item in issues:
                mark('verification.issue', attributes={'diagnostic_code': item.code, 'check': item.check, 'result_code': 'FAIL',
                    'check_source': item.origin, 'candidate_id': candidate.candidate_id,
                    'contract_version': contract.version})
            mark('verdict', attributes={'result_code': classification.value if classification else 'UNCLASSIFIED',
                                        'reason_origin': origin})
            return Verdict(classification, reason, refs, uncertainties=uncertainty, candidate_id=candidate.candidate_id,
                manifest_hash=candidate.manifest_hash, contract_id=contract.contract_id, contract_version=contract.version,
                run_ids=tuple(run.run_id for run in executions), next_action=next_action,
                execution_diagnostic=diagnostic, unmet_checks=unmet, issues=issues)
        if not record_check('execution_evidence_present', bool(executions)):
            return verdict(None, 'no execution evidence', code='EXECUTION_EVIDENCE_MISSING')
        run = executions[-1]
        diagnostic, failed = diagnose_execution(contract, candidate, run)
        if not record_check('binding_and_integrity_ok', diagnostic is None):
            for check in failed:
                record_check(check, False)
            return verdict(CandidateClass.INVALID_CANDIDATE if diagnostic.code in INVALID_INTEGRITY_CODES else None,
                diagnostic_reason(diagnostic, failed), diagnostic=diagnostic, unmet=failed, code=diagnostic.code,
                measurements={key:value for key,value in asdict(diagnostic).items() if key != 'code'},
                next_action='explore' if diagnostic.code in INVALID_INTEGRITY_CODES else 'revise_candidate_or_request_information')
        checks = self.adapter.check_framework(candidate, run.observation)
        non_origin_errors = tuple(error for error in checks.invalid if 'origin' not in error and error != 'no collected tests')
        if not record_check('no_non_origin_framework_errors', not non_origin_errors):
            return verdict(CandidateClass.INVALID_CANDIDATE, '; '.join(non_origin_errors), run.observation.failure_refs,
                code='FRAMEWORK_CHECK_FAILED', measurements={'framework_errors':list(non_origin_errors[:16])})
        if not checks.blocked:
            record_check('framework_not_blocked', True)
        elif not run.observation.target_origins:
            record_check('framework_not_blocked', False)
            return verdict(CandidateClass.INVALID_CANDIDATE, 'candidate import references an unavailable module before target execution',
                run.observation.failure_refs, code='CANDIDATE_IMPORT_BLOCKED')
        else:
            # An imported target plus a later import error does not locate the fault:
            # the candidate may have invented a module, or a real dependency may be
            # missing. Defer ownership to the existing cited semantic review, rather
            # than pretending this signal proves an unavailable environment.
            mark('verification.attribution_required', attributes={'check': 'framework_not_blocked',
                'candidate_id': candidate.candidate_id, 'run_id': run.run_id,
                'framework_errors': list(checks.blocked[:16])})
        execution_valid = not checks.invalid and bool(run.observation.executed)
        collection_attribution = (bool(checks.blocked) and all(error == 'no collected tests' for error in checks.invalid)
                                  and not run.observation.executed
                                  and bool(run.observation.framework_details.get('collection_failures')))
        if not record_check('tests_executed_and_valid', execution_valid) and not collection_attribution:
            return verdict(CandidateClass.INVALID_CANDIDATE, '; '.join(checks.invalid) or 'no executed tests',
                run.observation.failure_refs, code='TEST_EXECUTION_INVALID',
                measurements={'framework_errors':list(checks.invalid[:16]), 'collected':bool(run.observation.collected), 'executed':bool(run.observation.executed)})
        if not record_check('candidate_reproduced', run.raw.exit_code != 0):
            return verdict(CandidateClass.NOT_REPRODUCED, 'candidate passes on original snapshot', code='CANDIDATE_NOT_REPRODUCED')
        if not record_check('expectation_is_grounded', bool(contract.expected) and bool(contract.sources)):
            return verdict(None, 'expected behavior lacks source', uncertainty=('expectation is ungrounded',), code='EXPECTATION_UNGROUNDED')
        # Hard checks use the complete record. Collection imports may receive a
        # diagnostic-only review to distinguish candidate setup from unavailable
        # environment; that review cannot override the failed execution check.
        def read_file(entry):
            data = safe_child(candidate.storage_root, entry.path).read_bytes()
            if bytes_hash(data) != entry.content_hash:
                raise CandidateFileChanged('candidate file hash changed')
            return data
        cap = context.budget.limits.tool_response_bytes
        def project(protocol_error=''):
            return build_review_context(contract, candidate, run, read_ref=self.resolve, read_file=read_file,
                max_bytes=cap, protocol_error=protocol_error, secrets=self.secrets)
        try:
            review = project()
        except CandidateFileChanged:
            return verdict(CandidateClass.INVALID_CANDIDATE, 'candidate file hash changed', code='CANDIDATE_HASH_MISMATCH', check='candidate_file_hash')
        except ReviewContextTooLarge as error:
            record_check('review_context_fits', False)
            mark('review.context', attributes={'output_bytes':error.needed_bytes, 'budget_bytes':error.budget_bytes, 'stage':error.stage})
            return verdict(None, _overflow_reason(error.needed_bytes, error.budget_bytes), uncertainty=('evidence omitted rather than silently truncated',), next_action='narrow_candidate', code='REVIEW_CONTEXT_TOO_LARGE',
                measurements={'needed_bytes':error.needed_bytes, 'budget_bytes':error.budget_bytes, 'stage':error.stage})
        except (ValueError, OSError, UnicodeError):
            return verdict(None, 'evidence source is unavailable or altered', code='EVIDENCE_UNAVAILABLE')
        sources = tuple(EvidenceRef(s.path, s.content_hash, s.start_line, s.end_line) for s in contract.sources)
        record_check('review_context_fits', True)
        mark('review.context', attributes={'input_bytes':review.input_bytes, 'output_bytes':review.output_bytes,
            'budget_bytes':cap, 'omitted_fields':list(review.omitted_fields)})
        prompt = files('reproagent').joinpath('prompts/review_evidence.md').read_text(encoding='utf-8')
        key = (review_key(contract, candidate, run, review, prompt, self.gateway,
                          byte_limit=cap, secrets=self.secrets) if execution_valid else None)
        if previous is not None and key is not None and previous.key == key and previous.run_id != run.run_id:
            try:
                for ref in previous.verdict.evidence_refs:
                    self.resolve(ref)
                refs = rebound_refs(previous, review)
                for ref in refs:
                    self.resolve(ref)
                if not (any(ref in sources for ref in refs)
                        and any(ref in run.observation.failure_refs for ref in refs)):
                    raise ValueError('reused citations do not cover this execution')
                context.budget.check()
                if context.cancel_event.is_set():
                    raise BudgetStopped('CANCELLED')
                audit = {'first_run_id': previous.run_id, 'run_id': run.run_id,
                    'candidate_id': candidate.candidate_id, 'material_key': key,
                    'basis_verdict_hash': canonical_hash(asdict(previous.verdict)),
                    'review_origin': 'reused_provider_claim', 'new_model_call': False}
                self.store.append_event('verification.semantic_reused',
                    tuple('runs/' + rid + '/execution.json' for rid in (previous.run_id, run.run_id)), audit)
                context.budget.check()
                if context.cancel_event.is_set():
                    raise BudgetStopped('CANCELLED')
            except (OSError, ValueError, KeyError, RuntimeError):
                # No unverifiable approval or unaudited reuse becomes success.
                pass
            else:
                mark('verification.semantic_reused', attributes=audit)
                record_check('candidate_assertions_supported', True, source='reused_provider_claim')
                record_check('provider_semantic_claims', True, source='reused_provider_claim')
                record_check('citations_cover_expectation_and_failure', True)
                return verdict(CandidateClass.REPRODUCED,
                    '独立重跑的完整审查材料等价；复用 ' + previous.run_id + ' 的语义审查，未再次调用模型。',
                    refs, origin='reused_provider_claim')
        error = ''
        protocol_code = 'MODEL_PROTOCOL_ERROR'
        for attempt in range(3):
            context.budget.check()
            if context.cancel_event.is_set():
                raise BudgetStopped('CANCELLED')
            text = canonical_bytes(review.payload).decode()
            for secret in self.secrets:
                text = text.replace(secret, '[REDACTED]')
            size = len(text.encode())
            if size > cap:
                record_check('review_context_fits', False)
                mark('review.context', attributes={'output_bytes':size, 'budget_bytes':cap, 'stage':'redaction'})
                return verdict(None, _overflow_reason(size, cap), uncertainty=('evidence omitted rather than silently truncated',), next_action='narrow_candidate', code='REVIEW_CONTEXT_TOO_LARGE',
                    measurements={'needed_bytes':size, 'budget_bytes':cap, 'stage':'redaction'})
            try:
                response = await self.gateway.complete(ModelRequest(({'role':'system','content':prompt}, {'role':'user','content':text}), 'verdict'), context)
            except ModelOutputError as exc:
                if not exc.retryable:
                    # The provider already said why the answer is unusable; a correction
                    # request would be the same request again, so it ends the call here.
                    raise
                error = str(exc)
                protocol_code = 'MODEL_PROTOCOL_ERROR'
            else:
                context.budget.check()
                if context.cancel_event.is_set():
                    raise BudgetStopped('CANCELLED')
                try:
                    result = parse_json(response.text)
                except ValueError as exc:
                    # Ambiguous/unparseable JSON has no trustworthy object from
                    # which to retain negative claims. A later yes must not erase
                    # an earlier no hidden behind a duplicate key or syntax error.
                    return verdict(None, f'semantic verdict invalid JSON ({type(exc).__name__}); no correction request',
                        uncertainty=('MODEL_PROTOCOL_ERROR',), code='MODEL_PROTOCOL_ERROR',
                        measurements={'attempts':attempt + 1, 'protocol_error':type(exc).__name__})
                try:
                    sites = {site['id']: site for site in review.payload['assertion_sites']}
                    validate_verdict(result, tuple(sites))
                    classification = CandidateClass(result['classification']) if result['classification'] else None
                    refs = tuple(EvidenceRef(**ref) for ref in result['evidence_refs'])
                    if any(ref not in review.available_refs for ref in refs):
                        raise ValueError('unknown evidence citation; copy exact available_refs')
                    for ref in refs:
                        self.resolve(ref)
                    for row in result['assertion_reviews']:
                        if any(EvidenceRef(**ref) not in sources for ref in row['evidence_refs']):
                            raise ValueError('assertion_reviews may cite only supplied expectation sources, not observed failures')
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    rows = result.get('assertion_reviews', [])
                    negative_audit = isinstance(rows, list) and any(isinstance(row, dict) and row.get('supported') is False for row in rows)
                    if negative_audit or any(result.get(key) is False for key in ('expected_assertion','target_triggered','failure_matches_issue')):
                        return verdict(None, 'negative semantic check in an invalid verdict; correction cannot promote it to success',
                            code='MODEL_PROTOCOL_ERROR', measurements={'attempts':attempt + 1, 'protocol_error':'invalid_negative_response'})
                    error = str(exc)
                    protocol_code = 'MODEL_PROTOCOL_ERROR'
                else:
                    unsupported = [row for row in result['assertion_reviews'] if not row['supported']]
                    if not record_check('candidate_assertions_supported', not unsupported, source='provider_claim'):
                        notes = []
                        for row in unsupported:
                            site = sites[row['assertion_id']]
                            notes.append(f"{site['path']}:{site['start_line']}: {row['reason']}")
                        return verdict(CandidateClass.INVALID_CANDIDATE,
                            'unsupported candidate assertion(s): ' + '; '.join(notes), refs,
                            origin='provider', unmet=('candidate_assertions_supported',), code='CANDIDATE_ASSERTIONS_UNCONFIRMED',
                            measurements={'assertion_ids':[row['assertion_id'] for row in unsupported[:16]],
                                'omitted_assertions':max(0,len(unsupported)-16)})
                    semantic_issues = tuple(self._issue(code, result['reason'], check=key, refs=refs)
                        for key,code in (('expected_assertion','EXPECTED_ASSERTION_UNCONFIRMED'),
                            ('target_triggered','TARGET_TRIGGER_UNCONFIRMED'),
                            ('failure_matches_issue','ISSUE_FAILURE_MATCH_UNCONFIRMED')) if not result[key])
                    for item in semantic_issues:
                        record_check(item.check, False, source='provider_claim')
                    if not execution_valid:
                        # A semantic diagnosis may stop an unavailable environment,
                        # but even an all-true REPRODUCED answer cannot accept a test
                        # that never executed, or reuse a prior success approval.
                        if classification == CandidateClass.ENVIRONMENT_BLOCKED:
                            return verdict(classification, result['reason'], refs, origin='provider',
                                code='ENVIRONMENT_BLOCKED', check='provider_semantic_claims', issues=semantic_issues)
                        return verdict(CandidateClass.INVALID_CANDIDATE,
                            'no executed tests; diagnostic reviewer: ' + result['reason'], refs,
                            code='TEST_EXECUTION_INVALID', issues=semantic_issues,
                            measurements={'framework_errors':list(checks.invalid[:16]),
                                'collected':bool(run.observation.collected), 'executed':False})
                    if classification == CandidateClass.REPRODUCED:
                        # The three booleans are the model's own claim, and are recorded
                        # as a claim -- they are not the program accepting anything.
                        claims = record_check('provider_semantic_claims',
                                              all(result[key] for key in ('expected_assertion', 'target_triggered',
                                                                          'failure_matches_issue')),
                                              source='provider_claim')
                        if not claims:
                            return verdict(None, 'semantic reproduction checks are not all true', refs, issues=semantic_issues)
                        # ...and this one is the program's own check that the citations
                        # cover both the expectation and the observed failure.
                        cites_both = (any(ref in sources for ref in refs)
                                      and any(ref in run.observation.failure_refs for ref in refs))
                        if not record_check('citations_cover_expectation_and_failure', cites_both):
                            error = 'REPRODUCED requires citations to both expectation source and actual failure'
                            protocol_code = 'SEMANTIC_CITATIONS_INCOMPLETE'
                        else:
                            approved = verdict(classification, result['reason'], refs, origin='provider')
                            if key is not None and attempt == 0:
                                self._semantic_approval = SemanticApproval(key, run.run_id, approved, review.available_refs)
                            return approved
                    else:
                        if not semantic_issues:
                            code = 'ENVIRONMENT_BLOCKED' if classification == CandidateClass.ENVIRONMENT_BLOCKED else 'SEMANTIC_REPRODUCTION_UNCONFIRMED'
                            semantic_issues = (self._issue(code, result['reason'], check='provider_semantic_claims', refs=refs, origin='provider_claim'),)
                        return verdict(classification, result['reason'], refs, origin='provider', issues=semantic_issues)
            # A correction re-measures the whole payload, protocol error included: the
            # request that is actually sent has to stay inside the same budget.
            try:
                review = project(error[:512])
            except CandidateFileChanged:
                return verdict(CandidateClass.INVALID_CANDIDATE, 'candidate file hash changed', code='CANDIDATE_HASH_MISMATCH', check='candidate_file_hash')
            except ReviewContextTooLarge as error:
                record_check('review_context_fits', False)
                mark('review.context', attributes={'output_bytes':error.needed_bytes, 'budget_bytes':error.budget_bytes,
                    'stage':'protocol_correction', 'reason_code':'MODEL_PROTOCOL_ERROR'})
                # The initial evidence fit. A failed answer's correction envelope
                # is not proof that the candidate can never be reviewed.
                return verdict(None, f'semantic verdict invalid: correction context needs {error.needed_bytes} bytes; '
                    f'budget is {error.budget_bytes} bytes; initial evidence fit', uncertainty=('MODEL_PROTOCOL_ERROR',),
                    code='MODEL_PROTOCOL_ERROR', measurements={'attempts':attempt + 1, 'needed_bytes':error.needed_bytes,
                        'budget_bytes':error.budget_bytes, 'stage':'protocol_correction', 'protocol_error':protocol_code})
            except (ValueError, OSError, UnicodeError):
                return verdict(None, 'evidence source is unavailable or altered', code='EVIDENCE_UNAVAILABLE')
        extra = ()
        if protocol_code == 'SEMANTIC_CITATIONS_INCOMPLETE':
            extra = (self._issue(protocol_code, 'Both expectation and failure citations are required.'),)
        return verdict(None, 'semantic verdict invalid after 3 attempts: ' + error[:512], uncertainty=('MODEL_PROTOCOL_ERROR',),
            code='MODEL_PROTOCOL_ERROR', measurements={'attempts':3, 'protocol_error':protocol_code}, issues=extra)

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
            from .failure_signature import stable_failure_message, stable_pytester_message
            roots = [(spelling, RUN_ROOT) for spelling in
                     _run_roots(run.observation.framework_details.get('run_root', ''))]
            temp_root = run.observation.framework_details.get('temp_root', '')
            if temp_root:
                roots.extend((spelling, RUN_ROOT + '/tmp') for spelling in _root_spellings(temp_root))
            roots.sort(key=lambda item: len(item[0]), reverse=True)
            def normalized(text):
                for spelling, replacement in roots:
                    text = text.replace(spelling, replacement)
                return text
            result = []
            for phase in run.observation.tests:
                if phase.get('outcome') == 'failed':
                    crash = dict(phase.get('crash', {}))
                    crash['path'] = normalized(str(crash.get('path', '')))
                    message = stable_pytester_message(str(crash.get('message', '')), phase, run.observation.target_origins)
                    if message != str(crash.get('message', '')):
                        mark('replay.metadata_normalized', attributes={'run_id': run.run_id,
                            'field': 'crash.message', 'rule': 'pytest_line_matcher_summary_elapsed'})
                    crash['message'] = stable_failure_message(normalized(message))
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
