import asyncio
import json
import time
import uuid
from dataclasses import asdict, replace

from .agent import ActionProtocolError, ReproAgent
from .budget import BudgetStopped
from .models import (AgentContext, CandidateClass, CandidateDraft, DraftFile, EvidenceContext, EvidenceLevel, EvidenceRef,
                     IssueDescription, ProjectView, SourceRef, TaskResult, TaskState)
from .serialization import bytes_hash, canonical_hash
from .tools import Tools
from .protocol import ModelOutputError, ModelProtocolError
from .workflow import WorkflowState, choose_workflow
from reproagent.paths import relative_name
from reproagent.store import atomic_write

# Every action ends in one of these codes; none of them carries model or provider text.
# Each names a distinct failure class, so an interruption is never confused with a
# rejected argument, a model failure or an internal error.
ACTION_RESULT_CODES = frozenset({'OK', 'INVALID_ARGUMENT', 'MISSING_FILE', 'INVALID_ENCODING', 'UNKNOWN_CANDIDATE',
    'CANDIDATE_NOT_REPRODUCED', 'CONTRACT_INCOMPLETE', 'UNAUTHORIZED_EVIDENCE_REF', 'REVISION_BUDGET_EXCEEDED',
    'REPLAY_UNCONFIRMED', 'INTERRUPTED', 'CLEANUP_FAILED', 'MODEL_OUTPUT_ERROR', 'MODEL_PROTOCOL_ERROR', 'INTERNAL_ERROR'})


class CleanupFailed(Exception):
    def __init__(self, reason): self.reason = reason


class ActionRejected(ValueError):
    """A validated action failed its own preconditions; the event records only the code."""
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


