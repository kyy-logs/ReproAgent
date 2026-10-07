import argparse
import asyncio
import json
import signal
import sys
from dataclasses import replace
from pathlib import Path

from .app import create_controller
from .core.budget import Budget
from .core.models import FixValidationRequest, ModelConfig, RunContext, TaskState
from .core.serialization import decode_record, parse_json
from .paths import display_path
from .store import TaskStore

EXIT_CODES = {TaskState.DONE:0, TaskState.BLOCKED:1, TaskState.NEEDS_INFORMATION:1, TaskState.EXHAUSTED:1, TaskState.FAILED:3, TaskState.CANCELLED:130}


def load_request(path):
    path = Path(path).resolve()
    request = decode_record('request', parse_json(path.read_text(encoding='utf-8')))
    def resolved(value): return (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
    return replace(request, repo=resolved(request.repo), output_dir=resolved(request.output_dir), issue_file=resolved(request.issue_file))


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
    result = asyncio.run(run())
    print(json.dumps({'task_id':result.task_id, 'status':result.status.value, 'evidence_level':result.evidence_level.value,
        'export_state':result.export_state, 'output_dir':display_path(output)}, ensure_ascii=False))
    return EXIT_CODES[result.status]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='reproagent')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run'); run.add_argument('--config', required=True); run.add_argument('--model-config', required=True)
    run.add_argument('--fixed-repo'); run.add_argument('--fixed-python')
    run.add_argument('--model-backend', choices=('native', 'agentscope'), default='native')
    run.add_argument('--agent-backend', choices=('native', 'agentscope'), default='native')
    inspect = sub.add_parser('inspect'); inspect.add_argument('task_dir')
    try:
        args = parser.parse_args(argv)
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
