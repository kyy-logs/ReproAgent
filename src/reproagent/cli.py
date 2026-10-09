import argparse
import asyncio
import json
import signal
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .app import LEGACY_BACKENDS, create_controller
from .core.budget import Budget
from .core.models import FixValidationRequest, ModelConfig, RunContext, TaskState
from .core.serialization import decode_record, parse_json
from .observability import trace_session, write_trace, write_trace_html
from .paths import display_path
from .store import TaskStore

EXIT_CODES = {TaskState.DONE:0, TaskState.BLOCKED:1, TaskState.NEEDS_INFORMATION:1, TaskState.EXHAUSTED:1, TaskState.FAILED:3, TaskState.CANCELLED:130}
# A run makes one infrastructure choice, so the old flag is answered once, in the user's
# own words, instead of leaving a silent DeprecationWarning to stand for it.
BACKEND_DEPRECATION = ('--model-backend/--agent-backend are deprecated and no longer select a runtime: '
                       'ReproAgent runs on the AgentScope infrastructure only.')
#: A trace is a local diagnostic. If it cannot be written that is worth saying once, and
#: worth saying without touching the task's own JSON, its package or its exit code.
TRACE_WRITE_WARNING = 'reproagent: could not write the activity trace; the task result is unchanged.'


def _write_trace_or_warn(task_dir, document) -> bool:
    try:
        write_trace(task_dir, document)
    except (OSError, ValueError, TypeError):
        # TypeError is in the list because ``write_trace`` is public: a document it
        # cannot encode is a failed observation, not a failed task.
        print(TRACE_WRITE_WARNING, file=sys.stderr)
        return False
    return True


def load_request(path):
    path = Path(path).resolve()
    request = decode_record('request', parse_json(path.read_text(encoding='utf-8')))
    def resolved(value): return (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
    return replace(request, repo=resolved(request.repo), output_dir=resolved(request.output_dir), issue_file=resolved(request.issue_file),
        experience_file=resolved(request.experience_file) if request.experience_file is not None else None)


def run_command(args):
    request = load_request(args.config)
    model = ModelConfig(**parse_json(Path(args.model_config).read_text(encoding='utf-8')))
    if not request.repo.is_dir() or not request.issue_file.is_file():
        raise ValueError('repo/issue_file does not exist')
    output = request.output_dir.resolve()
    if request.repo.resolve().is_relative_to(output) or output == request.repo.resolve():
        raise ValueError('output_dir cannot contain repo')
    if (output / 'request.json').exists():
        raise ValueError('choose a fresh output_dir')
    if args.fixed_python and not args.fixed_repo:
        raise ValueError('--fixed-python requires --fixed-repo')
    if args.model_backend is not None or args.agent_backend is not None:
        print('reproagent: ' + BACKEND_DEPRECATION, file=sys.stderr)
    controller = create_controller(request, model, model_backend=args.model_backend, agent_backend=args.agent_backend)
    from .core.models import ProjectView, CodeSnapshot
    controller.runner.adapter.inspect(ProjectView(CodeSnapshot('preflight', request.repo, '', ())), request.language)
    import os
    if not os.environ.get(model.api_key_env):
        raise ValueError('missing API key environment variable: ' + model.api_key_env)
    fixed = FixValidationRequest(Path(args.fixed_repo).resolve(), args.fixed_python or '') if args.fixed_repo else None
    context = RunContext(Budget(request.limits))
    async def run():
        return await controller.run(request, context, fixed)
    # The scope is opened around the whole run, so it covers what the task itself does
    # after the phases -- sealing, export -- and the learning step that follows them.
    trace_document = None
    with trace_session(enabled=args.trace, secrets=(os.environ.get(model.api_key_env, ''),)) as recorder:
        result = asyncio.run(run())
        if recorder is not None:
            learning = asdict(controller.learning_result) if getattr(controller, 'learning_result', None) is not None else None
            trace_document = recorder.finish(task_id=result.task_id, status=result.status.value,
                                             main_duration=result.duration, learning=learning)
    summary = {'task_id':result.task_id, 'status':result.status.value, 'evidence_level':result.evidence_level.value,
        'export_state':result.export_state, 'output_dir':display_path(output)}
    if getattr(controller, 'learning_result', None) is not None:
        summary['learning'] = asdict(controller.learning_result)
        if not controller.learning_result.event_recorded:
            print('Learning summary could not be saved; the sealed task result is unchanged.', file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False))
    if trace_document is not None:
        _write_trace_or_warn(output, trace_document)
    return EXIT_CODES[result.status]


def trace_command(args):
    """Render the trace a task already stored, or say why there is none to show."""
    root = Path(args.task_dir).resolve()
    if not root.is_dir():
        raise ValueError('task directory does not exist')
    target = write_trace_html(root)
    print(json.dumps({'trace': display_path(target)}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='reproagent')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run'); run.add_argument('--config', required=True); run.add_argument('--model-config', required=True)
    run.add_argument('--fixed-repo'); run.add_argument('--fixed-python')
    run.add_argument('--model-backend', choices=LEGACY_BACKENDS,
                     help='deprecated: both legacy values select the one AgentScope infrastructure')
    run.add_argument('--agent-backend', choices=LEGACY_BACKENDS,
                     help='deprecated: both legacy values select the one AgentScope infrastructure')
    run.add_argument('--trace', action='store_true',
                     help='write a local activity trace to the task output directory')
    inspect = sub.add_parser('inspect'); inspect.add_argument('task_dir')
    # The trace viewer takes the task directory and nothing else: the two files it may
    # touch are fixed, so it cannot be pointed at the sealed package or anywhere else.
    trace = sub.add_parser('trace'); trace.add_argument('task_dir')
    try:
        args = parser.parse_args(argv)
        if args.command == 'trace':
            return trace_command(args)
        if args.command == 'inspect':
            root = Path(args.task_dir).resolve()
            if not root.is_dir(): raise ValueError('task directory does not exist')
            print(json.dumps(TaskStore(root).inspect(), ensure_ascii=False))
            return 0
        return run_command(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, TypeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
