import json
import os
import sys
import uuid
from pathlib import Path

from reproagent.core.models import EnvironmentSnapshot, ExecutionSpec, FrameworkChecks, LanguageInspection
from reproagent.store import safe_child
from .collector import read_probe

ALLOWED_ARGS = {'-q', '-v', '-vv', '-s', '--disable-warnings', '--tb=short', '--tb=long', '--tb=no'}


class PythonPytestAdapter:
    def inspect(self, project, config):
        python = str(Path(config.python or sys.executable).resolve())
        if not Path(python).is_file():
            raise ValueError('configured Python interpreter does not exist')
        if any(arg not in ALLOWED_ARGS for arg in config.pytest_args):
            raise ValueError('unsupported pytest argument; probe disabling, xdist and collection-only are forbidden')
        for path in config.source_roots:
            if path != '.':
                safe_child(project.snapshot.root, path)
        safe_child(project.snapshot.root, config.candidate_parent)
        probe = ExecutionSpec('inspect-' + uuid.uuid4().hex, (python, '-c', 'import pytest; print(pytest.__version__)'), project.snapshot.root)
        return LanguageInspection(config, (probe,))

    def describe_environment(self, inspection, probes):
        raw = probes.results[0]
        if raw.exit_code != 0 or not raw.cleanup_ok or raw.stop_reason != 'EXITED':
            raise ValueError('environment probe failed: Python/pytest unavailable or command interrupted')
        config = inspection.config
        roots = config.source_roots or (('.',))
        return EnvironmentSnapshot('env-' + uuid.uuid4().hex, inspection.probes[0].argv[0],
            Path(raw.stdout_ref.path).read_text(encoding='utf-8').strip(), roots, config.target_modules,
            config.candidate_parent, config.pytest_args, limitations=('file-copy isolation is not a security sandbox', 'xdist unsupported'))

    def build_execution(self, candidate, run, environment):
        if candidate.run_options:
            raise ValueError('agent execution overrides are unsupported')
        parent = environment.candidate_parent.rstrip('/') + '/'
        if any(not entry.path.startswith(parent) for entry in candidate.files if entry.role == 'test'):
            raise ValueError('candidate tests must inherit configured conftest location')
        probe = Path(__file__).parent / 'probe'
        roots = [str(run.root if root == '.' else safe_child(run.root, root)) for root in environment.source_roots]
        path = run.root.parent / 'probe.jsonl'
        return ExecutionSpec('spec-' + uuid.uuid4().hex,
            (environment.python, '-m', 'pytest', *environment.pytest_args, '-p', 'reproagent_pytest_probe', *candidate.selectors), run.root,
            run.run_id, run.snapshot_id, candidate.candidate_id, candidate.manifest_hash, candidate.contract_id, candidate.contract_version,
            env_overrides={'PYTHONPATH': os.pathsep.join([str(probe), *roots]), 'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                'REPROAGENT_RUN_ID': run.run_id, 'REPROAGENT_PROBE_PATH': str(path), 'REPROAGENT_TARGET_MODULES': json.dumps(environment.target_modules),
                'TMP': str(run.temp_root), 'TEMP': str(run.temp_root), 'PYTHONDONTWRITEBYTECODE': '1'},
            probe_path=path, target_modules=environment.target_modules, source_roots=environment.source_roots)

    def normalize(self, raw, artifacts):
        return read_probe(artifacts.path, artifacts.path.parent.name)

    def check_framework(self, candidate, observation):
        invalid, blocked, suspicious = [], [], []
        if not observation.probe_complete:
            invalid.append('incomplete probe')
        if observation.framework_details.get('exitstatus_matches_process') is False:
            invalid.append('Probe/process exit status mismatch')
        if observation.framework_details.get('source_binding_ok') is False:
            invalid.append('target source origin is outside run snapshot')
        if not observation.collected:
            invalid.append('no collected tests')
        for phase in observation.tests:
            if phase.get('wasxfail') or phase.get('outcome') == 'skipped':
                invalid.append('skip/xfail cannot establish reproduction')
            text = phase.get('longrepr', '')
            exception_type = phase.get('exception_type', '')
            syntax_error = exception_type in ('builtins.SyntaxError', 'builtins.IndentationError', 'builtins.TabError') or (not exception_type and 'SyntaxError' in text)
            frames = phase.get('exception_frames', [])
            origin = frames[-1] if frames else {}
            # A valid test can trigger compile() in real target code. Require the
            # innermost raising frame itself to bind to an observed target source;
            # merely calling the target before a candidate error is insufficient.
            target_syntax = (phase.get('when') == 'call' and origin.get('path') == observation.target_origins.get(origin.get('module'))
                             and bool(origin.get('path')))
            if 'fixture ' in text and 'not found' in text or syntax_error and not target_syntax:
                invalid.append('candidate syntax or fixture error')
            elif 'ModuleNotFoundError' in text:
                blocked.append('dependency/import unavailable; semantic attribution required')
        for item in observation.framework_details.get('collection_failures', []):
            if 'SyntaxError' in item.get('longrepr', ''):
                invalid.append('collection syntax error')
            elif 'ModuleNotFoundError' in item.get('longrepr', ''):
                blocked.append('collection dependency/import unavailable; semantic attribution required')
        return FrameworkChecks(tuple(blocked), tuple(invalid), tuple(suspicious))