class Controller:
    def __init__(self, store, workspace, runner, gateway, verifier, exporter, secrets=(), explorer_factory=ReproAgent, backend_info=None):
        self.store, self.workspace, self.runner = store, workspace, runner
        self.gateway, self.verifier, self.exporter = gateway, verifier, exporter
        self.secrets = tuple(secret for secret in secrets if secret)
        self.explorer_factory = explorer_factory
        self.backend_info = backend_info or {'model_backend':'native', 'agent_backend':'native'}

    def state(self, result, status, **changes):
        result = replace(result, status=status, **changes)
        self.store.save_record('task', result.task_id, result)
        self.store.append_event('task.state', (), {'status':status.value, 'evidence_level':result.evidence_level.value})
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

    def action_start(self, action, context, contract):
        """Record the action under a bounded name, a hash and the remaining budget only."""
        selected = {'action':action.name, 'parameters_hash':canonical_hash(action.parameters),
                    'steps_remaining':self.steps_remaining(context), 'contract_version':contract.version}
        self.store.append_event('action.selected', (), selected)
        return selected

    def action_end(self, selected, started, code):
        if code not in ACTION_RESULT_CODES: raise ValueError(f'unknown action result code: {code}')
        self.store.append_event('action.completed' if code == 'OK' else 'action.rejected', (),
            {**selected, 'result_code':code, 'duration':time.monotonic() - started})

    def workflow_signature(self, contract, project, executions, verdicts):
        """What a repeat would have to repeat: contract version, candidates, executions."""
        runs = tuple(sorted((candidate_id, str(getattr(verdicts.get(candidate_id), 'classification', None)))
                            for candidate_id in executions))
        return (contract.version, tuple(candidate.candidate_id for candidate in project.candidates), runs)

    def workflow_state(self, context, contract, project, executions, verdicts, signature, repeated):
        candidate = project.candidates[-1] if project.candidates else None
        verdict = verdicts.get(candidate.candidate_id) if candidate else None
        return WorkflowState(steps_remaining=self.steps_remaining(context),
            grounded=bool(contract.expected) and not contract.missing_information,
            candidate_id=candidate.candidate_id if candidate else '',
            candidate_contract_version=candidate.contract_version if candidate else 0,
            current_contract_version=contract.version,
            has_execution=bool(candidate) and candidate.candidate_id in executions,
            reproduced=verdict is not None and verdict.classification == CandidateClass.REPRODUCED,
            # A repeat only counts inside the state that produced it: once the contract
            # or the candidate set changes, the same arguments are legal again.
            repeated_action=repeated[1] if repeated and repeated[0] == signature else None)

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
            source = SourceRef('input/issue.md', bytes_hash(issue_bytes), 1, max(1, len(issue_bytes.splitlines())))
            agent = self.explorer_factory(self.gateway, context, request.language.candidate_parent)
            contract = await agent.analyze(IssueDescription(issue_bytes.decode('utf-8'), bytes_hash(issue_bytes)), EvidenceContext((source,), (issue_bytes.decode('utf-8'),)))
            for evidence in contract.sources:
                self.verifier.resolve(evidence)
            self.store.save_contract(contract)
            tools = Tools(ProjectView(snapshot), self.workspace, context)
            history, feedback, executions, verdicts = [], self.feedback({'missing_information':contract.missing_information}) if contract.missing_information else '', {}, {}
            # The last (action, result) pair and the state it happened in, so an
            # action that keeps returning the same result is answered instead of
            # repeated until the budget runs out.
            repeated, previous = None, None
            seen_sources = set()
            # The snapshot root can be long while the task root is not, so the
            # stored reference path is taken in one representation, not by string.
            original_paths = {relative_name(snapshot.root / entry.path, self.store.root):entry.content_hash
                              for entry in snapshot.files}
            def remember_original(refs):
                seen_sources.update(ref for ref in refs if original_paths.get(ref.path) == ref.content_hash)
            while True:
                context.budget.check()
                if context.cancel_event.is_set(): raise BudgetStopped('CANCELLED')
                result = self.state(result, TaskState.GENERATING)
                signature = self.workflow_signature(contract, tools.project, executions, verdicts)
                decision = choose_workflow(self.workflow_state(context, contract, tools.project, executions, verdicts, signature, repeated))
                try:
                    if decision.forced_action is not None:
                        # The lifecycle acts on its own: one existing step is spent,
                        # no model decision is bought, and the existing checks in
                        # run_candidate/submit_candidate still decide the outcome.
                        context.budget.take_step()
                        action = decision.forced_action
                    else:
                        action = await agent.next_action(AgentContext(contract, tools.project, tuple(history[-4:]), feedback,
                            decision.allowed_actions, decision.blocked_actions))
                except ActionProtocolError as exc:
                    self.store.append_event('protocol.error', (), {'response_kind':'action', 'result_code':exc.code,
                        'steps_remaining':self.steps_remaining(context)})
                    feedback = self.feedback({'error':str(exc)})
                except (ValueError, FileNotFoundError, UnicodeError) as exc:
                    # No action was selected, so this is a protocol failure rather than a rejection.
                    self.store.append_event('protocol.error', (), {'response_kind':'action', 'result_code':'INVALID_ACTION_RESPONSE',
                        'steps_remaining':self.steps_remaining(context)})
                    feedback = self.feedback({'error':str(exc)})
                else:
                    parameters = action.parameters
                    selected = self.action_start(action, context, contract)
                    started_action = time.monotonic()
                    code = 'OK'
                    try:
                        if action.name == 'search_code':
                            tool_result = tools.search_code(**parameters)
                            remember_original(tool_result.evidence_refs)
                            feedback = self.feedback(asdict(tool_result))
                        elif action.name == 'read_file':
                            tool_result = tools.read_file(**parameters)
                            remember_original(tool_result.evidence_refs)
                            feedback = self.feedback(asdict(tool_result))
                        elif action.name == 'write_candidate':
                            if contract.missing_information or not contract.expected:
                                raise ActionRejected('CONTRACT_INCOMPLETE', 'read expectation sources and revise contract before generating a candidate')
                            draft = CandidateDraft(tuple(DraftFile(f['path'], f['content'].encode('utf-8'), f['role']) for f in parameters['files']),
                                snapshot.snapshot_id, contract.contract_id, contract.version, parameters['hypothesis'], expectation_sources=contract.sources)
                            feedback = self.feedback(asdict(tools.write_candidate(draft)))
                        elif action.name == 'revise_contract':
                            refs = tuple(EvidenceRef(**ref) for ref in parameters['source_refs'])
                            if any(ref not in seen_sources for ref in refs):
                                raise ActionRejected('UNAUTHORIZED_EVIDENCE_REF', 'revision may cite only original project evidence returned by tools')
                            sources = [source, *(s for s in contract.sources if s != source)]
                            sources.extend(SourceRef(ref.path, ref.content_hash, ref.start_line, ref.end_line, 'repository') for ref in refs)
                            texts = tuple('\n'.join(self.verifier.resolve(s).splitlines()[s.start_line - 1:s.end_line]) for s in sources)
                            if sum(len(t.encode()) for t in texts) > context.budget.limits.tool_response_bytes:
                                raise ActionRejected('REVISION_BUDGET_EXCEEDED', 'revision sources exceed context budget; narrow line ranges')
                            revised = await agent.analyze(IssueDescription(issue_bytes.decode('utf-8'), bytes_hash(issue_bytes)), EvidenceContext(tuple(sources), texts, contract))
                            revised = replace(revised, revision_reason=parameters['reason'])
                            for ref in revised.sources: self.verifier.resolve(ref)
                            self.store.save_contract(revised)
                            contract = revised
                            executions.clear(); verdicts.clear(); history.clear()
                            result = self.state(result, TaskState.GENERATING, evidence_level=EvidenceLevel.NONE, accepted_candidate_id='')
                            feedback = self.feedback({'contract_version':contract.version, 'missing_information':contract.missing_information})
                        elif action.name == 'request_information':
                            result = self.state(result, TaskState.NEEDS_INFORMATION, stop_reason='MISSING_INFORMATION', uncertainties=(parameters['question'],))
                            break
                        else:
                            candidate = next((c for c in tools.project.candidates if c.candidate_id == parameters['candidate_id']), None)
                            if candidate is None: raise ActionRejected('UNKNOWN_CANDIDATE', 'unknown candidate_id')
                            if action.name == 'run_candidate':
                                result = self.state(result, TaskState.EXECUTING)
                                run = await self.runner.execute(candidate, snapshot, environment, context)
                                self.check_execution(run)
                                result = self.state(result, TaskState.VERIFYING)
                                verdict = await self.verifier.evaluate(contract, candidate, (run,), context)
                                self.store.save_record('verdicts', 'verdict-' + run.run_id, verdict)
                                executions[candidate.candidate_id], verdicts[candidate.candidate_id] = run, verdict
                                feedback = self.feedback(asdict(verdict))
                                if verdict.classification == CandidateClass.ENVIRONMENT_BLOCKED:
                                    result = self.state(result, TaskState.BLOCKED, stop_reason='ENVIRONMENT_BLOCKED', uncertainties=(verdict.reason,))
                                    break
                                if verdict.classification == CandidateClass.REPRODUCED:
                                    result = self.state(result, TaskState.VERIFYING, evidence_level=EvidenceLevel.SINGLE_OBSERVATION, accepted_candidate_id=candidate.candidate_id)
                            elif action.name == 'submit_candidate':
                                first, first_verdict = executions.get(candidate.candidate_id), verdicts.get(candidate.candidate_id)
                                if not first or first_verdict.classification != CandidateClass.REPRODUCED:
                                    raise ActionRejected('CANDIDATE_NOT_REPRODUCED', 'candidate has no verified original failure')
                                result = self.state(result, TaskState.REPLAYING)
                                second = await self.runner.execute(candidate, snapshot, environment, context)
                                self.check_execution(second)
                                second_verdict = await self.verifier.evaluate(contract, candidate, (second,), context)
                                self.store.save_record('verdicts', 'verdict-' + second.run_id, second_verdict)
                                if not self.verifier.confirm(contract, candidate, first, second, (first_verdict, second_verdict)):
                                    feedback = 'Independent replay did not confirm the same failure; revise or request information.'
                                    code = 'REPLAY_UNCONFIRMED'
                                    continue
                                result = self.state(result, TaskState.REPLAYING, evidence_level=EvidenceLevel.REPEATED_OBSERVATION, accepted_candidate_id=candidate.candidate_id)
                                if fixed:
                                    # No repair paths/source are ever placed in AgentContext or Tools.
                                    fixed_request = replace(request, repo=fixed.repo, language=replace(request.language, python=fixed.python or request.language.python))
                                    try:
                                        fixed_snapshot = self.workspace.freeze(fixed_request, context)
                                        fixed_environment = await self.runner.prepare(fixed_request, fixed_snapshot, context)
                                        fixed_run = await self.runner.execute(candidate, fixed_snapshot, fixed_environment, context, execution_role='fixed')
                                        self.check_execution(fixed_run)
                                        checks = self.runner.adapter.check_framework(candidate, fixed_run.observation)
                                        same_nodes = set(fixed_run.observation.framework_details.get('completed_nodeids', [])) == set(first.observation.framework_details.get('completed_nodeids', []))
                                        passed = same_nodes and fixed_run.raw.exit_code == 0 and fixed_run.raw.stop_reason == 'EXITED' and fixed_run.protection.ok and fixed_run.observation.executed and fixed_run.observation.probe_complete and not checks.invalid and not checks.blocked
                                        if passed:
                                            result = self.state(result, TaskState.REPLAYING, evidence_level=EvidenceLevel.DIFFERENTIAL_VALIDATED, fix_validation_status='passed')
                                        else:
                                            # The fixed version ran the same candidate and did not pass:
                                            # only the original repeated failure is confirmed.
                                            result = replace(result, fix_validation_status='failed',
                                                uncertainties=(*result.uncertainties, 'Fixed-version validation failed or was incompatible.'))
                                    except (ValueError, OSError):
                                        # No fixed-version verdict exists, so no differential claim is made.
                                        result = replace(result, fix_validation_status='blocked',
                                            uncertainties=(*result.uncertainties, 'Fixed-version environment/fixtures unavailable.'))
                                context.budget.check()
                                result = self.state(result, TaskState.EXPORTING)
                                break
                    except (BudgetStopped, asyncio.CancelledError, TimeoutError):
                        # Only budget, cancellation and timeout interrupt an action.
                        code = 'INTERRUPTED'
                        raise
                    except ModelOutputError as exc:
                        code = 'MODEL_OUTPUT_ERROR'
                        feedback = self.feedback({'error':str(exc)})
                    except ModelProtocolError:
                        code = 'MODEL_PROTOCOL_ERROR'
                        raise
                    except CleanupFailed:
                        code = 'CLEANUP_FAILED'
                        raise
                    except (ValueError, FileNotFoundError, UnicodeError) as exc:
                        code = rejection_code(exc)
                        feedback = self.feedback({'error':str(exc)})
                    except BaseException:
                        code = 'INTERNAL_ERROR'
                        raise
                    else:
                        history.append({'action':action.name, 'feedback':feedback.encode()[:2048].decode(errors='ignore')})
                    finally:
                        # Every selected action gets exactly one terminal event, including
                        # the branches that break, continue or interrupt the loop.
                        self.action_end(selected, started_action, code)
                        key = (*signature, action.name, canonical_hash(action.parameters))
                        if (key, code) == previous:
                            repeated = (signature, action)
                        previous = (key, code)
        except CleanupFailed as exc:
            result = self.state(result, TaskState.FAILED, stop_reason=exc.reason, uncertainties=(*result.uncertainties, 'Process cleanup failed; directories retained.'))
        except BudgetStopped as exc:
            terminal = TaskState.CANCELLED if exc.reason == 'CANCELLED' else TaskState.NEEDS_INFORMATION if exc.reason == 'NEEDS_INFORMATION' else TaskState.EXHAUSTED
            result = self.state(result, terminal, stop_reason=exc.reason, uncertainties=(*result.uncertainties, str(exc)))
        except asyncio.CancelledError:
            context.cancel_event.set(); context.budget.cancel()
            result = self.state(result, TaskState.CANCELLED, stop_reason='CANCELLED')
        except ModelProtocolError as exc:
            result = self.state(result, TaskState.FAILED, stop_reason='MODEL_PROTOCOL_ERROR', uncertainties=(self.feedback(str(exc)),))
        except (ValueError, OSError) as exc:
            status = TaskState.BLOCKED if result.status == TaskState.PREPARING else TaskState.NEEDS_INFORMATION
            result = self.state(result, status, stop_reason='ENVIRONMENT_BLOCKED' if status == TaskState.BLOCKED else 'INVALID_INPUT', uncertainties=(self.feedback(str(exc)),))
        except Exception as exc:
            result = self.state(result, TaskState.FAILED, stop_reason='INTERNAL_ERROR', uncertainties=(self.feedback(str(exc)),))
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
