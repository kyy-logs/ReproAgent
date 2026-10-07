"""The product's business state machine, driven by exploration phases.

The Controller owns the reproduction workflow and nothing else does.  It freezes the
original snapshot, prepares the target environment, asks the strategy for the contract
the task works from, and then runs exploration phases.  A phase's result is a fact about
the work, not a suggestion: a published candidate is immediately executed on the original
version, semantically verified, and -- when it reproduced -- repeated independently, all
without asking anyone whether to run or to submit.  A revision request re-derives the
contract from the sources the phase cited and invalidates everything the previous contract
version had produced.  Missing information and a phase that produced nothing end the task
with their own diagnostic.

Nothing here reads a model's narration: the SDK's natural-language ending is not a
success, and a candidate only becomes evidence by executing and being confirmed.  The
Controller's own business steps (executing, repeating, revising) are recorded as
``action.*`` events with a bounded name, a hash and a fixed result code; the exploration
phases themselves are recorded by the runtime that ran them.
"""
import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field, replace

from .budget import BudgetStopped
from .models import (AgentContext, CandidateClass, EnvironmentSnapshot, EvidenceContext, EvidenceLevel,
                     IssueDescription, ProjectView, SourceRef, TaskRequest, TaskResult, TaskState)
from .serialization import bytes_hash, canonical_hash
from .protocol import ModelOutputError, ModelProtocolError
from reproagent.paths import relative_name
from reproagent.store import atomic_write

# Every Controller business step ends in one of these codes; none of them carries model or
# provider text.  Each names a distinct failure class, so an interruption is never confused
# with a rejected argument, a model failure or an internal error.
ACTION_RESULT_CODES = frozenset({'OK', 'INVALID_ARGUMENT', 'MISSING_FILE', 'INVALID_ENCODING', 'UNKNOWN_CANDIDATE',
    'STALE_CANDIDATE', 'CANDIDATE_NOT_REPRODUCED', 'CONTRACT_INCOMPLETE', 'UNAUTHORIZED_EVIDENCE_REF',
    'REVISION_BUDGET_EXCEEDED', 'REPLAY_UNCONFIRMED', 'INTERRUPTED', 'CLEANUP_FAILED', 'MODEL_OUTPUT_ERROR',
    'MODEL_PROTOCOL_ERROR', 'INTERNAL_ERROR'})

# What the Controller made of one phase result, in the same closed vocabulary: the phase's
# own kind plus the outcome it led to, never the text a model or a provider produced.
PHASE_RESULT_CODES = ACTION_RESULT_CODES | {classification.value for classification in CandidateClass} | {
    'MISSING_INFORMATION', 'NO_CANDIDATE', 'REVISION_REQUESTED'}


class CleanupFailed(Exception):
    def __init__(self, reason): self.reason = reason


class ActionRejected(ValueError):
    """A Controller business step failed its own preconditions; the event records only the code."""
    def __init__(self, code, message):
        if code not in ACTION_RESULT_CODES or code == 'OK':
            raise ValueError(f'unknown action result code: {code}')
        super().__init__(message)
        self.code = code


def rejection_code(error):
    if isinstance(error, ActionRejected): return error.code
    if isinstance(error, FileNotFoundError): return 'MISSING_FILE'
    if isinstance(error, UnicodeError): return 'INVALID_ENCODING'
    return 'INVALID_ARGUMENT'


@dataclass
class Lifecycle:
    """The execution facts of one task, carried across its business steps.

    The snapshot and environment are the frozen original version's; ``executions`` and
    ``verdicts`` are what the task has observed so far, and a contract revision clears
    them because evidence from another contract version says nothing about this one.
    """

    request: TaskRequest
    fixed: object
    snapshot: object
    environment: EnvironmentSnapshot
    executions: dict = field(default_factory=dict)
    verdicts: dict = field(default_factory=dict)


