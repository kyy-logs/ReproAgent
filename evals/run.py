from dataclasses import asdict
from pathlib import Path

from reproagent.app import create_controller
from reproagent.core.budget import Budget
from reproagent.core.models import BudgetLimits, FixValidationRequest, PythonPytestConfig, RunContext, TaskRequest
from reproagent.core.serialization import bytes_hash
from reproagent.store import atomic_write
from .schema import EvalResult


def generation_input(case):
    return {'case_id':case.case_id, 'repo':str(case.buggy_repo), 'version':case.buggy_version,
        'description':case.allowed_description, 'issue_url':case.issue_url, 'issue_hash':case.issue_hash,
        'python':case.buggy_python, 'target_modules':list(case.target_modules), 'source_roots':list(case.source_roots)}


async def run_case(case, model, output_dir, *, limits=None, model_backend='native', agent_backend='native',cancel_event=None):
    if case.review_status != 'approved':
        raise ValueError('historical case must be manually reviewed before evaluation')
    output_dir = Path(output_dir)
    issue = output_dir / 'evaluation-input/issue.md'
    atomic_write(issue, case.allowed_description.encode())
    limits = limits or BudgetLimits()
    request = TaskRequest(case.buggy_repo, output_dir / 'task', issue, language=PythonPytestConfig(python=case.buggy_python,
        target_modules=case.target_modules, source_roots=case.source_roots, candidate_parent=case.candidate_parent,
        pytest_args=case.pytest_args,baseline_tests=case.baseline_tests), limits=limits,task_id=case.case_id)
    controller = create_controller(request, model, model_backend=model_backend, agent_backend=agent_backend)
    if case.buggy_source_hash or case.fixed_source_hash:
        from reproagent.core.serialization import canonical_hash
        freeze=controller.workspace.freeze
        def checked_freeze(freeze_request,freeze_context):
            snapshot=freeze(freeze_request,freeze_context)
            repo=freeze_request.repo.resolve()
            expected=case.buggy_source_hash if repo==case.buggy_repo.resolve() else case.fixed_source_hash if repo==case.fixed_repo.resolve() else ''
            if not expected or canonical_hash({entry.path:entry.content_hash for entry in snapshot.files})!=expected:
                raise ValueError('evaluation source differs from reviewed version')
            return snapshot
        controller.workspace.freeze=checked_freeze
    context=RunContext(Budget(limits))
    if cancel_event is not None:
        from dataclasses import replace
        context=replace(context,cancel_event=cancel_event)
    result = await controller.run(request, context, FixValidationRequest(case.fixed_repo, case.fixed_python))
    events, _ = controller.store.read_events()
    attempts = [e.payload for e in events if e.kind == 'model.attempt']
    calls = attempts or [e.payload for e in events if e.kind == 'model.completed']
    costs = [call.get('cost_value') for call in calls]
    known = bool(costs) and all(value is not None for value in costs)
    environments = list((request.output_dir / 'environments').glob('*.json'))
    env = asdict(controller.store.load_record('environments', environments[0].stem)) if environments else {}
    return EvalResult(case.case_id, result.status.value, result.evidence_level.value, 'runnable' if environments and result.status.value!='BLOCKED' else 'blocked',
        reproduced=result.status.value == 'DONE', differential_validated=result.evidence_level.value == 'DIFFERENTIAL_VALIDATED',
        duration=result.duration, cost_kind='estimated' if known else 'unknown', cost_value=sum(costs) if known else None,
        model=model.model, model_base_url=model.base_url, budget=asdict(limits), environment=env,
        description_hash=bytes_hash(case.allowed_description.encode()), buggy_version=case.buggy_version, fixed_version=case.fixed_version,
        model_backend=model_backend,agent_backend=agent_backend,stop_reason=result.stop_reason,http_attempts=len(attempts),
        usage={key:sum(call.get('usage',{}).get(key,0) for call in calls) for key in ('prompt_tokens','completion_tokens','total_tokens')},
        known_cost_subtotal=sum(value for value in costs if value is not None),unknown_cost_attempts=sum(value is None for value in costs),
        fix_validation_status=result.fix_validation_status)


def summarize(results):
    all_tasks = len(results)
    runnable = sum(r.environment_status == 'runnable' for r in results)
    effective = sum(r.reproduced and r.human_judgement is True and r.export_replayed is True for r in results)
    claimed = sum(r.reproduced for r in results)
    known = [r.cost_value for r in results if r.cost_kind != 'unknown' and r.cost_value is not None]
    # A DONE task whose fixed version also failed confirms only the original repeated
    # failure: it is counted here as a failed check, never as a differential success.
    return {'all_tasks':all_tasks, 'runnable_tasks':runnable, 'effective_reproductions':effective,
        'differential_successes':sum(r.fix_validation_status == 'passed' for r in results),
        'fix_validation_failed':sum(r.fix_validation_status == 'failed' for r in results),
        'total_rate':effective / all_tasks if all_tasks else None, 'runnable_rate':effective / runnable if runnable else None,
        'false_positives':sum(r.reproduced and r.human_judgement is False for r in results),
        'export_replay_rate':sum(r.reproduced and r.export_replayed is True for r in results) / claimed if claimed else None,
        'costs_known_count':len(known), 'costs_unknown_count':all_tasks - len(known), 'cost_samples':known,
        'time_samples':[r.duration for r in results if r.duration is not None], 'human_supplements':sum(r.human_supplements for r in results),
        'pending_human_review':sum(r.human_judgement is None for r in results), 'pending_export_replay':sum(r.reproduced and r.export_replayed is None for r in results)}