class Step:
    """One Controller business step: opened once, ended once, with a fixed code.

    The body either finishes (``OK``), refuses itself through :meth:`rejected`, or raises;
    in every case exactly one terminal event is written, and a raised error keeps the
    classification the Controller's own handlers use.
    """

    def __init__(self, controller, name, parameters, context, contract):
        self.controller = controller
        self.code = 'OK'
        self.selected = controller.action_start(name, parameters, context, contract)
        self.started = time.monotonic()

    def rejected(self, code):
        """Record that this step failed its own preconditions, with the code that names it."""
        if code not in ACTION_RESULT_CODES or code == 'OK':
            raise ValueError(f'unknown action result code: {code}')
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if error is not None:
            if isinstance(error, (BudgetStopped, asyncio.CancelledError, TimeoutError)):
                self.code = 'INTERRUPTED'
            elif isinstance(error, ModelOutputError):
                self.code = 'MODEL_OUTPUT_ERROR'
            elif isinstance(error, ModelProtocolError):
                self.code = 'MODEL_PROTOCOL_ERROR'
            elif isinstance(error, CleanupFailed):
                self.code = 'CLEANUP_FAILED'
            elif isinstance(error, (ValueError, FileNotFoundError, UnicodeError)):
                self.code = rejection_code(error)
            else:
                self.code = 'INTERNAL_ERROR'
        self.controller.action_end(self.selected, self.started, self.code)
        return False


class Controller:
    """Runs one reproduction task from a phase-driven exploration strategy.

    Args:
        store: the task store every record and event is written to.
        workspace: the product's snapshot, candidate and run workspace.
        runner: the target environment and execution backend.
        gateway: the structured-request boundary (contract and verdict) of the task.
        verifier: the hard checks and the semantic review of one execution.
        exporter: the delivery step; it runs once the task reached its own conclusion.
        secrets: strings that must never appear in a stored event or feedback text.
        explorer_factory: builds the task's :class:`~reproagent.core.ports.Explorer` from
            the gateway, the call context, the configured test area and this task's
            project view and workspace.
        backend_info: what the ``backend.selected`` event reports about the components.
    """

    def __init__(self, store, workspace, runner, gateway, verifier, exporter, secrets=(), explorer_factory=None, backend_info=None):
        if explorer_factory is None:
            raise ValueError('a Controller drives the product strategy: pass explorer_factory')
        self.store, self.workspace, self.runner = store, workspace, runner
        self.gateway, self.verifier, self.exporter = gateway, verifier, exporter
        self.secrets = tuple(secret for secret in secrets if secret)
        self.explorer_factory = explorer_factory
        self.backend_info = backend_info or {'model_backend':'native', 'agent_backend':'native'}
        self.explorer = None
        self._explorer_closed = False

    def state(self, result, status, **changes):
        result = replace(result, status=status, **changes)
        self.store.save_record('task', result.task_id, result)
        self.store.append_event('task.state', (), {'status':status.value, 'evidence_level':result.evidence_level.value})
        return result

    def recorded(self, result):
        """The task state the store holds, which is the one an interrupted step last wrote.

        The business steps run in their own methods, so the state a caller still holds can
        lag behind an execution or a verification that already happened; every transition
        is written to the store as it happens, and that record is the current one.
        """
        try:
            return self.store.load_record('task', 'task')
        except (ValueError, FileNotFoundError):
            return result

    def check_execution(self, run):
        if not run.raw.cleanup_ok:
            raise CleanupFailed(run.raw.stop_reason)
        if run.raw.stop_reason in ('CANCELLED', 'EXHAUSTED'):
            raise BudgetStopped(run.raw.stop_reason)

    def feedback(self, data):
        text = json.dumps(data, ensure_ascii=False, default=str)
        for secret in self.secrets:
            text = text.replace(secret, '[REDACTED]')
        return text.encode()[:8192].decode(errors='ignore')

    def steps_remaining(self, context):
        return max(0, context.budget.limits.agent_steps - context.budget.steps_used)

    def action_start(self, name, parameters, context, contract):
        """Record the step under a bounded name, a hash and the remaining budget only."""
        selected = {'action':name, 'parameters_hash':canonical_hash(parameters),
                    'steps_remaining':self.steps_remaining(context), 'contract_version':contract.version}
        self.store.append_event('action.selected', (), selected)
        return selected

    def action_end(self, selected, started, code):
        if code not in ACTION_RESULT_CODES: raise ValueError(f'unknown action result code: {code}')
        self.store.append_event('action.completed' if code == 'OK' else 'action.rejected', (),
            {**selected, 'result_code':code, 'duration':time.monotonic() - started})

    def phase_end(self, phase, code, context):
        """Record what one phase result led to: its kind and a fixed outcome code."""
        if code not in PHASE_RESULT_CODES: raise ValueError(f'unknown phase result code: {code}')
        self.store.append_event('phase.result', (),
            {'kind':phase.kind, 'result_code':code, 'steps_remaining':self.steps_remaining(context)})

    def phase_error(self, code, context):
        """Record a phase that failed before it had a result, with its own fixed code."""
        if code not in ACTION_RESULT_CODES: raise ValueError(f'unknown action result code: {code}')
        self.store.append_event('phase.error', (), {'result_code':code, 'steps_remaining':self.steps_remaining(context)})

    # =======================================================================
    # The business steps a phase result leads to
    # =======================================================================

    def published_candidate(self, candidate_id):
        """The candidate record the task store holds under *candidate_id*.

        Raises:
            ActionRejected: the id is not one this task published, so no phase can report
                a candidate that only exists in its own answer.
        """
        try:
            path = self.store.record_path('candidates', candidate_id)
        except ValueError:
            path = None
        if path is None or not path.exists():
            raise ActionRejected('UNKNOWN_CANDIDATE', 'the phase reported a candidate the task store does not hold')
        return self.store.load_record('candidates', candidate_id)

    async def confirm_candidate(self, lifecycle, candidate, contract, context, result):
        """Execute *candidate*, verify it and repeat it when it reproduced.

        Returns:
            `tuple`: ``(result, feedback, code, done)``.  ``feedback`` is what the next
            phase learns from this one, ``code`` is the fixed outcome the phase history
            records, and ``done`` says the task reached its own conclusion (an environment
            block, or a confirmed reproduction) and the loop must stop.
        """
        parameters = {'candidate_id':candidate.candidate_id, 'manifest_hash':candidate.manifest_hash,
                      'contract_version':contract.version}
        with Step(self, 'run_candidate', parameters, context, contract) as step:
            try:
                result = self.state(result, TaskState.EXECUTING)
                run = await self.runner.execute(candidate, lifecycle.snapshot, lifecycle.environment, context)
                self.check_execution(run)
                result = self.state(result, TaskState.VERIFYING)
                verdict = await self.verifier.evaluate(contract, candidate, (run,), context)
            except ModelOutputError as exc:
                step.rejected('MODEL_OUTPUT_ERROR')
                return result, self.feedback({'error':str(exc)}), 'MODEL_OUTPUT_ERROR', False
            except (ValueError, FileNotFoundError, UnicodeError) as exc:
                code = rejection_code(exc)
                step.rejected(code)
                return result, self.feedback({'error':str(exc)}), code, False
            self.store.save_record('verdicts', 'verdict-' + run.run_id, verdict)
            lifecycle.executions[candidate.candidate_id], lifecycle.verdicts[candidate.candidate_id] = run, verdict
            if verdict.classification == CandidateClass.ENVIRONMENT_BLOCKED:
                result = self.state(result, TaskState.BLOCKED, stop_reason='ENVIRONMENT_BLOCKED',
                                    uncertainties=(*result.uncertainties, verdict.reason))
                return result, self.feedback(asdict(verdict)), 'ENVIRONMENT_BLOCKED', True
            if verdict.classification != CandidateClass.REPRODUCED:
                return result, self.feedback(asdict(verdict)), str(verdict.classification or 'NOT_REPRODUCED'), False
            result = self.state(result, TaskState.VERIFYING, evidence_level=EvidenceLevel.SINGLE_OBSERVATION,
                                accepted_candidate_id=candidate.candidate_id)

        with Step(self, 'submit_candidate', parameters, context, contract) as step:
            try:
                result = self.state(result, TaskState.REPLAYING)
                second = await self.runner.execute(candidate, lifecycle.snapshot, lifecycle.environment, context)
                self.check_execution(second)
                second_verdict = await self.verifier.evaluate(contract, candidate, (second,), context)
            except ModelOutputError as exc:
                step.rejected('MODEL_OUTPUT_ERROR')
                return result, self.feedback({'error':str(exc)}), 'MODEL_OUTPUT_ERROR', False
            except (ValueError, FileNotFoundError, UnicodeError) as exc:
                code = rejection_code(exc)
                step.rejected(code)
                return result, self.feedback({'error':str(exc)}), code, False
            self.store.save_record('verdicts', 'verdict-' + second.run_id, second_verdict)
            if not self.verifier.confirm(contract, candidate, run, second, (verdict, second_verdict)):
                step.rejected('REPLAY_UNCONFIRMED')
                return (result, 'Independent replay did not confirm the same failure; revise or request information.',
                        'REPLAY_UNCONFIRMED', False)
            result = self.state(result, TaskState.REPLAYING, evidence_level=EvidenceLevel.REPEATED_OBSERVATION,
                                accepted_candidate_id=candidate.candidate_id)
            result = await self.validate_fixed_version(lifecycle, candidate, run, contract, context, result)
            context.budget.check()
            result = self.state(result, TaskState.EXPORTING)
        return result, self.feedback({'status':'confirmed', 'evidence_level':result.evidence_level.value}), 'REPRODUCED', True

    async def validate_fixed_version(self, lifecycle, candidate, first, contract, context, result):
        """Check the supplied fixed version with the same candidate, or record why not.

        No repair path, repository or interpreter ever reaches the exploration context:
        the fixed version is prepared and executed here, and only its outcome is recorded.
        """
        fixed = lifecycle.fixed
        if not fixed:
            return result
        fixed_request = replace(lifecycle.request, repo=fixed.repo,
                                language=replace(lifecycle.request.language, python=fixed.python or lifecycle.request.language.python))
        try:
            fixed_snapshot = self.workspace.freeze(fixed_request, context)
            fixed_environment = await self.runner.prepare(fixed_request, fixed_snapshot, context)
            fixed_run = await self.runner.execute(candidate, fixed_snapshot, fixed_environment, context, execution_role='fixed')
            self.check_execution(fixed_run)
            checks = self.runner.adapter.check_framework(candidate, fixed_run.observation)
            same_nodes = set(fixed_run.observation.framework_details.get('completed_nodeids', [])) == set(first.observation.framework_details.get('completed_nodeids', []))
            passed = same_nodes and fixed_run.raw.exit_code == 0 and fixed_run.raw.stop_reason == 'EXITED' and fixed_run.protection.ok and fixed_run.observation.executed and fixed_run.observation.probe_complete and not checks.invalid and not checks.blocked
        except (ValueError, OSError):
            # No fixed-version verdict exists, so no differential claim is made.
            return replace(result, fix_validation_status='blocked',
                           uncertainties=(*result.uncertainties, 'Fixed-version environment/fixtures unavailable.'))
        if passed:
            return self.state(result, TaskState.REPLAYING, evidence_level=EvidenceLevel.DIFFERENTIAL_VALIDATED,
                              fix_validation_status='passed')
        # The fixed version ran the same candidate and did not pass: only the original
        # repeated failure is confirmed.
        return replace(result, fix_validation_status='failed',
                       uncertainties=(*result.uncertainties, 'Fixed-version validation failed or was incompatible.'))

    async def revise_contract(self, phase, lifecycle, contract, source, issue_text, issue_hash, context, result):
        """Re-derive the contract from the sources the phase cited, and invalidate the old one.

        Returns:
            `tuple`: ``(contract, result, feedback, code)``.  The contract is the previous
            one when the revision was refused and the new version otherwise; either way the
            next phase is told what happened, and ``code`` is the outcome it records.
        """
        parameters = {'source_refs':[asdict(ref) for ref in phase.source_refs],
                      'reason_hash':canonical_hash(phase.reason)}
        originals = {relative_name(lifecycle.snapshot.root / entry.path, self.store.root): entry.content_hash
                     for entry in lifecycle.snapshot.files}
        with Step(self, 'revise_contract', parameters, context, contract) as step:
            try:
                for ref in phase.source_refs:
                    self.cite_original(ref, originals)
                sources = [source, *(item for item in contract.sources if item != source)]
                sources.extend(SourceRef(ref.path, ref.content_hash, ref.start_line, ref.end_line, 'repository')
                               for ref in phase.source_refs)
                texts = tuple('\n'.join(self.verifier.resolve(item).splitlines()[item.start_line - 1:item.end_line])
                              for item in sources)
                if sum(len(text.encode()) for text in texts) > context.budget.limits.tool_response_bytes:
                    raise ActionRejected('REVISION_BUDGET_EXCEEDED', 'revision sources exceed context budget; narrow line ranges')
                revised = await self.explorer.analyze(IssueDescription(issue_text, issue_hash), EvidenceContext(tuple(sources), texts, contract))
            except ActionRejected as exc:
                step.rejected(exc.code)
                return contract, result, self.feedback({'error':str(exc)}), exc.code
            except ModelOutputError as exc:
                step.rejected('MODEL_OUTPUT_ERROR')
                return contract, result, self.feedback({'error':str(exc)}), 'MODEL_OUTPUT_ERROR'
            except (ValueError, FileNotFoundError, UnicodeError) as exc:
                code = rejection_code(exc)
                step.rejected(code)
                return contract, result, self.feedback({'error':str(exc)}), code
            revised = replace(revised, revision_reason=phase.reason)
            for ref in revised.sources:
                self.verifier.resolve(ref)
            self.store.save_contract(revised)
            # The new contract version has observed nothing: evidence from another version
            # cannot be carried into it, and no accepted candidate survives the revision.
            lifecycle.executions.clear(); lifecycle.verdicts.clear()
            result = self.state(result, TaskState.GENERATING, evidence_level=EvidenceLevel.NONE, accepted_candidate_id='')
            feedback = self.feedback({'contract_version':revised.version, 'missing_information':revised.missing_information})
        return revised, result, feedback, 'REVISION_REQUESTED'

    def cite_original(self, ref, originals):
        """Refuse a citation that is not verified original evidence of this task.

        The frozen snapshot decides what may be cited, not the store: a candidate the phase
        wrote, an artifact of the run or anything else the task produced is not an original
        source, however well its path resolves.  The reference is then resolved again, so
        the citation is the frozen file it claims to be.

        Args:
            ref: the citation a phase asked for.
            originals: the store-relative path and frozen hash of every registered original.

        Raises:
            ActionRejected: the reference is not a registered original, or no longer
                resolves to it, so a phase cannot widen its own revision with invented
                or self-written sources.
        """
        if originals.get(ref.path) != ref.content_hash:
            raise ActionRejected('UNAUTHORIZED_EVIDENCE_REF',
                                 'a revision may cite only original project evidence a tool displayed')
        try:
            self.verifier.resolve(ref)
        except (ValueError, FileNotFoundError, UnicodeError) as exc:
            raise ActionRejected('UNAUTHORIZED_EVIDENCE_REF',
                                 'a revision may cite only original project evidence a tool displayed') from exc

    # =======================================================================
    # The task
    # =======================================================================

    async def run(self, request, context, fixed=None):
        started = time.monotonic()
        result = TaskResult(request.task_id or 'task-' + uuid.uuid4().hex, TaskState.PREPARING)
        if (self.store.root / 'request.json').exists():
            raise ValueError('task directory already used; choose a new output_dir')
        self.store.save_record('request', result.task_id, request)
        self.store.append_event('backend.selected', (), self.backend_info)
        result = self.state(result, TaskState.PREPARING)
        try:
            snapshot = self.workspace.freeze(request, context)
            environment = await self.runner.prepare(request, snapshot, context)
            context.budget.check()
            result = self.state(result, TaskState.ANALYZING)
            issue_bytes = request.issue_file.read_bytes()
            if len(issue_bytes) > context.budget.limits.tool_response_bytes:
                raise ValueError('issue description exceeds input budget')
            atomic_write(self.store.root / 'input/issue.md', issue_bytes)
            issue_text = issue_bytes.decode('utf-8')
            issue_hash = bytes_hash(issue_bytes)
            source = SourceRef('input/issue.md', issue_hash, 1, max(1, len(issue_bytes.splitlines())))
            project = ProjectView(snapshot)
            self.explorer = self.explorer_factory(self.gateway, context, request.language.candidate_parent,
                                                  project=project, workspace=self.workspace)
            contract = await self.explorer.analyze(IssueDescription(issue_text, issue_hash),
                                                   EvidenceContext((source,), (issue_text,)))
            for evidence in contract.sources:
                self.verifier.resolve(evidence)
            self.store.save_contract(contract)
            lifecycle = Lifecycle(request, fixed, snapshot, environment)
            feedback = self.feedback({'missing_information':contract.missing_information}) if contract.missing_information else ''
            history = []
            while True:
                context.budget.check()
                if context.cancel_event.is_set(): raise BudgetStopped('CANCELLED')
                result = self.state(result, TaskState.GENERATING)
                try:
                    phase = await self.explorer.explore(AgentContext(contract, project, tuple(history[-4:]), feedback))
                except BudgetStopped:
                    raise
                except ModelProtocolError:
                    # The phase had its bounded correction attempts and produced nothing
                    # usable: this is a model failure, not a request for user information.
                    self.phase_error('MODEL_PROTOCOL_ERROR', context)
                    raise
                except ModelOutputError as exc:
                    self.phase_error('MODEL_OUTPUT_ERROR', context)
                    feedback = self.feedback({'error':str(exc)})
                    history.append({'phase':'error', 'result_code':'MODEL_OUTPUT_ERROR'})
                    continue
                except (ValueError, FileNotFoundError, UnicodeError) as exc:
                    # No phase was selected, so this is a protocol failure rather than a rejection.
                    code = rejection_code(exc)
                    self.phase_error(code, context)
                    feedback = self.feedback({'error':str(exc)})
                    history.append({'phase':'error', 'result_code':code})
                    continue
                if phase.kind == 'request_information':
                    self.phase_end(phase, 'MISSING_INFORMATION', context)
                    result = self.state(result, TaskState.NEEDS_INFORMATION, stop_reason='MISSING_INFORMATION',
                                        uncertainties=(*result.uncertainties, phase.question))
                    break
                if phase.kind == 'no_candidate':
                    self.phase_end(phase, 'NO_CANDIDATE', context)
                    result = self.state(result, TaskState.NEEDS_INFORMATION, stop_reason='NO_CANDIDATE',
                                        uncertainties=(*result.uncertainties, phase.reason))
                    break
                if phase.kind == 'revise_contract':
                    contract, result, feedback, code = await self.revise_contract(phase, lifecycle, contract, source,
                                                                                 issue_text, issue_hash, context, result)
                    self.phase_end(phase, code, context)
                    history.clear()
                    continue
                try:
                    candidate = self.published_candidate(phase.candidate_id)
                    if (candidate.contract_id, candidate.contract_version) != (contract.contract_id, contract.version):
                        raise ActionRejected('STALE_CANDIDATE',
                            'the candidate was published against another contract version, so this version cannot accept it')
                except ActionRejected as exc:
                    self.phase_end(phase, exc.code, context)
                    feedback = self.feedback({'error':str(exc)})
                    history.append({'phase':'candidate', 'result_code':exc.code})
                    continue
                project = replace(project, candidates=(*project.candidates, candidate))
                result, feedback, code, done = await self.confirm_candidate(lifecycle, candidate, contract, context, result)
                self.phase_end(phase, code, context)
                history.append({'phase':'candidate', 'candidate_id':candidate.candidate_id, 'result_code':code})
                if done:
                    break
        except CleanupFailed as exc:
            result = self.recorded(result)
            result = self.state(result, TaskState.FAILED, stop_reason=exc.reason, uncertainties=(*result.uncertainties, 'Process cleanup failed; directories retained.'))
        except BudgetStopped as exc:
            result = self.recorded(result)
            terminal = TaskState.CANCELLED if exc.reason == 'CANCELLED' else TaskState.NEEDS_INFORMATION if exc.reason == 'NEEDS_INFORMATION' else TaskState.EXHAUSTED
            result = self.state(result, terminal, stop_reason=exc.reason, uncertainties=(*result.uncertainties, str(exc)))
        except asyncio.CancelledError:
            result = self.recorded(result)
            context.cancel_event.set(); context.budget.cancel()
            result = self.state(result, TaskState.CANCELLED, stop_reason='CANCELLED')
        except ModelProtocolError as exc:
            result = self.recorded(result)
            result = self.state(result, TaskState.FAILED, stop_reason='MODEL_PROTOCOL_ERROR', uncertainties=(self.feedback(str(exc)),))
        except (ValueError, OSError) as exc:
            result = self.recorded(result)
            status = TaskState.BLOCKED if result.status == TaskState.PREPARING else TaskState.NEEDS_INFORMATION
            result = self.state(result, status, stop_reason='ENVIRONMENT_BLOCKED' if status == TaskState.BLOCKED else 'INVALID_INPUT', uncertainties=(self.feedback(str(exc)),))
        except Exception as exc:
            result = self.recorded(result)
            result = self.state(result, TaskState.FAILED, stop_reason='INTERNAL_ERROR', uncertainties=(self.feedback(str(exc)),))
        finally:
            # The task's exploration session is closed before anything is delivered, so no
            # phase, tool call or SDK reply outlives the run however the run ended.
            await self.close_explorer()
        try:
            manifest = self.exporter.export(result, self.store, context)
            self.store.append_event('export.completed', ('artifacts/' + manifest.package_kind + '/manifest.json',), {'manifest_hash':manifest.manifest_hash})
            final_status = TaskState.DONE if result.status == TaskState.EXPORTING else result.status
            if final_status == TaskState.DONE:
                context.budget.check()
                if context.cancel_event.is_set(): raise BudgetStopped('CANCELLED')
            result = self.state(result, final_status, export_state='published', duration=time.monotonic() - started)
        except BudgetStopped as exc:
            result = self.state(result, TaskState.CANCELLED if exc.reason == 'CANCELLED' else TaskState.EXHAUSTED, export_state='failed', stop_reason=exc.reason, duration=time.monotonic() - started)
        except Exception as exc:
            result = self.state(result, TaskState.FAILED, export_state='failed', stop_reason=result.stop_reason or 'EXPORT_FAILED', duration=time.monotonic() - started, uncertainties=(*result.uncertainties, self.feedback(str(exc))))
        return result

    async def close_explorer(self):
        """Release the task's strategy; the result the run decided is never rewritten here.

        A session that cannot be closed is recorded as a cleanup failure rather than
        raised: the run has already reached its conclusion, and an exception from this
        point would replace that conclusion with an unrelated one.
        """
        explorer = self.explorer
        if explorer is None or self._explorer_closed:
            return
        self._explorer_closed = True
        try:
            await explorer.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.store.append_event('explorer.cleanup_failed', (), {'result_code':'CLEANUP_FAILED'})
